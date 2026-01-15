import utime as time
import network
import urequests as requests
import json
import os
import gc
import machine
from machine import Pin, PWM

# ---------- Constants ----------
GITHUB_USER   = "SLWRTHNU"
GITHUB_REPO   = "Iris-Classic"
GITHUB_BRANCH = "main"
API_BASE      = "https://api.github.com/repos/{}/{}/contents/".format(GITHUB_USER, GITHUB_REPO)

BLACK = 0x0000
WHITE = 0xFFFF
YELLOW = 0xFFE0
RED = 0xF800
LOCAL_VERSION_FILE = "local_version.txt"
DEVICE_ID_FILE     = "device_id.txt"
LOGO_FILE          = "logo.bin"
VERSIONS_PATH      = "versions.json"

# Hardware Pins
LCD_BL_PIN = 13
LCD_RST_PIN = 15

def log(msg):
    print("[{:>8}ms] {}".format(time.ticks_ms(), msg))

# ---------- 1. LCD & UI HELPERS ----------

def lcd_update(lcd):
    """Universal refresh that handles the RP2350 driver naming."""
    if hasattr(lcd, 'display'):
        lcd.display()
    elif hasattr(lcd, 'show') and callable(lcd.show):
        lcd.show()

def draw_bottom_status(lcd, msg):
    if not lcd: return
    # Simple status bar at bottom
    lcd.fill_rect(0, 228, 320, 12, WHITE)
    lcd.text(msg, 5, 230, BLACK)
    lcd_update(lcd)

def show_wifi_failed(lcd):
    if not lcd: return
    lcd.fill(BLACK)
    lcd.text("WIFI CONNECTION FAILED", 70, 100, RED)
    lcd.text("Check config.py", 100, 130, WHITE)
    lcd_update(lcd)

# ---------- 2. GITHUB & UPDATE LOGIC ----------

def gh_api_headers():
    h = {"User-Agent": "Pico2W-Iris", "Accept": "application/vnd.github.v3.raw"}
    try:
        import github_token
        h["Authorization"] = "Bearer " + github_token.GITHUB_TOKEN
    except: pass
    return h

def gh_download_to_file(path, out_path):
    url = API_BASE + path.lstrip("/") + "?ref=" + GITHUB_BRANCH
    r = None
    try:
        r = requests.get(url, headers=gh_api_headers())
        if r.status_code != 200: return False
        with open(out_path, "wb") as f:
            f.write(r.content)
        return True
    except: return False
    finally:
        if r: r.close()

def _safe_swap(target):
    new_file = target + ".new"
    bak_file = target + ".old"
    try:
        if target in os.listdir():
            try: os.remove(bak_file)
            except: pass
            os.rename(target, bak_file)
        os.rename(new_file, target)
        try: os.remove(bak_file)
        except: pass
    except: pass

def apply_staged_bootloader_if_present():
    if "bootloader.py.new" in os.listdir():
        try:
            print("Applying staged update...")
            _safe_swap("bootloader.py")
            
            # CRITICAL: Wait for flash and sync
            os.sync()
            time.sleep_ms(1000) 
            
            # Hard Reset
            machine.reset()
        except Exception as e:
            print("Swap failed:", e)

def fetch_versions_json(lcd):
    url = API_BASE + VERSIONS_PATH + "?ref=" + GITHUB_BRANCH + "&nocache=" + str(time.ticks_ms())
    r = None
    try:
        r = requests.get(url, headers=gh_api_headers())
        if r.status_code == 200:
            return r.json()
    except: pass
    finally:
        if r: r.close()
    return None

