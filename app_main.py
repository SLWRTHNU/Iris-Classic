def log(msg):
    # Change 'time' to 'utime' here
    timestamp = utime.ticks_ms()
    print("[{:>8}ms] {}".format(timestamp, msg))
    
# app_main.py (Iris Classic 1.8")
import utime
import network
import ntptime
import urequests as requests
import gc
from machine import Timer, WDT, reset
import control_poll

def log(msg):
    gc.collect() 
    free = gc.mem_free()
    timestamp = utime.ticks_ms()
    print("[{:>8}ms] [RAM Free: {:>5}B] {}".format(timestamp, free, msg))

# ---------- Config ----------
import config

# Helper to get config values with fallbacks
def cfg(name, default):
    return getattr(config, name, default)

# Assign portal values to variables used in the app
WIFI_SSID = cfg('WIFI_SSID', '')
WIFI_PASSWORD = cfg('WIFI_PASSWORD', '')
NS_URL = cfg('NS_URL', '')
NS_TOKEN = cfg('API_SECRET', '')
API_ENDPOINT = cfg('API_ENDPOINT', '/api/v1/entries/sgv.json?count=2')
DISPLAY_UNITS = cfg('UNITS', 'mmol')

# Ensure these are numbers for comparison logic
LOW_THRESHOLD = float(cfg('THRESHOLD_LOW', 4.0))
HIGH_THRESHOLD = float(cfg('THRESHOLD_HIGH', 11.0))
STALE_MIN = int(cfg('STALE_MINS', 7))

# The new Alert Toggles
ALERT_DOUBLE_UP = cfg('ALERT_DOUBLE_UP', True)
ALERT_DOUBLE_DOWN = cfg('ALERT_DOUBLE_DOWN', True)

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

# ---------- Fonts / Writer ----------
from writer import CWriter
import small_font as font_small
import age_small_font as age_font_small
import large_font as font_big
import arrows_font as font_arrows
import heart as font_heart
import delta as font_delta # Imported as font_delta to avoid naming conflicts

# ---------- Colors ----------
BLACK  = 0x0000
WHITE  = 0xFFFF
RED    = 0xF800  # Updated
YELLOW = 0xFFE0  # Updated
GREEN  = 0x07E0  # Updated

# --- Global Heart State ---
hb_state = True

# ---------- Helpers ----------
def get_device_id():
    try:
        with open("device_id.txt", "r") as f:
            return f.read().strip()
    except Exception:
        return "N/A"

def connect_wifi(ssid, pwd, timeout_sec=15):
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.config(pm=0xa11140) 
    
    if sta.isconnected(): return True
    log("Connecting WiFi...")
    sta.connect(ssid, pwd)
    t0 = utime.ticks_ms()
    while not sta.isconnected():
        if utime.ticks_diff(utime.ticks_ms(), t0) > timeout_sec * 1000:
            return False
        utime.sleep(0.5)
    return True


def ntp_sync():
    try:
        before = now_unix_s()
        ntptime.settime()
        after = now_unix_s()
        drift = after - before
        log("NTP Sync Successful. Drift: {}s".format(drift))
        return True
    except Exception as e:
        log("NTP Sync Failed: {}".format(e))
        return False


def ensure_count2(endpoint: str) -> str:
    if "count=" in endpoint: return endpoint.replace("count=1", "count=2")
    joiner = "&" if "?" in endpoint else "?"
    return endpoint + joiner + "count=2"


def fetch_ns_entries():
    gc.collect() # Clean up before starting
    headers = {
        "Accept": "application/json",
        "Connection": "close"  # <--- Crucial: Tells server to kill the socket
    }
    if NS_TOKEN: headers["api-secret"] = NS_TOKEN
    url = NS_URL + ensure_count2(API_ENDPOINT)
    resp = None
    try:
        # Reduced timeout to 5s to stay well under the 8s Watchdog
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        log("Fetch error: {}".format(e))
    finally:
        if resp:
            resp.close()
            del resp
        gc.collect() # <--- Force cleanup of the socket memory immediately
    return None

def mgdl_to_units(val_mgdl: float) -> float:
    try:
        if str(DISPLAY_UNITS).lower() == "mgdl": 
            return float(val_mgdl)
        return round(float(val_mgdl) / 18.0, 1) # Convert to mmol
    except:
        return 0.0

def direction_to_arrow(direction: str) -> str:
    return {
        "Flat": "B",
        "SingleUp": "G",
        "DoubleUp": "GG",
        "SingleDown": "H",
        "DoubleDown": "HH",
        "FortyFiveUp": "D",
        "FortyFiveDown": "F",
        "NOT COMPUTABLE": "--",
        "NONE": "--",
    }.get(direction or "NONE", "")


