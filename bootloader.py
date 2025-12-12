import utime as time
import machine
import network
import urequests as requests
import json
import os

# ---------------- GitHub settings ----------------

GITHUB_USER   = "SLWRTHNU"
GITHUB_REPO   = "Iris-Classic"
GITHUB_BRANCH = "main"
VERSIONS_PATH = "versions.json"
GITHUB_TOKEN  = "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"


RAW_BASE_URL = "https://raw.githubusercontent.com/{}/{}/{}/".format(
    GITHUB_USER, GITHUB_REPO, GITHUB_BRANCH
)
VERSIONS_URL = RAW_BASE_URL + VERSIONS_PATH

CONTROL_PATH = "control.json"
CONTROL_URL = "https://raw.githubusercontent.com/{}/{}/{}/{}".format(
    GITHUB_USER, GITHUB_REPO, GITHUB_BRANCH, CONTROL_PATH
)

LOCAL_VERSION_FILE  = "local_version.txt"
DEVICE_ID_FILE      = "device_id.txt"
CONTROL_HASH_FILE   = "last_control_hash.txt"



# ---------------- LCD support ----------------

try:
    from Pico_LCD_1_8 import LCD_1inch8 as ST7735
    LCD_AVAILABLE = True
except ImportError:
    LCD_AVAILABLE = False
    ST7735 = None

BLACK = 0x0000
WHITE = 0xFFFF


def init_lcd():
    if not LCD_AVAILABLE:
        return None
    try:
        lcd = ST7735()
        lcd.fill(BLACK)
        lcd.show()
        return lcd
    except Exception as e:
        print("LCD init failed:", e)
        return None


def lcd_msg(lcd, lines):
    """
    Show up to 4 short lines of text on the LCD.
    lines: list[str]
    """
    if lcd is None:
        return
    lcd.fill(BLACK)
    y = 20
    for line in lines[:4]:
        lcd.text(line, 5, y, WHITE)
        y += 20
    lcd.show()


# ---------------- Config helpers ----------------

DEVICE_ID_FILE = "device_id.txt"

def load_device_id():
    """
    Read a persistent device ID from device_id.txt (root of Pico filesystem).
    Returns a string or None if missing/empty.
    """
    try:
        with open(DEVICE_ID_FILE, "r") as f:
            devid = f.read().strip()
            if devid:
                return devid
    except OSError:
        pass
    return None



def load_config_wifi():
    """
    Return (ssid, password) from config.py, or (None, None) if missing.
    """
    try:
        import config
    except ImportError:
        print("config.py not found yet (first boot / AP mode).")
        return None, None

    ssid = getattr(config, "WIFI_SSID", "") or None
    pwd  = getattr(config, "WIFI_PASSWORD", "") or None

    if not ssid:
        print("Config present but WIFI_SSID is empty.")
        return None, None

    return ssid, pwd


def get_github_headers():
    headers = {}
    token = GITHUB_TOKEN  # defined at top of file
    if token:
        headers["Authorization"] = "token {}".format(token)
    return headers





# ---------------- Wi-Fi ----------------

def connect_wifi(lcd, ssid, pwd, timeout_sec=10):
    """
    Connect to Wi-Fi. Returns True on success, False on failure.
    """
    if ssid is None or pwd is None:
        print("No Wi-Fi credentials; skipping Wi-Fi connect.")
        return False

    lcd_msg(lcd, ["Wi-Fi connecting", ssid])

    ap = network.WLAN(network.AP_IF)
    ap.active(False)

    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.connect(ssid, pwd)

    t0 = time.ticks_ms()
    while not sta.isconnected():
        if time.ticks_diff(time.ticks_ms(), t0) > timeout_sec * 1000:
            print("Wi-Fi connect timeout")
            lcd_msg(lcd, ["Wi-Fi failed", "Timeout"])
            return False
        time.sleep(0.5)

    ip = sta.ifconfig()[0]
    print("Wi-Fi OK. IP:", ip)
    lcd_msg(lcd, ["Wi-Fi OK", ip])
    time.sleep(1)
    return True


# ---------------- Version helpers ----------------

def load_local_version():
    try:
        with open(LOCAL_VERSION_FILE, "r") as f:
            v = f.read().strip()
            if v:
                return v
    except OSError:
        pass
    return "0.0.0"