def perform_update(vers_data, lcd):
    SKIP_ALWAYS = ("github_token.py", "config.py", "local_version.txt", "main.py")
    STAGE_ONLY  = ("bootloader.py",)
    
    files = vers_data.get("files", [])
    remote_v = (vers_data.get("version") or "").strip()
    
    for f in files:
        p = f.get("path")
        t = f.get("target") or p.split("/")[-1]
        if t in SKIP_ALWAYS: continue
        
        log("Downloading: {}".format(t))
        draw_bottom_status(lcd, "Updating: {}".format(t))
        if not gh_download_to_file(p, t + ".new"):
            log("Download failed: " + t)
            return False
        
        if t not in STAGE_ONLY:
            _safe_swap(t)
        gc.collect()

    log("Update complete. Saving version...")
    with open(LOCAL_VERSION_FILE, "w") as f:
        f.write(remote_v)
    
    # 1. Force the file system to write all data to physical flash
    try:
        os.sync()
    except:
        pass
    
    # 2. Short pause to allow power rails to stabilize
    time.sleep_ms(500)
    
    log("REBOOTING...")
    
    # 3. Perform a clean hard reset
    machine.reset()

# ---------- 3. WIFI & HARDWARE ----------

def load_config_wifi():
    try:
        import config
        return config.WIFI_SSID, config.WIFI_PASSWORD
    except: return None, None

def connect_wifi(lcd, ssid, pwd):
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    try: sta.config(pm=0xa11140) # Disable power save for stability
    except: pass
    
    log("Connecting to WiFi: " + ssid)
    draw_bottom_status(lcd, "Connecting to WiFi: " + ssid)
    sta.connect(ssid, pwd)
    
    for _ in range(30): # 15 second timeout
        if sta.isconnected():
            log("WiFi Connected. IP: " + sta.ifconfig()[0])
            return True
        time.sleep_ms(500)
    return False

def _lcd_backlight_on():
    try:
        bl = PWM(Pin(LCD_BL_PIN))
        bl.freq(1000)
        bl.duty_u16(30000)
    except: pass

def init_lcd():
    try:
        # Import the actual CLASS, not the boolean variable
        from Pico_LCD_2_8 import LCD_2inch8 as LCD_Driver
        
        # Hardware Reset
        rst = Pin(LCD_RST_PIN, Pin.OUT)
        rst.value(1); time.sleep_ms(50)
        rst.value(0); time.sleep_ms(150)
        rst.value(1); time.sleep_ms(150)
        
        lcd = LCD_Driver()
        
        # Patch the names so the rest of your code works
        lcd.show = lcd.show_up
        lcd.display = lcd.show_up
        
        lcd.fill(BLACK)
        lcd.show() 
        
        # Note: Your driver handles backlight internally in __init__, 
        # but we can call it here to be sure.
        if hasattr(lcd, 'bl_ctrl'):
            lcd.bl_ctrl(100)
            
        return lcd
    except Exception as e:
        log("LCD Init Error: {}".format(e)) 
        return None

def draw_boot_logo(lcd):
    if not lcd: return
    try:
        if LOGO_FILE in os.listdir() and os.stat(LOGO_FILE)[6] == 153600:
            with open(LOGO_FILE, "rb") as f:
                f.readinto(lcd.buffer)
        else:
            lcd.fill(BLACK)
            lcd.text("IRIS CLASSIC", 110, 110, WHITE)
    except: lcd.fill(BLACK)
    lcd_update(lcd)

def run_app_main(lcd):
    gc.collect()
    log("Handoff -> app_main")
    try:
        import app_main
        app_main.main(lcd)
    except Exception as e:
        print("APP CRASH:", e)
        time.sleep(5)
        machine.reset()

# ---------- 4. MAIN RUNNER ----------

def main():
    apply_staged_bootloader_if_present()
    time.sleep_ms(500)
    
    lcd = init_lcd()
    if not lcd: return

    draw_boot_logo(lcd)
    
    ssid, pwd = load_config_wifi()
    if not ssid or not connect_wifi(lcd, ssid, pwd):
        show_wifi_failed(lcd)
        return

    log("Checking for updates...")
    draw_bottom_status(lcd, "Checking for updates...")
    vers_data = fetch_versions_json(lcd)
    
    if vers_data:
        local_v = "0.0.0"
        try:
            with open(LOCAL_VERSION_FILE, "r") as f:
                local_v = f.read().strip()
        except: pass
        
        remote_v = (vers_data.get("version") or "").strip()
        if (local_v != remote_v) or vers_data.get("force_update"):
            log("Updating: {} -> {}".format(local_v, remote_v))
            perform_update(vers_data, lcd)
            return

    run_app_main(lcd)

if __name__ == "__main__":
    main()
