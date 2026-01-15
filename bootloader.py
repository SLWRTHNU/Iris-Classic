import utime as time
import network
import urequests as requests
import json
import os
import gc
import machine
from machine import Pin, PWM

# ---------- Constants ----------
BLACK = 0x0000
WHITE = 0xFFFF
YELLOW = 0xFFE0
RED = 0xF800
LOCAL_VERSION_FILE = "local_version.txt"
DEVICE_ID_FILE = "device_id.txt"
LOGO_FILE = "logo.bin"

# Hardware Pins
LCD_BL_PIN = 13
LCD_RST_PIN = 15

def log(msg):
    print("[{:>8}ms] {}".format(time.ticks_ms(), msg))

# ---------- 1. THE UPDATE FIXES ----------

def apply_staged_bootloader_if_present():
    """This must run before ANYTHING else to prevent loops."""
    files = os.listdir()
    if "bootloader.py.new" in files:
        try:
            # Serial log only since LCD isn't up
            print("!!! STAGED BOOTLOADER FOUND - APPLYING !!!")
            
            if "bootloader.py.old" in files:
                os.remove("bootloader.py.old")

            # The critical swap
            os.rename("bootloader.py", "bootloader.py.old")
            os.rename("bootloader.py.new", "bootloader.py")
            
            try:
                os.sync() # Flush to physical flash
            except:
                pass
                
            print("Swap successful. Rebooting to new code...")
            time.sleep_ms(1000)
            machine.reset()
        except Exception as e:
            print("CRITICAL: Bootloader swap failed:", e)

def perform_update(vers_data, lcd):
    SKIP_ALWAYS = ("github_token.py", "config.py", "local_version.txt", "main.py")
    STAGE_ONLY  = ("bootloader.py",)

    remote_v = (vers_data.get("version") or "").strip()
    files = vers_data.get("files", [])
    
    work_swap = []
    work_stage = []

    for f in files:
        p = f.get("path")
        t = f.get("target") or p.split("/")[-1]
        if t in SKIP_ALWAYS: continue
        if t in STAGE_ONLY: work_stage.append((p, t))
        else: work_swap.append((p, t))

    # Download Phase
    for p, t in (work_swap + work_stage):
        log("Downloading: {}".format(t))
        if not gh_download_to_file(p, t + ".new"):
            return False
        gc.collect()

    # Swap Phase
    log("Committing changes...")
    for p, t in work_swap:
        _safe_swap(t)

    # Version Save
    with open(LOCAL_VERSION_FILE, "w") as f:
        f.write(remote_v)
    
    log("Update complete. Finalizing...")
    try:
        os.sync()
    except:
        pass
        
    time.sleep_ms(1000) # Give flash time to breathe
    machine.reset()

# ---------- 2. HARDWARE INIT ----------

def _lcd_backlight_on():
    try:
        bl = PWM(Pin(LCD_BL_PIN))
        bl.freq(1000)
        bl.duty_u16(30000) # Lower power for stability
    except:
        pass

def init_lcd():
    try:
        # Import driver here to save RAM if it fails
        from Pico_LCD_2_8 import PICO_RP2350 as LCD_Driver
        
        # Hard Reset LCD Hardware
        rst = Pin(LCD_RST_PIN, Pin.OUT)
        rst.value(1); time.sleep_ms(50)
        rst.value(0); time.sleep_ms(150)
        rst.value(1); time.sleep_ms(150)
        
        lcd = LCD_Driver()
        lcd.fill(BLACK)
        lcd.show()
        _lcd_backlight_on()
        return lcd
    except Exception as e:
        log("LCD Init Error: {}".format(e))
        return None

# ---------- 3. RUNNER ----------

def main():
    # STEP 1: Apply updates before doing anything else
    # If this isn't here, the Pico reboots into old code forever
    apply_staged_bootloader_if_present()

    # STEP 2: Hardware stability delay
    time.sleep_ms(500)
    
    # STEP 3: Init Display
    lcd = init_lcd()
    if not lcd:
        return

    # STEP 4: Standard Flow
    draw_boot_logo(lcd)
    
    ssid, pwd = load_config_wifi()
    if not ssid or not connect_wifi(lcd, ssid, pwd):
        show_wifi_failed(lcd)
        return

    # STEP 5: Update Check
    vers_data = fetch_versions_json(lcd)
    if vers_data:
        local_v = "0.0.0"
        try:
            with open(LOCAL_VERSION_FILE, "r") as f:
                local_v = f.read().strip()
        except: pass
        
        remote_v = (vers_data.get("version") or "").strip()
        if (local_v != remote_v):
            perform_update(vers_data, lcd)
            return

    # STEP 6: Launch App
    run_app_main(lcd)

if __name__ == "__main__":
    main()
