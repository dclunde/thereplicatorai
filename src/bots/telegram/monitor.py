import asyncio
import os
from urllib.parse import quote
import uuid

from telegram.ext import Application

from src.bots.telegram.handlers_printer import get_bambu_cloud, known_chat_ids
from src.core.config import SNAPSHOTS_DIR, load_api_keys
from src.core.printer.bambu_client import BUSY_STATES

previous_print_state = "UNKNOWN"


def _capture_snapshot_sync(rtsps_url: str, output_path: str) -> bool:
    try:
        import cv2

        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "timeout;5000000|stimeout;5000000")
        cap = cv2.VideoCapture(rtsps_url, cv2.CAP_FFMPEG)
        if cap.isOpened():
            ret, frame = cap.read()
            cap.release()
            if ret and frame is not None:
                cv2.imwrite(output_path, frame)
                return True
        return False
    except ImportError:
        print("OpenCV not installed, skipping snapshot")
        return False
    except Exception as e:
        print(f"Error taking snapshot: {e}")
        return False


async def monitor_print_status(app: Application):
    global previous_print_state
    keys = load_api_keys()
    bambu_ip = keys.get("BAMBU_PRINTER_IP", "")
    bambu_access_code = keys.get("BAMBU_ACCESS_CODE", "")
    bambu_cloud = get_bambu_cloud()

    while True:
        try:
            current_state = bambu_cloud.last_status.get("state", "UNKNOWN")

            # Check for transition to FINISH
            if previous_print_state in BUSY_STATES and current_state == "FINISH":
                print("Print completed! Sending notification...")
                bambu_cloud.turn_on_light()
                await asyncio.sleep(2)

                os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
                snapshot_path = os.path.join(
                    SNAPSHOTS_DIR, f"completion_{uuid.uuid4().hex[:8]}.jpg"
                )
                try:
                    if bambu_ip and bambu_access_code:
                        quoted_code = quote(str(bambu_access_code), safe="")
                        rtsps_url = f"rtsps://bblp:{quoted_code}@{bambu_ip}:322/streaming/live/1"
                        await asyncio.to_thread(_capture_snapshot_sync, rtsps_url, snapshot_path)

                    for chat_id in known_chat_ids:
                        try:
                            await app.bot.send_message(
                                chat_id=chat_id, text="🎉 Your print has finished successfully!"
                            )
                            if os.path.exists(snapshot_path):
                                with open(snapshot_path, "rb") as photo:
                                    await app.bot.send_photo(
                                        chat_id=chat_id, photo=photo, caption="Final Result"
                                    )
                        except Exception as e:
                            print(f"Failed to send completion notification to {chat_id}: {e}")
                finally:
                    if os.path.exists(snapshot_path):
                        try:
                            os.remove(snapshot_path)
                        except Exception:
                            pass

            # Check for transition to FAILED
            if previous_print_state in BUSY_STATES and current_state == "FAILED":
                print("Print failed! Sending notification...")
                for chat_id in known_chat_ids:
                    try:
                        await app.bot.send_message(
                            chat_id=chat_id,
                            text="❌ Your print has failed or encountered an error!",
                        )
                    except Exception as e:
                        print(f"Failed to send failure notification to {chat_id}: {e}")

            previous_print_state = current_state
        except Exception as e:
            print(f"Error in monitor loop: {e}")

        await asyncio.sleep(60)
