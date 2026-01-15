import utime as time
import network
import urequests as requests
import json
import os
import gc
import ubinascii
import machine
import config_font
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

def _clamp(n, lo, hi):
    return lo if n < lo else (hi if n > hi else n)

def _wifi_progress_pct(start_ms, timeout_sec):
    elapsed_ms = time.ticks_diff(time.ticks_ms(), start_ms)
    pct = int((elapsed_ms * 100) // (timeout_sec * 1000))
    return _clamp(pct, 0, 99)  # keep 99 until actually connected


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
        last_ui = t0  # UI update once per second
        
        while time.ticks_diff(time.ticks_ms(), t0) < timeout_sec * 1000:
            status = sta.status()
            
            now = time.ticks_ms()
            if time.ticks_diff(now, last_ui) >= 1000:
                last_ui = now
                
                pct = _wifi_progress_pct(t0, timeout_sec)
                if lcd:
                    draw_bottom_status(lcd, "Connecting {}%".format(pct), show_id=True)
                
                log("WiFi Status: {} ({}%)".format(status, pct))
                
            if sta.isconnected():
                if lcd:
                    draw_bottom_status(lcd, "Connected 100%", show_id=True)
                log("WiFi Connected! IP: " + sta.ifconfig()[0])
                return True
            
            if status < 0 or status == 201:
                if lcd:
                    draw_bottom_status(lcd, "ERR: WiFi {}".format(status), show_id=True)
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
    # Never OTA update these
    SKIP_ALWAYS = ("github_token.py", "config.py", "local_version.txt", "main.py")
    STAGE_ONLY  = ("bootloader.py",)  # download as .new, do NOT swap here

    remote_v = (vers_data.get("version") or "").strip()
    if not remote_v:
        log("versions.json missing 'version' - aborting update")
        return False

    files = vers_data.get("files", [])
    work_swap = []
    work_stage = []

    for f in files:
        p = f.get("path")
        t = f.get("target") or p.split("/")[-1]
        if not p or not t:
            continue

        if t in SKIP_ALWAYS:
            continue

        if t in STAGE_ONLY:
            work_stage.append((p, t))
        else:
            work_swap.append((p, t))

    # Nothing to do
    if not work_swap and not work_stage:
        return True

    total = len(work_swap) + len(work_stage)
    done = 0

    # 1) DOWNLOAD everything to .new
    for p, t in (work_swap + work_stage):
        done += 1
        pct = int((done * 100) / total)
        log("Downloading: {} ({}%)".format(t, pct))
        if lcd:
            draw_bottom_status(lcd, "Updating {}%".format(pct), show_id=True)

        if not gh_download_to_file(p, t + ".new"):
            log("Download failed: {}".format(t))
            return False

        gc.collect()

    # 2) COMMIT swaps (excluding staged bootloader)
    log("Swapping files...")
    if lcd:
        draw_bottom_status(lcd, "Saving", show_id=True)

    for p, t in work_swap:
        _safe_swap(t)

    # 3) Write local version at the end
    try:
        with open(LOCAL_VERSION_FILE, "w") as f:
            f.write(remote_v)
        try:
            os.sync()
        except:
            pass
    except Exception as e:
        log("Failed writing local_version: {}".format(e))
        # Still reboot; files may already be swapped

    # 4) Reboot (give flash time to settle)
    log("REBOOTING NOW")
    if lcd:
        draw_bottom_status(lcd, "Restarting...", show_id=True)
    
    # Cleanly shut down WiFi to prevent power spikes during reset
    try:
        network.WLAN(network.STA_IF).active(False)
    except:
        pass
        
    time.sleep_ms(500)
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
    if "bootloader.py.new" in os.listdir():
        try:
            log("Applying new bootloader...")
            try: os.remove("bootloader.py.old")
            except: pass

            os.rename("bootloader.py", "bootloader.py.old")
            os.rename("bootloader.py.new", "bootloader.py")

            log("Bootloader updated. Hard rebooting...")
            # Ensure the rename is physically written to flash
            try: os.sync()
            except: pass
            time.sleep_ms(500) 
            
            # Direct reset is more reliable than WDT loop on RP2350
            machine.reset() 

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
    
def show_wifi_failed(lcd):
    # Uses your existing globals: BLACK, WHITE, RED, and CWriter import already in bootloader
    import config_font
    import config_font_title
    from writer import CWriter

    w_body  = CWriter(lcd, config_font, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_title = CWriter(lcd, config_font_title, fgcolor=WHITE, bgcolor=BLACK, verbose=False)

    w_body.set_spacing(2)
    w_title.set_spacing(2)

    lcd.fill(BLACK)

    def center_title(text, y, color=RED):
        tw = w_title.stringlen(text)
        x = max(0, (lcd.width - tw) // 2)
        w_title.setcolor(color, BLACK)
        w_title.set_textpos(lcd, y, x)
        w_title.printstring(text)

    def body_line(text, y, x=10, color=WHITE):
        w_body.setcolor(color, BLACK)
        w_body.set_textpos(lcd, y, x)
        w_body.printstring(text)

    # Title (centered)
    center_title("WiFi Failed", 20, RED)

    # Body alignment based on BODY font
    x_num = 45
    num_prefix = "1) "
    x_text = x_num + w_body.stringlen(num_prefix)

    y = 65
    line_gap = 30
    wrap_gap = 20

    body_line("1) Power cycle", y, x_num)
    body_line("your Iris", y + wrap_gap, x_text)

    y += line_gap * 2
    body_line("2) Power cycle", y, x_num)
    body_line("your router", y + wrap_gap, x_text)

    y += line_gap * 2
    body_line("3) Factory Reset", y, x_num)
    body_line("to reconfigure", y + wrap_gap, x_text)

    lcd.show()


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
    
    ssid, pwd = load_config_wifi()
    if not ssid or not connect_wifi(lcd, ssid, pwd):
        log("WiFi Failed.")
        if lcd:
            show_wifi_failed(lcd)
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






