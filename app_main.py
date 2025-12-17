# app_main.py (Iris Classic 2.8")
import utime
import network
import ntptime
import urequests as requests
import gc
from Pico_LCD_2_8 import PICO_RP2040, PICO_RP2350
import machine

# ---------- Config ----------
# Must exist after your config portal saves it.
# Required names:
#   WIFI_SSID, WIFI_PASSWORD, NS_URL, NS_TOKEN, API_ENDPOINT, DISPLAY_UNITS
from config import *  # noqa

# ---------- Display driver ----------
try:
    from Pico_LCD_2_8 import LCD_2inch8 as LCD
except ImportError:
    raise RuntimeError("Missing Pico_LCD_2_8.py")

# Ensure the driver has .show() (your driver flushes via show_up()).
if not hasattr(LCD, "show") and hasattr(LCD, "show_up"):
    def _show(self):
        self.show_up()
    LCD.show = _show

# ---------- Fonts / Writer ----------
from writer import CWriter
import small_font as font_small
import large_font as font_big  # digits-only big font
import arrows_font as font_arrows




# ---------- Colors ----------
BLACK = 0x0000
WHITE = 0xFFFF

# ---------- Helpers ----------
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
    # Make sure we always request 2 readings so delta works.
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
            if resp:
                resp.close()
        except Exception:
            pass
        return None


def mgdl_to_units(val_mgdl: float) -> float:
    if str(DISPLAY_UNITS).lower() == "mgdl":
        return float(val_mgdl)
    return round(float(val_mgdl) / 18.0, 1)


def direction_to_arrow(direction: str) -> str:
    # These letters map to glyphs in your arrows_font font.
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

    # Delta from the previous reading if present
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
    if delta_val is None:
        return ""
    if str(DISPLAY_UNITS).lower() == "mgdl":
        return "{:+.0f}".format(delta_val)
    return "{:+.1f}".format(delta_val)