def save_local_version(version_str):
    try:
        with open(LOCAL_VERSION_FILE, "w") as f:
            f.write(version_str)
    except OSError as e:
        print("Failed to write local version file:", e)


def fetch_versions_json():
    print("Fetching versions.json from:", VERSIONS_URL)
    headers = get_github_headers()
    try:
        r = requests.get(VERSIONS_URL, headers=headers)
        try:
            status = getattr(r, "status_code", getattr(r, "status", 0))
        except Exception:
            status = 0

        print("HTTP status:", status)
        if status != 200:
            print("Non-200 status getting versions.json")
            r.close()
            return None

        data = r.json()
        r.close()
        return data
    except Exception as e:
        print("Error fetching versions.json:", e)
        try:
            r.close()
        except Exception:
            pass
        return None




def ensure_dirs_for(target_path):
    """
    Create any intermediate directories for target_path if needed.
    E.g. "lib/foo/bar.py" -> create "lib", then "lib/foo".
    """
    parts = target_path.split("/")
    if len(parts) <= 1:
        return

    path = ""
    for p in parts[:-1]:
        if not p:
            continue
        path = (path + "/" + p) if path else p
        try:
            os.mkdir(path)
        except OSError:
            pass


def download_file(remote_path, target_path, lcd):
    """
    Download one file from GitHub (raw) and write it to target_path.
    Returns True/False for success.
    """
    url = RAW_BASE_URL + remote_path
    headers = get_github_headers()
    msg = "Updating " + target_path
    print(msg, "from", url)
    lcd_msg(lcd, ["Updating", target_path])

    try:
        r = requests.get(url, headers=headers)
        try:
            status = getattr(r, "status_code", getattr(r, "status", 0))
        except Exception:
            status = 0

        if status != 200:
            print("Download failed with status", status)
            r.close()
            lcd_msg(lcd, ["DL failed", target_path])
            time.sleep(1)
            return False

        content = r.content
        r.close()

        ensure_dirs_for(target_path)
        with open(target_path, "wb") as f:
            f.write(content)

        print("Saved", target_path, "OK")
        return True

    except Exception as e:
        print("Exception downloading", remote_path, "->", target_path, ":", e)
        lcd_msg(lcd, ["DL error", target_path])
        time.sleep(1)
        return False




def perform_update(vers_data, lcd):
    """
    vers_data: parsed JSON from versions.json
    Returns True if everything is OK (including 'no update needed'),
    False only on hard failure.

    NOTE: If an update *is* performed successfully, this function
    will call machine.reset() and never return.
    """
    if not vers_data:
        print("perform_update: no version data.")
        return False

    remote_version = str(vers_data.get("version", "0.0.0"))
    local_version  = load_local_version()
    print("Local version:", local_version, "| Remote version:", remote_version)

    # No update needed
    if remote_version == local_version:
        print("Versions match; no update needed.")
        lcd_msg(lcd, ["Firmware up to date"])
        time.sleep(1)
        return True  # bootloader.main() will then call app_main

    # We DO need to update
    files = vers_data.get("files", [])
    if not isinstance(files, list) or not files:
        print("versions.json has no files[] list.")
        return False

    lcd_msg(lcd, ["New version", remote_version, "Updating..."])
    print("New version detected; starting update.")

    for entry in files:
        try:
            remote_path = entry["path"]
            target_path = entry.get("target", remote_path)
        except Exception:
            print("Bad entry in files[]:", entry)
            return False

        ok = download_file(remote_path, target_path, lcd)
        if not ok:
            print("Aborting update due to failure on", target_path)
            return False

    # All files downloaded OK: store version and HARD RESET
    save_local_version(remote_version)
    lcd_msg(lcd, ["Update complete", "v " + remote_version])
    time.sleep(1)

    print("Update successful; performing full reset so new code runs cleanly.")
    machine.reset()
    # We never return from here


