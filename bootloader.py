import utime as time
import network
import urequests as requests
import json
import os
import gc
import ubinascii
import machine
import sys
import utime
from writer import CWriter


# 0. SPEED BOOST: Overclock to 240MHz 
#machine.freq(240000000)

def log(msg):
    timestamp = time.ticks_ms()
    print("[{:>8}ms] {}".format(timestamp, msg))

# ---------- GitHub & Paths ----------
GITHUB_USER   = "SLWRTHNU"
GITHUB_REPO   = "Iris-Classic"
GITHUB_BRANCH = "main"
RAW_BASE_URL  = "https://raw.githubusercontent.com/{}/{}/{}/".format(GITHUB_USER, GITHUB_REPO, GITHUB_BRANCH)
API_BASE      = "https://api.github.com/repos/{}/{}/contents/".format(GITHUB_USER, GITHUB_REPO)

VERSIONS_PATH = "versions.json"
LOCAL_VERSION_FILE = "local_version.txt"
DEVICE_ID_FILE     = "device_id.txt"

CURRENT_BRIGHTNESS = 100

def _get_token():
    try:
        import github_token
        return getattr(github_token, "GITHUB_TOKEN", "")
    except:
        return ""

def gh_api_headers_raw():
    h = {
        "User-Agent": "Pico",
        "Accept": "application/vnd.github.v3.raw",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = _get_token()
    if token:
        h["Authorization"] = "Bearer " + token
    return h

# ---------- Display driver ----------
try:
    from Pico_LCD_2_8 import LCD_2inch8 as LCD_Driver
except ImportError:
    try:
        from Pico_LCD_2_8 import PICO_RP2040 as LCD_Driver
    except ImportError:
        try:
            from Pico_LCD_2_8 import PICO_RP2350 as LCD_Driver
        except ImportError:
            raise RuntimeError("Missing 2.8 inch LCD driver")

BLACK = 0x0000
WHITE = 0xFFFF
YELLOW = 0xFFE0
RED = 0xF800
LOGO_FILE   = "logo.bin"
LOGO_W = 320
LOGO_H = 240
TEXT_HEIGHT = 11
BAR_HEIGHT  = 12
Y_POS       = 229
STATUS_X    = 3

# ---------- LCD Logic (ORDERED CORRECTLY) ----------

def _lcd_backlight_on():
    try:
        import Pico_LCD_2_8 as drv
        from machine import Pin, PWM
        # Try PWM first for smooth brightness
        bl_pin = Pin(drv.BL, Pin.OUT)
        pwm = PWM(bl_pin)
        pwm.freq(1000)
        pwm.duty_u16(65535) # 100% Brightness
        log("Backlight: PWM ON")
    except Exception as e:
        # Fallback: Just turn the pin HIGH
        try:
            import Pico_LCD_2_8 as drv
            from machine import Pin
            Pin(drv.BL, Pin.OUT).value(1)
            log("Backlight: PIN HIGH")
        except:
            log("Backlight: Failed to toggle")

def _lcd_hard_reset():
    try:
        import Pico_LCD_2_8 as drv
        from machine import Pin
        rst = Pin(drv.RST, Pin.OUT)
        rst.value(1)
        time.sleep_ms(20)
        rst.value(0)
        time.sleep_ms(100)
        rst.value(1)
        time.sleep_ms(200)
        log("LCD Hardware Reset Complete")
    except:
        pass

def init_lcd():
    if LCD_Driver is None: 
        log("Driver not found")
        return None
    try:
        # These are now defined ABOVE this function
        _lcd_hard_reset()
        lcd = LCD_Driver()
        
        # Point to the correct method we found earlier
        lcd.display_update = lcd.show
            
        lcd.fill(BLACK)
        lcd.display_update()
        _lcd_backlight_on()
        return lcd
    except Exception as e:
        log("LCD Init Error: {}".format(e))
        return None

# ---------- WiFi & Updates ----------

def load_config_wifi():
    try:
        import config
        ssid = getattr(config, "WIFI_SSID", None)
        pwd  = getattr(config, "WIFI_PASSWORD", None)
        if ssid: ssid = ssid.strip()
        if pwd: pwd = str(pwd)
        return ssid, pwd
    except ImportError:
        # This specifically catches when config.py is missing
        return None, None
    except Exception:
        return None, None

def connect_wifi(lcd, ssid, pwd, timeout_sec=15, retries=2):
    if not ssid:
        log("WiFi Error: No SSID")
        return False

    draw_bottom_status(lcd, "Connecting")
    
    # 1. Ensure Access Point is fully OFF
    ap = network.WLAN(network.AP_IF)
    if ap.active():
        ap.active(False)
        time.sleep_ms(500)

    sta = network.WLAN(network.STA_IF)
    
    try:
        network.hostname("Iris-Classic")
    except: pass

    for attempt in range(1, retries + 1):
        log("WiFi Attempt {}/{}".format(attempt, retries))
        
        # 2. Hard reset the STA interface for a clean slate
        sta.active(False)
        time.sleep_ms(500)
        sta.active(True)
        
        # 3. Explicitly disconnect before starting a new handshake
        sta.disconnect()
        time.sleep_ms(200)
        
        sta.connect(ssid, pwd)

        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < timeout_sec * 1000:
            status = sta.status()
            
            # 0=Idle, 1=Connecting, 2=No Route, 3=Connected, -1=Fail, -2=Bad Password
            if utime.ticks_ms() % 1000 < 100: # Log once per second
                log("WiFi Status: {}".format(status))
            
            # 4. Check for success
            if sta.isconnected():
                log("WiFi Connected! IP: " + sta.ifconfig()[0])
                return True
            
            if status < 0 or status == 201: 
                log("WiFi Error: Bad Auth/Failure ({})".format(status))
                break
                
            utime.sleep_ms(250)
        
        log("Attempt {} timed out.".format(attempt))
        time.sleep_ms(1000)

    return False

def gh_contents_url(path):
    return API_BASE + path.lstrip("/") + "?ref=" + GITHUB_BRANCH

def fetch_versions_json(lcd):
    url = gh_contents_url(VERSIONS_PATH)
    r = None
    try:
        r = requests.get(url, headers=gh_api_headers_raw())
        if r.status_code != 200: return None
        return json.loads(r.text)
    except: return None
    finally:
        if r: r.close()

def gh_download_to_file(path, out_path):
    url = gh_contents_url(path)
    r = None
    try:
        r = requests.get(url, headers=gh_api_headers_raw())
        if r.status_code != 200: return False
        
        if "/" in out_path:
            parts = out_path.split("/")[:-1]
            cur = ""
            for p in parts:
                cur = p if cur == "" else (cur + "/" + p)
                try: os.mkdir(cur)
                except: pass

        with open(out_path, "wb") as f:
            try:
                while True:
                    chunk = r.raw.read(1024)
                    if not chunk: break
                    f.write(chunk)
            except: f.write(r.content)
        return True
    except: return False
    finally:
        if r: r.close()

def _safe_swap(target):
    tmp, bak = target + ".new", target + ".old"
    try: os.remove(bak)
    except: pass
    try: os.rename(target, bak)
    except: pass
    os.rename(tmp, target)
    try: os.remove(bak)
    except: pass
 

def perform_update(vers_data, lcd, force=False):
    # We only skip user-specific credentials and the version tracker
    SKIP = ("github_token.py", "config.py", "local_version.txt")
    
    remote_v = (vers_data.get("version") or "0.0.0").strip()
    
    files = vers_data.get("files", [])
    work = []
    for f in files:
        p = f.get("path")
        t = f.get("target") or p.split("/")[-1]
        if t not in SKIP: 
            work.append((p, t))

    if not work: return True

    # 1. DOWNLOAD
    for idx, (p, t) in enumerate(work, start=1):
        pct = int((idx * 100) / len(work))
        log("Downloading: {} ({}%)".format(t, pct))
        if lcd: draw_bottom_status(lcd, "Updating {}%".format(pct), show_id=True)
        if not gh_download_to_file(p, t + ".new"): return False
        gc.collect()

    # 2. COMMIT
    log("Swapping files...")
    if lcd: draw_bottom_status(lcd, "Saving", show_id=True)
    for p, t in work: 
        _safe_swap(t)

    with open(LOCAL_VERSION_FILE, "w") as f: f.write(remote_v)
    
    try:
        import os
        os.sync() 
    except: pass
    
    # 3. THE HARD RESET (The most important part)
    log("REBOOTING NOW")
    if lcd:
        draw_bottom_status(lcd, "Rebooting", show_id=True)
    
    time.sleep(2) # IMPORTANT: Let the file system finish writing
    
    machine.WDT(timeout=10) 
    while True: pass
    

def run_app_main(lcd=None):
    gc.collect()
    log("Handoff -> app_main")
    try: draw_bottom_status(lcd, "Connected", show_id=True)
    except: pass
    try:
        import app_main
        app_main.main(lcd)
    except Exception as e:
        print("APP CRASH:", e)
        time.sleep(2)
        machine.reset()

def apply_staged_bootloader_if_present():
    # Check if a new version of the bootloader was downloaded
    if "bootloader.py.new" in os.listdir():
        try:
            log("Applying new bootloader...")
            # Delete the old backup if it exists
            try: os.remove("bootloader.py.old")
            except: pass
            
            # Rename current to old, and new to current
            os.rename("bootloader.py", "bootloader.py.old")
            os.rename("bootloader.py.new", "bootloader.py")
            
            log("Bootloader updated. Restarting...")
            machine.reset() # Restart to run the new code
        except Exception as e:
            log("Bootloader swap failed: {}".format(e))
        
def draw_bottom_status(lcd, status_msg, show_id=None):
    if lcd is None: return
    # Show ID if connecting or if an error starts with ERR
    if show_id is None:
        show_id = any(status_msg.startswith(x) for x in ["Connecting", "Connected", "ERR:"])

    # Draw status bar at the bottom
    lcd.fill_rect(0, Y_POS - 1, lcd.width, BAR_HEIGHT, WHITE)
    lcd.text(status_msg, STATUS_X, Y_POS, BLACK)

    if show_id:
        device_id = "N/A"
        try:
            if DEVICE_ID_FILE in os.listdir():
                with open(DEVICE_ID_FILE, "r") as f:
                    device_id = f.read().strip()
        except: pass
        id_text = "ID:{}".format(device_id)
        id_x = lcd.width - (len(id_text) * 8) - 3
        lcd.text(id_text, id_x, Y_POS, BLACK)
    lcd.show()

def draw_boot_logo(lcd):
    if lcd is None: return
    # 320x240 * 2 bytes = 153,600
    expected = 153600 
    try:
        st = os.stat(LOGO_FILE)
        if st[6] == expected:
            with open(LOGO_FILE, "rb") as f:
                f.readinto(lcd.buffer)
            log("Logo binary loaded.")
        else:
            lcd.fill(BLACK)
    except Exception as e:
        log("Logo error: {}".format(e))
        lcd.fill(BLACK)
        
    lcd.show() 
    draw_bottom_status(lcd, "Connecting")

# ---------- Runner ----------
def main():
    # 1. Hardware Stability Delay
    time.sleep_ms(500) 
    
    # 2. Start the LCD and show the logo immediately
    lcd = init_lcd()
    if lcd:
        draw_boot_logo(lcd)
    
    # 3. Handle Bootloader Updates FIRST
    # If this finds a new file, it will reboot the Pico immediately
    apply_staged_bootloader_if_present()
    
    # 4. Heartbeat LED
    try:
        led = machine.Pin("LED", machine.Pin.OUT)
        led.on()
    except:
        pass

    log("BOOTLOADER: Starting...")
    
    # 5. Check for WiFi config
    config_exists = False
    try:
        os.stat("config.py")
        config_exists = True
    except OSError:
        config_exists = False

    # 3. Handle Setup Mode FIRST (No Logo)
    if not config_exists:
        lcd = init_lcd() # Start LCD but don't draw logo
        if not lcd: return

        from writer import CWriter
        import config_font
        
        log("Entering Setup Mode (Skipping Logo)...")
        ap = network.WLAN(network.AP_IF)
        ap.active(True)
        ap.config(essid="Iris Classic", security=0)
        ip = "192.168.4.1"

        # Initialize Setup UI
        w_setup = CWriter(lcd, config_font, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
        w_setup.set_spacing(2) 
        lcd.fill(BLACK)

        def print_safe(text, y, x_val, color):
            tw = w_setup.stringlen(text)
            final_x = max(0, (320 - tw) // 2) if x_val == -1 else x_val
            w_setup.setcolor(color, BLACK)
            w_setup.set_textpos(lcd, y, final_x)
            w_setup.printstring(text)

        # Draw Setup Screen
        print_safe("Iris Setup", 20, -1, YELLOW) 
        print_safe("1) Connect to WiFi:", 80, 60, WHITE)
        print_safe("Iris Classic", 110, 90, YELLOW) 
        print_safe("2) Visit in browser:", 160, 60, WHITE)
        print_safe("{}".format(ip), 190, 90, YELLOW) 
        
        lcd.show()
        
        import setup_server
        setup_server.run()
        return

    # 4. NORMAL FLOW (Logo only shows if config exists)
    lcd = init_lcd()
    if lcd:
        draw_boot_logo(lcd)
    
    apply_staged_bootloader_if_present()
    
    try:
        led = machine.Pin("LED", machine.Pin.OUT)
        led.on()
    except: pass

    log("BOOTLOADER: Starting Normal Boot...")

    # 5. Normal Boot WiFi Connection
    ssid, pwd = load_config_wifi()
    if not ssid or not connect_wifi(lcd, ssid, pwd):
        log("WiFi Failed.")
        if lcd:
            lcd.fill(0x0000) # BLACK 
            lcd.text("WIFI FAILED", 40, 15, 0xFC00) # RED
            lcd.text("1. Power cycle", 10, 40, 0xFFFF)
            lcd.text("   your Iris", 10, 50, 0xFFFF)
            lcd.text("2. Power cycle", 10, 70, 0xFFFF)
            lcd.text("   your router", 10, 80, 0xFFFF)
            lcd.text("3. Factory Reset", 10, 100, 0xFFFF)
            lcd.text("   to reconfigure", 10, 110, 0xFFFF)
            lcd.show()
        return

    # 8. Check for Updates
    log("Checking for updates...")
    vers_data = fetch_versions_json(lcd)
    
    if vers_data:
        if vers_data.get("remote_command") == "reboot":
            machine.reset()
            
        remote_v = (vers_data.get("version") or "0.0.0").strip()
        local_v = "0.0.0"
        try:
            with open(LOCAL_VERSION_FILE, "r") as f: local_v = f.read().strip()
        except: pass

        if (local_v != remote_v) or vers_data.get("force_update"):
            perform_update(vers_data, lcd, force=True)
            return 
    
    # 9. Success - Run App
    run_app_main(lcd)

if __name__ == "__main__":
    main()
