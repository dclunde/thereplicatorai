import os
import sys
import time

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.core.config import load_env_keys
from src.core.printer import BambuCloud


def main():
    keys = load_env_keys()

    bambu = BambuCloud(
        keys.get("BAMBU_USERNAME"),
        keys.get("BAMBU_PASSWORD"),
        keys.get("BAMBU_DEVICE_SERIAL"),
        keys.get("BAMBU_PRINTER_IP"),
        keys.get("BAMBU_ACCESS_CODE"),
    )

    print("Connecting to printer...")
    bambu.connect_mqtt()

    time.sleep(2)

    success = bambu.stop_print()
    time.sleep(1)  # Allow MQTT publish to drain before disconnect
    if success:
        print("Successfully sent stop command.")
    else:
        print("Failed to send stop command.")

    bambu.disconnect_mqtt()


if __name__ == "__main__":
    main()