def get_or_create_device_id():
    """
    Persistent DEVICE_ID stored in device_id.txt.
    Will NOT overwrite a manually assigned ID (ex: 0000, 1234).
    Auto-generates only if missing.
    """
    try:
        with open(DEVICE_ID_FILE, "r") as f:
            dev_id = f.read().strip()
            if dev_id:
                return dev_id
    except OSError:
        pass  # File missing

    # File missing → auto generate one backup ID
    try:
        import ubinascii
        raw = machine.unique_id()
        hexid = ubinascii.hexlify(raw).decode().upper()
        dev_id = hexid[:4]  # 4 hex chars (fallback only)
    except Exception:
        dev_id = "0000"     # worst-case fallback

    try:
        with open(DEVICE_ID_FILE, "w") as f:
            f.write(dev_id)
        print("Created new DEVICE_ID:", dev_id)
    except OSError as e:
        print("Failed to write DEVICE_ID_FILE:", e)

    return dev_id

def _simple_hash(s):
    """
    Tiny string hash so the device can detect changes in control.json.
    """
    h = 0
    for ch in s:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return h


def _load_last_control_hash():
    try:
        with open(CONTROL_HASH_FILE, "r") as f:
            txt = f.read().strip()
            if txt:
                return int(txt)
    except OSError:
        pass
    return None


def _save_last_control_hash(h):
    try:
        with open(CONTROL_HASH_FILE, "w") as f:
            f.write(str(h))
    except OSError as e:
        print("Failed to write CONTROL_HASH_FILE:", e)


def check_remote_commands():
    """
    Check control.json on GitHub for commands targeting this device.
    If this device is listed, perform reboot or force-update.
    """
    # Ensure we have a persistent ID (from device_id.txt, or auto-generated)
    device_id = get_or_create_device_id()
    if not device_id:
        print("check_remote_commands: no device_id")
        return

    headers = get_github_headers()
    print("Checking remote commands for", device_id)

    try:
        r = requests.get(CONTROL_URL, headers=headers)
        status = getattr(r, "status_code", getattr(r, "status", 0))
        print("control.json HTTP status:", status)
        if status != 200:
            r.close()
            return

        data = r.json()
        r.close()
    except Exception as e:
        print("Error fetching control.json:", e)
        try:
            r.close()
        except Exception:
            pass
        return

    reboot_ids = data.get("reboot_ids", [])
    force_update_ids = data.get("force_update_ids", [])

    # IDs are compared as strings, e.g. "0000"
    if device_id in reboot_ids:
        print("Remote reboot for", device_id)
        machine.reset()

    if device_id in force_update_ids:
        print("Remote force-update for", device_id)
        # Clear local version so bootloader will re-download everything
        try:
            os.remove(LOCAL_VERSION_FILE)
        except OSError:
            pass
        machine.reset()




# ---------------- main/runner ----------------

def run_app_main():
    """
    Import and execute app_main.main().
    """
    print("Starting app_main.py...")
    try:
        import app_main as app
    except ImportError as e:
        print("ERROR: app_main.py not found or bad:", e)
        return

    if hasattr(app, "main"):
        try:
            app.main()
        except Exception as e:
            print("Error running app.main():", e)
    else:
        print("app_main.py has no main() function; nothing to call.")


def main():
    lcd = init_lcd()

    lcd_msg(lcd, ["Bootloader", "Starting..."])
    print("Bootloader starting...")

    ssid, pwd = load_config_wifi()

    # If no Wi-Fi config yet, skip OTA and let app_main handle AP config
    if ssid is None or pwd is None:
        print("No Wi-Fi config; skipping OTA and running app_main directly")
        lcd_msg(lcd, ["No Wi-Fi config", "Skipping OTA"])
        time.sleep(1)
        run_app_main()
        return

    # Try Wi-Fi for OTA
    if not connect_wifi(lcd, ssid, pwd):
        print("Wi-Fi failed; skipping OTA and running app_main.py")
        run_app_main()
        return

    vers_data = fetch_versions_json()
    if vers_data is None:
        print("Could not fetch versions.json; running app_main.py anyway.")
        lcd_msg(lcd, ["No versions.json", "Running app_main.py"])
        time.sleep(1)
        run_app_main()
        return

    ok = perform_update(vers_data, lcd)
    if not ok:
        print("Update failed (or invalid data). Running app_main.py anyway.")
        lcd_msg(lcd, ["Update failed", "Running app_main.py"])
        time.sleep(1)

    # If we get here, either no update was needed OR the update failed.
    # In both cases, just run the current app_main.py.
    run_app_main()


if __name__ == "__main__":
    main()