def draw_screen(lcd, w_small, w_big, w_arrow, state):
    lcd.fill(BLACK)

    if not state:
        lcd.text("NO DATA", 10, 10, WHITE)
        lcd.show()
        return

    W = lcd.width
    H = lcd.height
    M = 8  # margin from edges

    # ---- Build strings ----
    raw_s = state["time_ms"] // 1000
    mins = int((utime.time() - raw_s) // 60)
    if mins < 0:
       mins = 0

    unit = "min" if mins == 1 else "mins"
    age_text = "{} {} ago".format(mins, unit)


    bg_text = fmt_bg(state["bg"])
    arrow_text = state["arrow"]
    delta_text = fmt_delta(state["delta"])

    # ---- Font heights ----
    small_h = font_small.height()
    big_h = font_big.height()
    arrow_h = font_arrows.height()
    bottom_h = max(small_h, arrow_h)

    # ---- Anchored Y positions ----
    y_age = M
    y_bg = max(0, (H - big_h) // 2)
    y_bottom_base = max(0, H - bottom_h - M)

    # Center each bottom item within the bottom row height
    y_arrow = y_bottom_base + (bottom_h - arrow_h) // 2
    y_delta = y_bottom_base + (bottom_h - small_h) // 2

    # ---- Draw age (top, centered) ----
    w_small.setcolor(WHITE, BLACK)
    x_age = max(0, (W - w_small.stringlen(age_text)) // 2)
    w_small.set_textpos(lcd, y_age, x_age)
    w_small.printstring(age_text)

    # ---- Draw BG (centered) ----
    w_big.setcolor(WHITE, BLACK)
    x_bg = max(0, (W - w_big.stringlen(bg_text)) // 2)
    w_big.set_textpos(lcd, y_bg, x_bg)
    w_big.printstring(bg_text)

    # ---- Draw trend (bottom-left) ----
    w_arrow.setcolor(WHITE, BLACK)
    w_arrow.set_textpos(lcd, y_arrow, M)
    w_arrow.printstring(arrow_text)


    # ---- Draw delta (bottom-right) ----
    if delta_text:
        w_small.setcolor(WHITE, BLACK)
        x_delta = max(0, W - M - w_small.stringlen(delta_text))
        w_small.set_textpos(lcd, y_delta, x_delta)
        w_small.printstring(delta_text)

    lcd.show()

# ---------------- Touch RUN long-press factory reset ----------------

# Fill these in using the numbers you recorded in Step 1
RUN_X0 = 245
RUN_Y0 = 190
RUN_X1 = 319
RUN_Y1 = 239

RUN_HOLD_MS = 5000

_run_hold_start_ms = None
_run_last_shown = None

def _touch_xy_on_screen(lcd):
    raw = lcd.touch_get()
    if not raw:
        return None

    if PICO_RP2040:
        x = 320 - int((raw[1] - 430) * 320 / 3270)
        y = int((raw[0] - 430) * 240 / 3270)
    else:
        x = 320 - int((raw[1] - 4500) * 320 / 3360)
        y = int((raw[0] - 4480) * 240 / 3500)

    if x < 0: x = 0
    if x > 319: x = 319
    if y < 0: y = 0
    if y > 239: y = 239
    return x, y

def _in_run_button(x, y):
    return (RUN_X0 <= x <= RUN_X1) and (RUN_Y0 <= y <= RUN_Y1)

def _center_x(writer, text, screen_w):
    return (screen_w - writer.stringlen(text)) // 2

def _draw_reset_countdown(lcd, w_small, seconds_left):
    # Black screen
    lcd.fill(0x0000)

    # Two centered lines:
    # "Resetting in"
    # "5" (or 4..0)
    line1 = "Resetting in"
    line2 = str(seconds_left)

    # vertical centering for 2 lines
    line_h = w_small.height
    gap = 6
    total_h = (line_h * 2) + gap
    y0 = (lcd.height - total_h) // 2

    w_small.setcolor(0xFFFF, 0x0000)  # white on black

    x1 = _center_x(w_small, line1, lcd.width)
    w_small.set_textpos(lcd, y0, x1)
    w_small.printstring(line1)

    x2 = _center_x(w_small, line2, lcd.width)
    w_small.set_textpos(lcd, y0 + line_h + gap, x2)
    w_small.printstring(line2)

    lcd.show()

def _do_factory_reset():
    # Keep this list conservative so you do not brick the device.
    # This forces setup again because config.py is gone.
    files_to_delete = [
        "config.py",
    ]

    for fn in files_to_delete:
        try:
            os.remove(fn)
        except OSError:
            pass

def check_touch_factory_reset(lcd, w_small):
    """
    Call this often (every 20 to 100ms).
    If the RUN touch area is held for 5 seconds:
      - show countdown 5..0
      - delete config.py
      - reset the device
    """
    global _run_hold_start_ms, _run_last_shown

    pt = _touch_xy_on_screen(lcd)
    now = utime.ticks_ms()

    if pt and _in_run_button(pt[0], pt[1]):
        if _run_hold_start_ms is None:
            _run_hold_start_ms = now
            _run_last_shown = None

        elapsed = utime.ticks_diff(now, _run_hold_start_ms)

        # Countdown display (5..0)
        remaining = 5 - (elapsed // 1000)
        if remaining < 0:
            remaining = 0

        if remaining != _run_last_shown:
            _draw_reset_countdown(lcd, w_small, remaining)
            _run_last_shown = remaining

        if elapsed >= RUN_HOLD_MS:
            _draw_reset_countdown(lcd, w_small, 0)
            utime.sleep_ms(250)
            _do_factory_reset()
            machine.reset()
    else:
        _run_hold_start_ms = None
        _run_last_shown = None

def sleep_ms_with_reset_check(lcd, w_small, total_ms):
    """
    Use this instead of utime.sleep_ms(total_ms) so the 5-second hold works
    even while your code is "sleeping".
    """
    step = 50
    t0 = utime.ticks_ms()
    while utime.ticks_diff(utime.ticks_ms(), t0) < total_ms:
        check_touch_factory_reset(lcd, w_small)
        utime.sleep_ms(step)


def main(lcd=None):
    if lcd is None:
        from Pico_LCD_2_8 import LCD_2inch8
        lcd = LCD_2inch8()

    # continue with your normal app code using lcd...

    gc.collect()

    lcd = LCD()
    if hasattr(lcd, "show") is False and hasattr(lcd, "show_up"):
        lcd.show = lcd.show_up

    w_small = CWriter(lcd, font_small, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_small.set_spacing(6)    
    w_big = CWriter(lcd, font_big, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_arrow = CWriter(lcd, font_arrows, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_arrow.set_spacing(10)

    # Connect Wi-Fi once
    if not connect_wifi(WIFI_SSID, WIFI_PASSWORD):
        # Show something, but keep running (you can decide later what to do here)
        lcd.fill(BLACK)
        lcd.text("WiFi failed", 10, 10, WHITE)
        lcd.show()
        utime.sleep(2)
        

    ntp_sync()

    last = None
    last_time_ms = None

    while True:
        # Try fetch
        data = fetch_ns_entries()
        parsed = parse_entries(data)

        # Only replace "last" when we got valid data
        # (so the screen never gets stuck on "Fetching BG")
        if parsed and parsed.get("time_ms") != last_time_ms:
            last = parsed
            last_time_ms = parsed["time_ms"]

        # Always redraw using the last known value (age updates)
        draw_screen(lcd, w_small, w_big, w_arrow, last)

        utime.sleep(5)


# Run
main()
