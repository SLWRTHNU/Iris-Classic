import utime as time
import network
import urequests as requests
import json
import os
import gc
import ubinascii
import machine
from writer import CWriter

def guarded_reset(reason=""):
    try:
        if "no_reset.flag" in os.listdir():
            try:
                print("RESET SKIPPED (no_reset.flag): {}".format(reason))
            except:
                pass
            return False
    except:
        pass

    machine.reset()
    return True



# 0. SPEED BOOST: Overclock to 240MHz 
#machine.freq(240000000)

def log(msg):
    timestamp = time.ticks_ms()
    print("[{:>8}ms] {}".format(timestamp, msg))

# ---------- GitHub & Paths ----------
GITHUB_USER   = "SLWRTHNU"
GITHUB_REPO   = "Iris-Classic"
GITHUB_BRANCH = "main"
API_BASE      = "https://api.github.com/repos/{}/{}/contents/".format(GITHUB_USER, GITHUB_REPO)

VERSIONS_PATH = "versions.json"
LOCAL_VERSION_FILE = "local_version.txt"
DEVICE_ID_FILE     = "device_id.txt"

CURRENT_BRIGHTNESS = 100

def _get_token():
    try:
        import github_token
        t = getattr(github_token, "GITHUB_TOKEN", "")
        if t:
            t = t.strip()
        return t
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

# Use the driver variables for maximum compatibility
import Pico_LCD_2_8 as drv

# 1. Update your pin constants at the top
LCD_BL_PIN = 13
LCD_RST_PIN = 15

def _lcd_backlight_on():
    from machine import Pin, PWM
    bl = PWM(Pin(LCD_BL_PIN))
    bl.freq(1000)
    bl.duty_u16(65535) # Stable PWM as proven in shell

def _lcd_hard_reset():
    from machine import Pin
    rst = Pin(LCD_RST_PIN, Pin.OUT)
    rst.value(1)
    time.sleep_ms(50)
    rst.value(0) 
    time.sleep_ms(150)
    rst.value(1)
    time.sleep_ms(150) # Matching our successful shell timing

# Place this at the top of your "LCD Logic" section
_LCD_INSTANCE = None

def init_lcd():
    global _LCD_INSTANCE
    
    # If already initialized, just return it
    if _LCD_INSTANCE is not None:
        return _LCD_INSTANCE

    if LCD_Driver is None: 
        return None
    
    try:
        log("Performing Hard Reset and Driver Init...")
        _lcd_hard_reset()
        
        lcd = LCD_Driver()
        lcd.display_update = lcd.show
        lcd.fill(BLACK)
        lcd.display_update()
        _lcd_backlight_on()
        
        # Save to global so we never reset again this session
        _LCD_INSTANCE = lcd
        return _LCD_INSTANCE
        
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

    # Ensure Access Point is fully OFF
    ap = network.WLAN(network.AP_IF)
    if ap.active():
        ap.active(False)
        time.sleep_ms(500)

    sta = network.WLAN(network.STA_IF)

    try:
        network.hostname("Iris-Classic")
    except:
        pass

    for attempt in range(1, retries + 1):
        log("WiFi Attempt {}/{}".format(attempt, retries))

        # Hard reset the STA interface for a clean slate
        sta.active(False)
        time.sleep_ms(500)
        sta.active(True)

        # Re-apply PM fix for Pico 2 W stability
        try:
            sta.config(pm=0xa11140)
            log("WiFi Power Management: High Performance Set")
        except:
            pass

        sta.disconnect()
        time.sleep_ms(200)
        sta.connect(ssid, pwd)

        t0 = time.ticks_ms()
        last_log = t0  # used to log once per second

        while time.ticks_diff(time.ticks_ms(), t0) < timeout_sec * 1000:
            status = sta.status()

            # Log status once per second (stable, no modulo spam)
            now = time.ticks_ms()
            if time.ticks_diff(now, last_log) >= 1000:
                last_log = now
                log("WiFi Status: {}".format(status))

            if sta.isconnected():
                log("WiFi Connected! IP: " + sta.ifconfig()[0])
                return True

            # Catch known failure states
            if status < 0 or status == 201:
                log("WiFi Error: Auth/Hardware Failure ({})".format(status))
                break

            time.sleep_ms(250)

        log("Attempt {} timed out.".format(attempt))
        time.sleep_ms(1000)

    return False


def gh_contents_url(path):
    return API_BASE + path.lstrip("/") + "?ref=" + GITHUB_BRANCH

def fetch_versions_json(lcd):
    # Cache-bust so GitHub/CDNs don’t serve an older copy
    url = gh_contents_url(VERSIONS_PATH) + "&nocache={}".format(time.ticks_ms())

    r = None
    try:
        r = requests.get(url, headers=gh_api_headers_raw())
        log("versions.json HTTP: {}".format(r.status_code))

        if r.status_code != 200:
            # Often this is 403 rate limit or 404 path issues
            try:
                log("versions.json body (start): {}".format(r.text[:200]))
            except:
                pass
            return None

        try:
            data = json.loads(r.text)
        except Exception as e:
            log("versions.json JSON parse failed: {}".format(e))
            try:
                log("versions.json raw (start): {}".format(r.text[:200]))
            except:
                pass
            return None

        # Helpful confirmation log
        rv = (data.get("version") or "").strip()
        log("versions.json parsed OK. remote version='{}'".format(rv))
        return data

    except Exception as e:
        log("versions.json fetch error: {}".format(e))
        return None
    finally:
        if r:
            try: r.close()
            except: pass


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
 

