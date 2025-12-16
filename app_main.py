# app_main.py (Iris Classic 2.8")
import utime
import network
import ntptime
import urequests as requests
import gc

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
        "Flat": " A ",
        "SingleUp": " C ",
        "DoubleUp": " CC ",
        "TripleUp": " CCC ",
        "SingleDown": " D ",
        "DoubleDown": " DD ",
        "TripleDown": " DDD ",
        "FortyFiveUp": " G ",
        "FortyFiveDown": " H ",
        "NOT COMPUTABLE": " -- ",
        "NONE": " -- ",
    }.get(direction or "NONE", "?")


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
    return " {:.1f}   ".format(bg_val)


def fmt_delta(delta_val) -> str:
    if delta_val is None:
        return ""
    if str(DISPLAY_UNITS).lower() == "mgdl":
        return " {:+.0f}   ".format(delta_val)
    return " {:+.1f}   ".format(delta_val)


def draw_screen(lcd, w_small, w_big, w_arrow, state):
    lcd.fill(BLACK)

    if not state:
        lcd.text("NO DATA", 10, 10, WHITE)
        lcd.show()
        return

    W = lcd.width
    H = lcd.height
    M = 10  # margin from edges

    # ---- Build strings ----
    raw_s = state["time_ms"] // 1000
    mins = int((utime.time() - raw_s) // 60)
    if mins < 0:
        mins = 0
    age_text = "{} mins ago".format(mins)

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


def main():
    gc.collect()

    lcd = LCD()
    if hasattr(lcd, "show") is False and hasattr(lcd, "show_up"):
        lcd.show = lcd.show_up

    w_small = CWriter(lcd, font_small, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_big = CWriter(lcd, font_big, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_arrow = CWriter(lcd, font_arrows, fgcolor=WHITE, bgcolor=BLACK, verbose=False)

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
