# control_poll.py
import utime as time
import network
import machine
import bootloader

# Check every 60 seconds
CONTROL_POLL_MS = 60_000 
_last_poll_ms = 0

def _wifi_connected():
    try:
        sta = network.WLAN(network.STA_IF)
        return sta.active() and sta.isconnected()
    except:
        return False

def tick(lcd=None):
    """
    Checks for remote updates or reboot commands while the app is running.
    """
    global _last_poll_ms

    now = time.ticks_ms()
    # Skip if it's not time yet
    if _last_poll_ms and time.ticks_diff(now, _last_poll_ms) < CONTROL_POLL_MS:
        return

    _last_poll_ms = now

    if not _wifi_connected():
        return

    try:
        print("APP: Checking for remote commands...")
        # Single-Trip: fetch the version data which now holds commands too
        vers_data = bootloader.fetch_versions_json(lcd)
        
        if vers_data:
            # 1. Check for Reboot Command
            if vers_data.get("remote_command") == "reboot":
                print("APP: Remote reboot received.")
                machine.reset()

            # 2. Check for Version Mismatch
            local_v = "0.0.0"
            try:
                with open("local_version.txt", "r") as f:
                    local_v = f.read().strip()
            except:
                pass

            remote_v = (vers_data.get("version") or "0.0.0").strip()
            force_update = vers_data.get("force_update", False)

            # If there's a new version, just reboot. 
            # The bootloader will see the difference and perform the update.
            if force_update or (remote_v != local_v):
                print("APP: Update detected ({} -> {}). Rebooting...".format(local_v, remote_v))
                machine.reset()

    except Exception as e:
        print("APP: control poll failed:", repr(e))