def perform_update(vers_data, lcd):
    SKIP = ("github_token.py", "config.py", "local_version.txt")

    remote_v = (vers_data.get("version") or "").strip()
    if not remote_v:
        log("versions.json missing 'version' - aborting update")
        return False

    files = vers_data.get("files", [])
    work = []
    for f in files:
        p = f.get("path")
        t = f.get("target") or p.split("/")[-1]
        if t not in SKIP:
            work.append((p, t))

    if not work:
        return True

    # 1. DOWNLOAD
    for idx, (p, t) in enumerate(work, start=1):
        pct = int((idx * 100) / len(work))
        log("Downloading: {} ({}%)".format(t, pct))
        if lcd:
            draw_bottom_status(lcd, "Updating {}%".format(pct), show_id=True)
        if not gh_download_to_file(p, t + ".new"):
            return False
        gc.collect()

    # 2. COMMIT
    log("Swapping files...")
    if lcd:
        draw_bottom_status(lcd, "Saving", show_id=True)

    for p, t in work:
        _safe_swap(t)

    # Write local version AFTER all swaps
    with open(LOCAL_VERSION_FILE, "w") as f:
        f.write(remote_v)
        try:
            f.flush()
        except:
            pass

    # 3. REBOOT
    log("REBOOTING NOW")
    if lcd:
        draw_bottom_status(lcd, "Rebooting", show_id=True)

    time.sleep_ms(300)

    try:
        gc.collect()
        log("Hard reboot (WDT) after update")
        machine.WDT(timeout=2000)  # 2 seconds
        while True:
            time.sleep_ms(100)
    except Exception as e:
        log("WDT failed: {}".format(e))
        time.sleep_ms(200)
        machine.reset()


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
        guarded_reset("app_main crash")


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

            log("Bootloader updated. Hard rebooting...")
            time.sleep_ms(200)
            machine.WDT(timeout=10000)
            while True:
                pass

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
    
    # 2. START THE LCD ONCE (The only blink happens here)
    lcd = init_lcd()
    if not lcd:
        log("LCD critical failure")
        return

    # 3. Check for WiFi config
    config_exists = False
    try:
        os.stat("config.py")
        config_exists = True
    except OSError:
        config_exists = False

    # 4. Handle Setup Mode (If no config, clear logo and show setup)
    if not config_exists:
        log("Entering Setup Mode...")
        import config_font
        
        ap = network.WLAN(network.AP_IF)
        ap.active(True)
        ap.config(essid="Iris Classic", security=0)
        ip = "192.168.4.1"

        w_setup = CWriter(lcd, config_font, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
        w_setup.set_spacing(2) 
        lcd.fill(BLACK) # Clear screen for setup text

        def print_safe(text, y, x_val, color):
            tw = w_setup.stringlen(text)
            final_x = max(0, (320 - tw) // 2) if x_val == -1 else x_val
            w_setup.setcolor(color, BLACK)
            w_setup.set_textpos(lcd, y, final_x)
            w_setup.printstring(text)

        print_safe("Iris Setup", 20, -1, YELLOW) 
        print_safe("1) Connect to WiFi:", 80, 60, WHITE)
        print_safe("Iris Classic", 110, 90, YELLOW) 
        print_safe("2) Visit in browser:", 160, 60, WHITE)
        print_safe("{}".format(ip), 190, 90, YELLOW) 
        
        lcd.show()
        import setup_server
        setup_server.run()
        return

    # 5. NORMAL FLOW (Config exists)
    # The logo is drawn ONCE and stays there
    draw_boot_logo(lcd)
    
    # Handle staged updates
    apply_staged_bootloader_if_present()
    
    try:
        led = machine.Pin("LED", machine.Pin.OUT)
        led.on()
    except: pass

    log("BOOTLOADER: Starting Normal Boot...")

    # 6. WiFi Connection (Writes status over logo)
    ssid, pwd = load_config_wifi()
    if not ssid or not connect_wifi(lcd, ssid, pwd):
        log("WiFi Failed.")
        if lcd:
            lcd.fill(0x0000)
            lcd.text("WIFI FAILED", 40, 15, 0xFC00)
            lcd.show()
        return

    # 7. Check for Updates
    log("Checking for updates...")
    vers_data = fetch_versions_json(lcd)
    
    if vers_data:
        if vers_data.get("remote_command") == "reboot":
            guarded_reset("remote_command reboot")
                
        remote_v = (vers_data.get("version") or "").strip()
        if not remote_v:
            log("versions.json missing 'version' - skipping update check")
            run_app_main(lcd)
            return
        
        local_v = "0.0.0"
        log("Version check: local='{}' remote='{}' force_update={}".format(local_v, remote_v, vers_data.get("force_update")))

        try:
            with open(LOCAL_VERSION_FILE, "r") as f:
                local_v = f.read().strip()
        
        except:
            pass
        
        if vers_data.get("force_update") or (local_v != remote_v):
            ok = perform_update(vers_data, lcd)
            if not ok:
                log("Update failed - continuing with existing firmware")
                run_app_main(lcd)
                return
            return


        
    # 8. Success - Run App
    run_app_main(lcd)


if __name__ == "__main__":
    main()