def parse_entries(data):
    if not data or not isinstance(data, list) or len(data) < 1: 
        return None
        
    cur = data[0]
    cur_mgdl = cur.get("sgv")
    # Nightscout sometimes uses "date" or "mills"
    cur_time_ms = int(cur.get("mills", cur.get("date", 0)))
    
    if cur_mgdl is None: 
        return None
    
    delta_units = None
    if len(data) > 1:
        prev_mgdl = data[1].get("sgv")
        if prev_mgdl is not None:
            diff = float(cur_mgdl) - float(prev_mgdl)
            if str(DISPLAY_UNITS).lower() == "mgdl":
                delta_units = diff
            else:
                delta_units = diff / 18.0
        
    return {
        "bg": mgdl_to_units(cur_mgdl),
        "time_ms": cur_time_ms,
        "direction": cur.get("direction", "NONE"),
        "arrow": direction_to_arrow(cur.get("direction")),
        "delta": delta_units,
    }


def fmt_bg(bg_val) -> str:
    if bg_val is None: return "---" # Safety check
    try:
        if str(DISPLAY_UNITS).lower() == "mgdl":
            return str(int(round(bg_val)))
        return "{:.1f}".format(float(bg_val))
    except:
        return "ERR"
        
def fmt_delta(delta_val) -> str:
    if delta_val is None: return ""
    return "{:+.0f}".format(delta_val) if str(DISPLAY_UNITS).lower() == "mgdl" else "{:+.1f}".format(delta_val)
    

def now_unix_s():
    t = utime.time()
    return t + UNIX_2000_OFFSET if t < 1200000000 else t

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

