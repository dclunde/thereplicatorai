import glob
import hashlib
import json
import logging
import os
import subprocess
import threading
import time
import uuid
from urllib.parse import quote

import paho.mqtt.client as mqtt

from src.core.config import CACHE_DIR, SNAPSHOTS_DIR

logger = logging.getLogger(__name__)

try:
    from bambulab import BambuAuthenticator, BambuClient as BaseBambuSdkClient
except ImportError:
    BambuAuthenticator = None
    BaseBambuSdkClient = None

LOCK_FILE = os.path.join(CACHE_DIR, "printer_dispatch.lock")
DISPATCH_LOCK_FILE = LOCK_FILE  # Backward-compatible alias

# gcode_state values that mean the printer is actively working on a job.
BUSY_STATES = frozenset({"RUNNING", "PREPARE", "PAUSE", "PAUSED", "SLICING"})

# A lock file that exists but cannot be parsed (e.g. a writer crashed between
# creating and writing it) is treated as stale after this many seconds.
_UNREADABLE_LOCK_GRACE_SECONDS = 5.0


class PrinterBusyError(Exception):
    """Raised when an operation cannot proceed because the printer is actively printing or locked."""

    pass


def _is_pid_running(pid: int) -> bool:
    """Checks if a given process ID is actively running."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            process = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if process != 0:
                kernel32.CloseHandle(process)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


def _read_lock(lock_file: str) -> dict | None:
    """Returns the parsed lock payload, or None if missing/unreadable."""
    try:
        with open(lock_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _lock_is_stale(lock_file: str, timeout_seconds: float) -> bool:
    """A lock is stale if it expired, its owner process died, or it is corrupt and old."""
    now = time.time()
    data = _read_lock(lock_file)
    if data is None:
        try:
            age = now - os.path.getmtime(lock_file)
        except OSError:
            return True  # Vanished between checks
        return age > _UNREADABLE_LOCK_GRACE_SECONDS
    try:
        lock_ts = float(data.get("timestamp", 0) or 0)
        lock_pid = int(data.get("pid", 0) or 0)
    except (TypeError, ValueError):
        return True
    return (now - lock_ts) >= timeout_seconds or not _is_pid_running(lock_pid)


def acquire_dispatch_lock(timeout_seconds: float = 90.0) -> bool:
    """Atomically acquires the cross-process print dispatch lock.

    Uses O_CREAT | O_EXCL so exactly one process can create the lock file. Stale
    locks (expired, dead owner, or corrupt) are removed and acquisition is retried once.
    """
    lock_file = LOCK_FILE
    lock_dir = os.path.dirname(lock_file)
    if lock_dir:
        os.makedirs(lock_dir, exist_ok=True)

    for _ in range(2):
        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if not _lock_is_stale(lock_file, timeout_seconds):
                return False
            try:
                os.remove(lock_file)
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.warning(f"Could not remove stale dispatch lock: {e}")
                return False
            continue
        except OSError as e:
            logger.warning(f"Failed to acquire dispatch lock: {e}")
            return False

        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "timestamp": time.time()}, f)
        return True
    return False


def release_dispatch_lock() -> None:
    """Releases the dispatch lock ONLY if it is owned by the current process."""
    lock_file = LOCK_FILE
    data = _read_lock(lock_file)
    if data is not None and data.get("pid") == os.getpid():
        try:
            os.remove(lock_file)
        except OSError:
            pass


def is_dispatch_locked(timeout_seconds: float = 90.0) -> bool:
    """Returns True if an active (non-stale) print dispatch lock exists."""
    lock_file = LOCK_FILE
    if not os.path.exists(lock_file):
        return False
    return not _lock_is_stale(lock_file, timeout_seconds)


def cleanup_old_snapshots(max_age_seconds: float = 300.0) -> None:
    """Removes snapshot image files in outputs/snapshots/ older than max_age_seconds (5 min)."""
    if not os.path.exists(SNAPSHOTS_DIR):
        return
    now = time.time()
    for f in glob.glob(os.path.join(SNAPSHOTS_DIR, "snapshot_*.jpg")):
        try:
            if now - os.path.getmtime(f) > max_age_seconds:
                os.remove(f)
        except Exception:
            pass


class BambuClient:
    def __init__(self, username=None, password=None, device_serial=None, ip=None, access_code=None):
        self.ip = ip
        self.access_code = access_code
        self.username = username
        self.password = password
        self.device_serial = device_serial
        self.auth_token = None
        self.mqtt_client = None
        self.ams_colors = {}
        self.ams_data_received = False
        self.bambu_client = None
        self.last_status = {}
        self.chamber_light_auto_off_delay = 300  # Auto-turn off light after 5 minutes of inactivity
        self._light_off_timer = None
        self._light_timer_lock = threading.Lock()
        self._connected = False
        # True after a successful dispatch until MQTT reports a busy state (or the lock expires).
        self._holding_dispatch_lock = False
        if self.ip and self.access_code:
            print("LAN credentials provided — skipping cloud authentication.")
        elif self.username and self.password:
            self._login()

    @property
    def connected(self) -> bool:
        if getattr(self, "_connected", False):
            return True
        if self.mqtt_client is not None:
            is_conn = getattr(self.mqtt_client, "is_connected", None)
            if callable(is_conn):
                res = is_conn()
                if isinstance(res, bool):
                    return res
        return False

    @connected.setter
    def connected(self, value: bool) -> None:
        self._connected = bool(value)

    def _acquire_printer_lock(self, timeout_seconds: float = 90.0) -> bool:
        """Acquires the printer dispatch lock."""
        return acquire_dispatch_lock(timeout_seconds=timeout_seconds)

    def _release_printer_lock(self) -> None:
        """Releases the printer dispatch lock."""
        release_dispatch_lock()

    def _login(self):
        print("Authenticating with Bambu Cloud...")
        if not BambuAuthenticator or not BaseBambuSdkClient:
            print("bambu-lab-cloud-api package not installed. Cannot authenticate.")
            return

        try:
            auth = BambuAuthenticator()
            self.auth_token = auth.get_or_create_token(
                username=self.username, password=self.password
            )
            self.bambu_client = BaseBambuSdkClient(token=self.auth_token)
            print("Authenticated successfully.")
        except Exception as e:
            print(f"Failed to authenticate: {e}")
            self.auth_token = None

    def upload_3mf(self, file_path):
        if not self.bambu_client:
            print("Not authenticated. Cannot upload.")
            return None, None

        filename = os.path.basename(file_path)
        print(f"Uploading {filename} to Bambu Cloud staging...")

        md5_hash = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                md5_hash.update(chunk)
        file_md5 = md5_hash.hexdigest()

        try:
            result = self.bambu_client.upload_file(file_path)
            time.sleep(2)

            aws_url = result.get("file_url") or result.get("url") or result.get("upload_url")
            if not aws_url:
                files = self.bambu_client.get_cloud_files()
                for f in files:
                    if (
                        f.get("name") == filename
                        or f.get("file_name") == filename
                        or f.get("title") == filename
                    ):
                        aws_url = f.get("file_url") or f.get("url")
                        break

            if aws_url:
                print("Upload successful!")
                return aws_url, file_md5
            else:
                print("Upload succeeded but could not find the file URL.")
                return None, None
        except Exception as e:
            print(f"Upload failed: {e}")
            return None, None

    def connect_mqtt(self):
        """Connects to the Bambu MQTT broker to send print commands and monitor AMS."""
        if self.ip and self.access_code:
            print("Connecting using LAN mode...")
            broker = self.ip
            port = 8883
            uid = "bblp"
            password = self.access_code
        else:
            if not self.auth_token:
                print("Cannot connect to MQTT without auth token.")
                return
            broker = "us.mqtt.bambulab.com"
            port = 8883
            try:
                user_info = self.bambu_client.get_user_info()
                uid = f"u_{user_info['uid']}"
            except Exception as e:
                print(f"Could not get user info for MQTT username: {e}")
                uid = "bblp"
            password = self.auth_token

        self.mqtt_client = mqtt.Client(
            client_id=f"text2printer_{self.device_serial}_{uuid.uuid4().hex[:8]}"
        )
        self.mqtt_client.username_pw_set(uid, password)
        if self.ip and self.access_code:
            import ssl

            self.mqtt_client.tls_set(cert_reqs=ssl.CERT_NONE)
            self.mqtt_client.tls_insecure_set(True)
        else:
            self.mqtt_client.tls_set()

        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message

        self.mqtt_client.connect_async(broker, port, 60)
        self.mqtt_client.loop_start()

        for _ in range(50):
            if self.mqtt_client.is_connected():
                break
            time.sleep(0.1)

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._connected = True
            print("Connected to Bambu MQTT broker!")
            client.subscribe(f"device/{self.device_serial}/report")
            client.publish(
                f"device/{self.device_serial}/request",
                json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}),
            )
        else:
            print(f"Failed to connect to MQTT, return code {rc}")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            if "print" in payload and isinstance(payload["print"], dict):
                if "gcode_state" in payload["print"]:
                    prev_state = self.last_status.get("state", "UNKNOWN")
                    new_state = payload["print"].get("gcode_state", "UNKNOWN")
                    self.last_status["state"] = new_state
                    if new_state in BUSY_STATES and self._holding_dispatch_lock:
                        # The printer has picked up our job; other services will now
                        # see it as busy via their own MQTT status, so free the lock.
                        self._holding_dispatch_lock = False
                        release_dispatch_lock()
                    if self.is_printing():
                        self._cancel_light_off_timer()
                    elif new_state == "FINISH" and prev_state != "FINISH":
                        self._schedule_light_off()
                if "mc_percent" in payload["print"]:
                    self.last_status["percent"] = payload["print"].get("mc_percent", 0)
                if "mc_remaining_time" in payload["print"]:
                    self.last_status["remaining_time"] = payload["print"].get(
                        "mc_remaining_time", 0
                    )

                if "ams" in payload["print"]:
                    try:
                        ams_block = payload["print"]["ams"]
                        ams_list = ams_block.get("ams", []) if isinstance(ams_block, dict) else []
                        if len(ams_list) > 0:
                            self.ams_data_received = True
                            for ams_unit in ams_list:
                                if not isinstance(ams_unit, dict):
                                    continue
                                ams_unit_id = int(ams_unit.get("id", 0) or 0)
                                ams_data = ams_unit.get("tray", [])
                                for tray in ams_data:
                                    try:
                                        if isinstance(tray, dict) and "id" in tray:
                                            color = tray.get("tray_color", "")
                                            ftype = tray.get("tray_type", "")
                                            if not color and not ftype:
                                                continue
                                            color = color[:6] if color else "Unknown"
                                            if not ftype:
                                                ftype = "Unknown"
                                            is_bambu = "tag_uid" in tray and tray["tag_uid"] != ""
                                            raw_tray_id = int(tray["id"])
                                            slot_id = (
                                                ams_unit_id * 4 + raw_tray_id
                                                if raw_tray_id < 4
                                                else raw_tray_id
                                            )
                                            self.ams_colors[slot_id] = {
                                                "color": color,
                                                "type": ftype,
                                                "remain": tray.get("remain", -1)
                                                if is_bambu
                                                else -1,
                                                "is_bambu": is_bambu,
                                            }
                                    except Exception as e:
                                        logger.warning(f"Error parsing AMS tray: {e}")
                    except Exception as e:
                        logger.warning(f"Error parsing AMS block: {e}")

                if "vt_tray" in payload["print"]:
                    try:
                        vt_data = payload["print"]["vt_tray"]
                        if isinstance(vt_data, dict):
                            vt_color = vt_data.get("tray_color", "")
                            vt_type = vt_data.get("tray_type", "")
                            if vt_color or vt_type:
                                vt_color = vt_color[:6] if vt_color else "Unknown"
                                if not vt_type:
                                    vt_type = "Unknown"
                                is_bambu = "tag_uid" in vt_data and vt_data["tag_uid"] != ""
                                self.ams_colors[4] = {
                                    "color": vt_color,
                                    "type": vt_type,
                                    "remain": vt_data.get("remain", -1) if is_bambu else -1,
                                    "is_bambu": is_bambu,
                                    "is_external": True,
                                }
                    except Exception as e:
                        logger.warning(f"Error parsing vt_tray: {e}")

            try:
                lights = payload.get("lights_report") or (
                    payload.get("print", {}).get("lights_report")
                    if isinstance(payload.get("print"), dict)
                    else None
                )
                if lights and isinstance(lights, list):
                    for light in lights:
                        if (
                            isinstance(light, dict)
                            and light.get("node") == "chamber_light"
                            and light.get("mode") == "off"
                        ):
                            self._cancel_light_off_timer()
            except Exception as e:
                logger.warning(f"Error parsing lights_report: {e}")
        except Exception as e:
            logger.warning(f"Error in on_message: {e}")

    def disconnect_mqtt(self):
        self._cancel_light_off_timer()
        self._connected = False
        if self._holding_dispatch_lock:
            self._holding_dispatch_lock = False
            release_dispatch_lock()
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()

    def is_connected(self):
        return bool(self.mqtt_client and self.mqtt_client.is_connected())

    def get_status(self):
        return self.last_status

    def is_printer_busy(self) -> tuple[bool, str]:
        """Checks if printer is currently executing a print job.
        Returns (is_busy: bool, description: str).

        An UNKNOWN state (no status report received yet) is NOT considered busy here so
        that prints are not blocked forever when status is unavailable; send_print_job()
        waits briefly for a first status report before deciding.
        """
        state = self.last_status.get("state", "UNKNOWN")
        percent = self.last_status.get("percent", 0)
        if state in BUSY_STATES:
            return True, f"Printer is currently {state} ({percent}% complete)"
        return False, "Printer is idle"

    def is_printing(self) -> bool:
        """Conservative check used by safety features (e.g. chamber light auto-off):
        treats an UNKNOWN state as printing so the light is never turned off mid-print.
        """
        state = self.last_status.get("state", "UNKNOWN")
        return state in BUSY_STATES or state == "UNKNOWN"

    def _wait_for_first_status(self, timeout_seconds: float = 2.0) -> None:
        """If connected but no gcode_state has arrived yet, wait briefly for one."""
        if "state" in self.last_status or not self.connected:
            return
        deadline = time.time() + timeout_seconds
        while time.time() < deadline and "state" not in self.last_status:
            time.sleep(0.05)

    def get_available_colors(self, blocking: bool = True):
        if blocking and not (self.ams_data_received or self.ams_colors):
            for _ in range(10):
                if self.ams_data_received or self.ams_colors:
                    break
                time.sleep(0.5)

        if not self.ams_colors:
            logger.warning("Did not receive AMS data. Returning empty dict.")
            return {}
        return self.ams_colors

    def send_print_job(self, file_path, ams_slot=None):
        """Sends a print job to the printer, protected by busy check and cross-process lock.

        Raises PrinterBusyError if the printer is busy or another service is dispatching.
        On success the dispatch lock is intentionally kept until MQTT reports a busy state
        (or it auto-expires after 90s), closing the window between the upload finishing and
        the printer transitioning to PREPARE during which a second job could slip through.
        """
        self._wait_for_first_status()
        busy, msg = self.is_printer_busy()
        if busy:
            logger.warning(f"Rejected print job: {msg}")
            raise PrinterBusyError(msg)

        if not acquire_dispatch_lock(timeout_seconds=90.0):
            logger.warning("Rejected print job: another service is currently dispatching a print")
            raise PrinterBusyError(
                "Another print job is currently being dispatched to the printer."
            )

        dispatched = False
        try:
            dispatched = bool(self._dispatch_lan_print(file_path, ams_slot))
            if dispatched and self.connected:
                self._holding_dispatch_lock = True
            return dispatched
        finally:
            if not (dispatched and self.connected):
                self._holding_dispatch_lock = False
                release_dispatch_lock()

    def _dispatch_lan_print(self, file_path, ams_slot=None) -> bool:
        """Uploads the file over FTPS and publishes the LAN print command. Returns success."""
        filename = os.path.basename(file_path)

        if not self.ip or not self.access_code:
            print("Printer IP or Access Code missing for LAN Mode print.")
            return False

        try:
            from bambulab.local_api import LocalPrintClient
        except ImportError:
            print("bambu-lab-cloud-api local_api module not found.")
            return False

        print(f"Uploading {filename} to FTP on {self.ip} via curl...")
        upload_success = False

        cmd = [
            "curl",
            "-k",
            "--tlsv1.2",
            "--ftp-port",
            "-",
            "--connect-timeout",
            "10",
            "--max-time",
            "120",
            "-T",
            file_path,
            f"ftps://{self.ip}:990/{quote(filename)}",
            "--user",
            f"bblp:{self.access_code}",
            "-s",
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                print("FTPS upload completed via curl!")
                upload_success = True
            else:
                print(f"curl upload failed with code {result.returncode}: {result.stderr}")
        except subprocess.TimeoutExpired:
            print("FTPS upload timed out after 120 seconds.")
        except Exception as e:
            print(f"Failed to execute curl: {e}")

        if not upload_success:
            print("Failed to upload file to printer via LAN.")
            return False

        if ams_slot is not None and ams_slot != 4:
            use_ams = True
            ams_mapping = [ams_slot]
        else:
            use_ams = False
            ams_mapping = []

        cmd = LocalPrintClient.create_print_command(
            f"/{filename}", use_ams=use_ams, ams_mapping=ams_mapping, timelapse=True
        )

        print(f"Publishing print command to device/{self.device_serial}/request...")
        if self.mqtt_client:
            self.mqtt_client.publish(f"device/{self.device_serial}/request", json.dumps(cmd))
            print("Print job submitted via LAN Mode MQTT!")
            return True
        else:
            print("MQTT Client not connected!")
            return False

    def stop_print(self):
        """Cancels the current print job."""
        if self.mqtt_client and self.mqtt_client.is_connected():
            cmd = {"print": {"sequence_id": "1", "command": "stop"}}
            result = self.mqtt_client.publish(
                f"device/{self.device_serial}/request", json.dumps(cmd)
            )
            result.wait_for_publish(timeout=5.0)
            print("Print stop command sent.")
            return True
        else:
            print("MQTT Client not connected!")
            return False

    def _cancel_light_off_timer(self):
        with self._light_timer_lock:
            if self._light_off_timer:
                try:
                    self._light_off_timer.cancel()
                except Exception:
                    pass
                self._light_off_timer = None

    def _schedule_light_off(self, delay: float | None = None):
        if delay is None:
            delay = self.chamber_light_auto_off_delay
        with self._light_timer_lock:
            if self._light_off_timer:
                try:
                    self._light_off_timer.cancel()
                except Exception:
                    pass
            self._light_off_timer = threading.Timer(delay, self._auto_turn_off_light_callback)
            self._light_off_timer.daemon = True
            self._light_off_timer.start()

    def _auto_turn_off_light_callback(self):
        if not self.is_printing():
            print("Auto-turning off chamber light after inactivity period.")
            self.turn_off_light()
        else:
            print("Printer is currently printing; skipping chamber light auto-off.")

    def turn_off_light(self):
        self._cancel_light_off_timer()
        if self.mqtt_client:
            cmd = {
                "system": {
                    "sequence_id": "1",
                    "command": "ledctrl",
                    "led_node": "chamber_light",
                    "led_mode": "off",
                    "led_on_time": 500,
                    "led_off_time": 500,
                    "loop_times": 0,
                    "interval_time": 0,
                }
            }
            res = self.mqtt_client.publish(f"device/{self.device_serial}/request", json.dumps(cmd))
            print("Turned off chamber light.")
            return res

    def turn_on_light(self, auto_off: bool = True):
        if auto_off and not self.is_printing():
            self._schedule_light_off()

        if self.mqtt_client:
            now = time.time()
            if hasattr(self, "_last_light_on") and (now - getattr(self, "_last_light_on", 0) < 10):
                return
            cmd = {
                "system": {
                    "sequence_id": "1",
                    "command": "ledctrl",
                    "led_node": "chamber_light",
                    "led_mode": "on",
                    "led_on_time": 500,
                    "led_off_time": 500,
                    "loop_times": 1,
                    "interval_time": 1000,
                }
            }
            res = self.mqtt_client.publish(f"device/{self.device_serial}/request", json.dumps(cmd))
            self._last_light_on = now
            print("Turned on chamber light.")
            return res

    def get_formatted_status(self):
        status = self.get_status()
        if not status and not self.ams_data_received:
            return "Printer status is currently unknown or unavailable."

        state = status.get("state", "IDLE")
        percent = status.get("percent", 0)
        rem_time = status.get("remaining_time", 0)

        if state == "IDLE":
            return "Printer Status:\nState: Not Printing (IDLE)"

        return f"Printer Status:\nState: {state}\nProgress: {percent}%\nRemaining Time: {rem_time} mins"

    def get_snapshot(self, temp_img=None):
        """Captures a frame from the printer's camera using RTSPS into outputs/snapshots/."""
        if not self.ip or not self.access_code:
            print("Printer IP or Access Code missing for snapshot.")
            return False

        os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
        cleanup_old_snapshots()

        if temp_img is None:
            temp_img = os.path.join(
                SNAPSHOTS_DIR, f"snapshot_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
            )

        try:
            import cv2

            os.environ.setdefault(
                "OPENCV_FFMPEG_CAPTURE_OPTIONS", "timeout;5000000|stimeout;5000000"
            )

            self.turn_on_light()
            time.sleep(1)

            quoted_code = quote(str(self.access_code), safe="")
            rtsps_url = f"rtsps://bblp:{quoted_code}@{self.ip}:322/streaming/live/1"

            def capture():
                c = cv2.VideoCapture(rtsps_url, cv2.CAP_FFMPEG)
                if not c.isOpened():
                    return False, None
                try:
                    r, f = c.read()
                finally:
                    c.release()
                return r, f

            ret, frame = capture()
            if ret and frame is not None:
                cv2.imwrite(temp_img, frame)
                return temp_img
            else:
                print("Failed to capture frame from the camera stream.")
                return None
        except Exception as e:
            print(f"Error getting snapshot: {e}")
            return None


# Backward-compatible alias
BambuCloud = BambuClient
