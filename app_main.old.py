# app_main.py (Iris Classic 2.8")
import utime
import network
import ntptime
import urequests as requests
import gc
from machine import Timer
from Pico_LCD_2_8 import PICO_RP2040, PICO_RP2350

# Optional: not all MicroPython builds have setdefaulttimeout()
try:
    import socket
    if hasattr(socket, "setdefaulttimeout"):
        socket.setdefaulttimeout(2)
except Exception:
    pass

# ---------- Config ----------
from config import * # noqa

# ---------- Display driver ----------
try:
    from Pico_LCD_2_8 import LCD_2inch8 as LCD
except ImportError:
    raise RuntimeError("Missing Pico_LCD_2_8.py")

if not hasattr(LCD, "show") and hasattr(LCD, "show_up"):
    def _show(self):
        self.show_up()
    LCD.show = _show

# ---------- Fonts / Writer ----------
from writer import CWriter
import small_font as font_small
import large_font as font_big
import arrows_font as font_arrows
import heart as font_heart

# ---------- Colors ----------
BLACK  = 0x0000
WHITE  = 0xFFFF
RED    = 0xF800
YELLOW = 0xFFE0

# --- Global Heart State ---
hb_state = True

# ---------- Helpers ----------
def get_device_id():
    """Reads the persistent ID created by the bootloader."""
    try:
        with open("device_id.txt", "r") as f:
            return f.read().strip()
    except Exception:
        return "N/A"

def connect_wifi(ssid, pwd, timeout_sec=12):
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if sta.isconnected():
        return True
    sta.connect(ssid, pwd)
    t0 = utime.ticks_ms()
    while not sta.isconnected():
        if utime.ticks_diff(utime.ticks_ms(), t0) > timeout_sec * 1000:
            return False
        utime.sleep(0.25)
    return True

def ntp_sync():
    try:
        ntptime.settime()
        return True
    except Exception:
        return False

def ensure_count2(endpoint: str) -> str:
    if "count=" in endpoint:
        return endpoint.replace("count=1", "count=2")
    joiner = "&" if "?" in endpoint else "?"
    return endpoint + joiner + "count=2"

def fetch_ns_entries():
    headers = {}
    if NS_TOKEN:
        headers["api-secret"] = NS_TOKEN
    ep = ensure_count2(API_ENDPOINT)
    url = NS_URL + ep
    resp = None
    try:
        resp = requests.get(url, headers=headers)
        data = resp.json()
        resp.close()
        return data
    except Exception:
        try:
            if resp: resp.close()
        except Exception: pass
        return None

def mgdl_to_units(val_mgdl: float) -> float:
    if str(DISPLAY_UNITS).lower() == "mgdl":
        return float(val_mgdl)
    return round(float(val_mgdl) / 18.0, 1)

def direction_to_arrow(direction: str) -> str:
    return {
        
        "Flat": "A",
        "SingleUp": "C",
        "DoubleUp": "CC",
        "TripleUp": "CCC",
        "SingleDown": "D",
        "DoubleDown": "DD",
        "TripleDown": "DDD",
        "FortyFiveUp": "G",
        "FortyFiveDown": "H",
        "NOT COMPUTABLE": "--",
        "NONE": "--",
            }.get(direction or "NONE", "")



def parse_entries(data):
    if not data or not isinstance(data, list) or len(data) < 1:
        return None
    cur = data[0]
    if "sgv" not in cur or "date" not in cur:
        return None
    cur_mgdl = cur["sgv"]
    cur_time_ms = cur["date"]
    direction = cur.get("direction", "NONE")
    delta_units = None
    if len(data) > 1 and isinstance(data[1], dict) and "sgv" in data[1]:
        prev_mgdl = data[1]["sgv"]
        delta_mgdl = float(cur_mgdl) - float(prev_mgdl)
        if str(DISPLAY_UNITS).lower() == "mgdl":
            delta_units = float(delta_mgdl)
        else:
            delta_units = round(delta_mgdl / 18.0, 1)
    return {
        "bg": mgdl_to_units(cur_mgdl),
        "time_ms": int(cur_time_ms),
        "direction": direction,
        "arrow": direction_to_arrow(direction),
        "delta": delta_units,
    }

def fmt_bg(bg_val: float) -> str:
    if str(DISPLAY_UNITS).lower() == "mgdl":
        return str(int(round(bg_val)))
    return "{:.1f}".format(bg_val)

def fmt_delta(delta_val) -> str:
    if delta_val is None: return ""
    if str(DISPLAY_UNITS).lower() == "mgdl":
        return "{:+.0f}".format(delta_val)
    return "{:+.1f}".format(delta_val)