def draw_screen(lcd, w_small, w_age_small, w_big, w_arrow, w_heart, w_delta_icon, last, hb_state, heart_only=False): 
    W, H = lcd.width, lcd.height # 320, 240
    
    # --- POSITIONAL CONSTANTS ---
    # These must be defined first so both full and partial draws use the same math
    W, H = lcd.width, lcd.height
    y_age = 6
    heart_right_margin = 4
    age_small_h = age_font_small.height()
    heart_h = font_heart.height()
    heart_w = w_heart.stringlen("T")
    
    # Calculate Heart Position
    x_heart = W - heart_right_margin - heart_w
    y_heart = y_age + (age_small_h - heart_h) // 4

    # --- PARTIAL DRAW (Heart Blink) ---
    if heart_only:
        # Erase just the heart area
        lcd.fill_rect(x_heart, y_heart, heart_w, heart_h, BLACK)
        if hb_state:
            w_heart.setcolor(RED, BLACK)
            w_heart.set_textpos(lcd, y_heart, x_heart)
            w_heart.printstring("T")
        
        # Performance: Use show_rect if your driver supports it, else standard show
        if hasattr(lcd, "show_rect"):
            lcd.show_rect(x_heart, y_heart, heart_w, heart_h)
        else:
            lcd.show()
        return

    # --- LOADING STATE ---
    if not last:
        device_id = get_device_id()
        id_text = "ID:{}".format(device_id)
        draw_bottom_status(lcd, "Loading", show_id=True)
        lcd.show()
        return         

    # --- FULL DATA STATE DRAW ---
    lcd.fill(BLACK)
    M = 4 
    
    raw_s = last["time_ms"] // 1000
    age_s = now_unix_s() - raw_s
    if age_s < 0: age_s = 0
    mins = int((age_s + 30) // 60)

    bg_val = last["bg"]
    #bg_val = "88.8"
    direction = last["direction"]
    #direction = last["direction"]
    bg_text = fmt_bg(bg_val)
    #bg_text = "88.8"
    arrow_text = last["arrow"]
    #arrow_text = "44"
    delta_text = fmt_delta(last["delta"])
    #delta_text = "8.8
    age_text = "{} {} ago".format(mins, "min" if mins == 1 else "mins")
    #age_text = "88 mins ago"
    
    # Color Logic
    age_color = RED if mins >= STALE_MIN else WHITE
    bg_color = GREEN
    if bg_val <= LOW_THRESHOLD:
        bg_color = RED
    elif bg_val >= HIGH_THRESHOLD:
        bg_color = YELLOW
        
    arrow_color = WHITE
    if ALERT_DOUBLE_UP and direction == "DoubleUp":
        arrow_color = YELLOW
    elif ALERT_DOUBLE_DOWN and direction == "DoubleDown":
        arrow_color = RED

    small_h = font_small.height()
    big_h = font_big.height()
    arrow_h = font_arrows.height()
    bottom_h = max(small_h, arrow_h)

    y_bg = (H - big_h) // 2
    y_bottom_base = H - bottom_h - 1
    
    # Arrow Position
    arrow_offset = -2 
    y_arrow = (y_bottom_base + (bottom_h - arrow_h) // 2) + arrow_offset
    y_delta = y_bottom_base + (bottom_h - small_h) // 2

    # Draw Age
    heart_age_gap = 6
    age_w = w_age_small.stringlen(age_text)
    x_age = x_heart - age_w - heart_age_gap
    w_age_small.setcolor(age_color, BLACK)
    w_age_small.set_textpos(lcd, y_age, x_age)
    w_age_small.printstring(age_text)

    # Draw Heart (Full Draw Phase)
    if hb_state:
        w_heart.setcolor(RED, BLACK)
        w_heart.set_textpos(lcd, y_heart, x_heart)
        w_heart.printstring("T")

    # Draw BG
    w_big.setcolor(bg_color, BLACK)
    x_bg = (W - w_big.stringlen(bg_text)) // 2
    w_big.set_textpos(lcd, y_bg, x_bg)
    w_big.printstring(bg_text)

    # Draw Trend Arrow
    w_arrow.setcolor(arrow_color, BLACK)
    w_arrow.set_textpos(lcd, y_arrow, 10) 
    w_arrow.printstring(arrow_text)

    # Draw Delta (Fixed Sign Logic)
    if delta_text:
        sign = delta_text[0]  # Correctly pulls + or -
        val_num = delta_text[1:] 
        
        gap = 5          
        v_offset = -5
        
        w_small.setcolor(WHITE, BLACK)
        w_delta_icon.setcolor(WHITE, BLACK)
        
        h_small = font_small.height()
        h_delta = font_delta.height()
        y_delta_centered = y_delta + (h_small - h_delta) // 2 + v_offset

        num_w = w_small.stringlen(val_num)
        sign_w = w_delta_icon.stringlen(sign)
        
        x_num = W - M - num_w
        x_sign = x_num - sign_w - gap
        
        w_delta_icon.set_textpos(lcd, y_delta_centered, x_sign)
        w_delta_icon.printstring(sign)
        
        w_small.set_textpos(lcd, y_delta, x_num)
        w_small.printstring(val_num)

    lcd.show()

# ---------- Main Loop ----------

def main(lcd=None):
    log("--- SYSTEM START / REBOOT ---")
    wdt = WDT(timeout=8000) # Hardware fail-safe
    
    global hb_state
    last, last_drawn_hb = None, hb_state 
    if lcd is None: lcd = LCD_Driver()

    # Initialize Writers
    w_small = CWriter(lcd, font_small, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_age_small = CWriter(lcd, age_font_small, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_small.set_spacing(3)
    w_age_small.set_spacing(2)
    w_big = CWriter(lcd, font_big, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_arrow = CWriter(lcd, font_arrows, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_heart = CWriter(lcd, font_heart, fgcolor=RED, bgcolor=BLACK, verbose=False)
    w_delta_icon = CWriter(lcd, font_delta, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_arrow.set_spacing(8)

    # Initial Screen Setup
    draw_screen(lcd, w_small, w_age_small, w_big, w_arrow, w_heart, w_delta_icon, None, hb_state)
    
    if connect_wifi(WIFI_SSID, WIFI_PASSWORD): 
        ntp_sync()
    
    # --- UPDATED TIMING INTERVALS ---
    GLUCOSE_INTERVAL = 5000    # 15 seconds
    CONTROL_INTERVAL = 300000   # 5 minutes (Corrected)
    
    next_glucose = utime.ticks_ms() + 1000
    next_control = utime.ticks_ms() + 30000 # First check 30s after boot
    
    # Safe Heartbeat Toggle
    def toggle_hb(t):
        global hb_state
        hb_state = not hb_state

    heart_timer = Timer(-1)
    heart_timer.init(period=1000, mode=Timer.PERIODIC, callback=toggle_hb)

    while True:
        wdt.feed() # Pat the dog
        now = utime.ticks_ms()
        
        # Network connection check - reboots if failed
        sta = network.WLAN(network.STA_IF)
        if not sta.isconnected():
            log("WiFi lost. Attempting reconnect...")
            connect_wifi(WIFI_SSID, WIFI_PASSWORD)
            # If we just reconnected, sync time again
            if sta.isconnected():
                ntp_sync()

       # 1. Heartbeat Blink
        if hb_state != last_drawn_hb:
            last_drawn_hb = hb_state
            if last: 
                draw_screen(lcd, w_small, w_age_small, w_big, w_arrow, w_heart, w_delta_icon, last, hb_state, heart_only=True)
                
        # 2. Glucose Fetch (15s)
        if utime.ticks_diff(now, next_glucose) >= 0:
            log("Fetching Glucose...")
            data = fetch_ns_entries()
            parsed = parse_entries(data)
            if parsed:
                last = parsed
                # Force a redraw immediately when new data arrives
                draw_screen(lcd, w_small, w_age_small, w_big, w_arrow, w_heart, w_delta_icon, last, hb_state)
            
            next_glucose = utime.ticks_add(now, GLUCOSE_INTERVAL)
            gc.collect()

       # 3. Control Poll (5m)
        if utime.ticks_diff(now, next_control) >= 0:
            log("Checking for Control Updates...")
            control_poll.tick(lcd)
            next_control = utime.ticks_add(now, CONTROL_INTERVAL)
        
        utime.sleep_ms(100)
        
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("CRITICAL CRASH:", e)
        utime.sleep(5)
        reset()