def draw_screen(lcd, w_small, w_big, w_arrow, w_heart, last, hb_state): 
    
    if not last:
        # Layout constants matching bootloader
        TEXT_HEIGHT = 8
        BAR_HEIGHT = TEXT_HEIGHT + 1
        Y_POS = 240 - BAR_HEIGHT + 1
        STATUS_X = 5
        
        device_id = get_device_id()
        id_text = "ID:{}".format(device_id)

        # Draw the white background bar at the bottom
        lcd.fill_rect(0, Y_POS - 1, lcd.width, BAR_HEIGHT, WHITE)
        
        # Draw "LOADING DATA..." on the left
        lcd.text("Loading", STATUS_X, Y_POS, BLACK)
        
        # Draw the Device ID on the right
        id_x = lcd.width - (len(id_text) * 8) - 5
        lcd.text(id_text, id_x, Y_POS, BLACK)
        
        lcd.show()
        return        

    # Once data is available, then we clear the screen for the main UI
    lcd.fill(BLACK)

    W, H, M = lcd.width, lcd.height, 8
    raw_s = last["time_ms"] // 1000
    mins = max(0, int((utime.time() - raw_s) // 60))
    
    bg_val = last["bg"]            # <--- This creates the name 'bg_val'
    direction = last["direction"]  # <--- This creates the name 'direction'
    
    bg_text = fmt_bg(bg_val)
    arrow_text = last["arrow"]
    delta_text = fmt_delta(last["delta"])
    age_text = "{} {} ago".format(mins, "min" if mins == 1 else "mins")
    
    bg_text = fmt_bg(last["bg"])
    arrow_text = last["arrow"]
    delta_text = fmt_delta(last["delta"])
    age_color = RED if mins >= STALE_MIN else WHITE
    bg_color = WHITE
    if bg_val <= LOW_THRESHOLD:
        bg_color = RED
    elif bg_val >= HIGH_THRESHOLD:
        bg_color = YELLOW
    arrow_color = WHITE
    if ALERT_DOUBLE_UP and direction == "DoubleUp":
        arrow_color = YELLOW
    elif ALERT_DOUBLE_DOWN and direction == "DoubleDown":
        arrow_color = RED
    

    small_h, big_h, arrow_h, heart_h = font_small.height(), font_big.height(), font_arrows.height(), font_heart.height()
    bottom_h = max(small_h, arrow_h)

    # Calculate Y positions
    y_age = M
    y_bg = max(0, (H - big_h) // 2)
    y_bottom_base = max(0, H - bottom_h - M)
    y_arrow = y_bottom_base + (bottom_h - arrow_h) // 2
    y_delta = y_bottom_base + (bottom_h - small_h) // 2

    # Draw Age (Top)
    w_small.setcolor(age_color, BLACK)
    age_w = w_small.stringlen(age_text)
    x_age = (W - age_w) // 2
    w_small.set_textpos(lcd, y_age, x_age)
    w_small.printstring(age_text)

    # Draw Heart (Blinking)
    if hb_state:
        w_heart.setcolor(RED, BLACK)
        w_heart.set_textpos(lcd, y_age + (small_h - heart_h) // 2, x_age + age_w + 10)
        w_heart.printstring("T")

    # Draw BG (Middle)
    w_big.setcolor(bg_color, BLACK)
    x_bg = (W - w_big.stringlen(bg_text)) // 2
    w_big.set_textpos(lcd, y_bg, x_bg)
    w_big.printstring(bg_text)

    # Draw Trend Arrow (Bottom Left)
    w_arrow.setcolor(arrow_color, BLACK)
    w_arrow.set_textpos(lcd, y_arrow, M)
    w_arrow.printstring(arrow_text)

    # Draw Delta (Bottom Right)
    if delta_text:
        w_small.setcolor(WHITE, BLACK) # Delta usually stays white unless you want it to match BG
        x_delta = W - M - w_small.stringlen(delta_text)
        w_small.set_textpos(lcd, y_delta, x_delta)
        w_small.printstring(delta_text)

    lcd.show()

def main(lcd=None):
    global hb_state
    gc.collect()

    if lcd is None:
        from Pico_LCD_2_8 import LCD_2inch8
        lcd = LCD_2inch8()

    w_small = CWriter(lcd, font_small, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_small.set_spacing(5)    
    w_big = CWriter(lcd, font_big, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_arrow = CWriter(lcd, font_arrows, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_heart = CWriter(lcd, font_heart, fgcolor=0xF800, bgcolor=BLACK, verbose=False)
    w_arrow.set_spacing(10)
    
    draw_screen(lcd, w_small, w_big, w_arrow, w_heart, None, hb_state)

    connect_wifi(WIFI_SSID, WIFI_PASSWORD)
    ntp_sync()

    FETCH_MS = 15000  # Fetch every 15s to be kind to server
    last = None
    last_time_ms = None
    fetch_next = utime.ticks_ms()
    last_hb_state = not hb_state # Force first draw

    # Timer for rock-solid 1-second blink (500ms toggle)
    def tick(t):
        global hb_state
        hb_state = not hb_state

    blink_timer = Timer()
    blink_timer.init(period=500, mode=Timer.PERIODIC, callback=tick)

    while True:
        now = utime.ticks_ms()

        # 1. Update display ONLY when heart state changes
        if hb_state != last_hb_state:
            last_hb_state = hb_state
            draw_screen(lcd, w_small, w_big, w_arrow, w_heart, last, hb_state)

        # 2. Fetch BG data
        if utime.ticks_diff(now, fetch_next) >= 0:
            data = fetch_ns_entries()
            parsed = parse_entries(data)
            if parsed:
                last = parsed
                last_time_ms = parsed["time_ms"]
            fetch_next = utime.ticks_add(now, FETCH_MS)

        utime.sleep_ms(0)

if __name__ == "__main__":
    main()
