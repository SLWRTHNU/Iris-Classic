import gc
import utime
import network
import ntptime
import urequests as requests

from Pico_LCD_2_8 import LCD_2inch8
from writer import CWriter

import small_20 as font_small
import large_65_digits as font_big  # digits-only

BLACK = 0x0000
WHITE = 0xFFFF
RED   = 0xF800


def wifi_connect(ssid, password, timeout_sec=15):
    sta = network.WLAN(network.STA_IF)
    sta.active(True)

    if not sta.isconnected():
        sta.connect(ssid, password)
        t0 = utime.ticks_ms()
        while not sta.isconnected():
            if utime.ticks_diff(utime.ticks_ms(), t0) > timeout_sec * 1000:
                return None
            utime.sleep_ms(250)

    return sta.ifconfig()[0]


def ntp_sync(timeout_sec=10):
    t0 = utime.ticks_ms()
    while True:
        try:
            ntptime.settime()
            return True
        except Exception:
            if utime.ticks_diff(utime.ticks_ms(), t0) > timeout_sec * 1000:
                return False
            utime.sleep_ms(500)


def ns_fetch_latest(ns_url, ns_token, endpoint):
    if "count=" not in endpoint:
        endpoint = endpoint + ("&count=1" if "?" in endpoint else "?count=1")

    if ns_url.endswith("/"):
        ns_url = ns_url[:-1]
    url = ns_url + endpoint

    headers = {}
    if ns_token:
        headers["API-SECRET"] = ns_token

    r = None
    try:
        r = requests.get(url, headers=headers)
        if r.status_code != 200:
            return None, "HTTP " + str(r.status_code)
        data = r.json()
        if not data or not isinstance(data, list):
            return None, "bad JSON"
        return data[0], None
    except Exception as e:
        return None, repr(e)
    finally:
        try:
            if r:
                r.close()
        except Exception:
            pass


def mgdl_to_display(mgdl, units):
    # returns (value, label) but we won't display the label
    if units == "mmol":
        return round(float(mgdl) / 18.0, 1), "mmol"
    return int(mgdl), "mg/dL"


def draw_status(w_small, lcd, lines):
    lcd.fill(BLACK)
    y = 10
    for txt, color in lines:
        w_small.setcolor(color, BLACK)
        w_small.set_textpos(lcd, y, 10)
        w_small.printstring(txt)
        y += 25
    lcd.show()


def main():
    # Pull settings from config.py if present
    try:
        import config
        WIFI_SSID = getattr(config, "WIFI_SSID", "")
        WIFI_PASSWORD = getattr(config, "WIFI_PASSWORD", "")
        NS_URL = getattr(config, "NS_URL", "")
        NS_TOKEN = getattr(config, "NS_TOKEN", "")
        API_ENDPOINT = getattr(config, "API_ENDPOINT", "/api/v1/entries/sgv.json?count=1")
        DISPLAY_UNITS = getattr(config, "DISPLAY_UNITS", "mmol")  # "mmol" or "mgdl"
    except Exception:
        WIFI_SSID = ""
        WIFI_PASSWORD = ""
        NS_URL = ""
        NS_TOKEN = ""
        API_ENDPOINT = "/api/v1/entries/sgv.json?count=1"
        DISPLAY_UNITS = "mmol"

    gc.collect()
    print("A free:", gc.mem_free(), "alloc:", gc.mem_alloc(), "total:", gc.mem_free() + gc.mem_alloc())

    lcd = LCD_2inch8()

    w_small = CWriter(lcd, font_small, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_big   = CWriter(lcd, font_big,   fgcolor=WHITE, bgcolor=BLACK, verbose=False)

    gc.collect()
    print("B after LCD+writers free:", gc.mem_free(), "alloc:", gc.mem_alloc(), "total:", gc.mem_free() + gc.mem_alloc())

    draw_status(w_small, lcd, [("Iris Classic", WHITE), ("Connecting WiFi...", WHITE)])

    ip = wifi_connect(WIFI_SSID, WIFI_PASSWORD, timeout_sec=20)
    if ip is None:
        draw_status(w_small, lcd, [("Iris Classic", WHITE), ("WiFi FAILED", RED)])
        print("WiFi connect timeout")
        while True:
            utime.sleep(1)

    print("WiFi OK. IP:", ip)

    draw_status(w_small, lcd, [("Iris Classic", WHITE), ("NTP sync...", WHITE)])
    ntp_ok = ntp_sync(timeout_sec=12)
    print("NTP OK" if ntp_ok else "NTP FAILED")

    entry, err = ns_fetch_latest(NS_URL, NS_TOKEN, API_ENDPOINT)

    if err:
        draw_status(w_small, lcd, [("Iris Classic", WHITE), ("NS FAIL", RED), (err[:22], WHITE)])
        print("Nightscout fetch failed:", err)
        while True:
            utime.sleep(1)

    sgv = entry.get("sgv", None)
    if sgv is None:
        draw_status(w_small, lcd, [("Iris Classic", WHITE), ("No sgv field", RED)])
        print("No sgv field:", entry)
        while True:
            utime.sleep(1)

    bg_val, _unit = mgdl_to_display(sgv, DISPLAY_UNITS)

    # Make a display string that matches your units style
    if DISPLAY_UNITS == "mgdl":
        bg_str = str(int(bg_val))
    else:
        bg_str = "{:.1f}".format(bg_val)

    # ---- Final screen: BG only, centered ----
    lcd.fill(BLACK)

    bg_w = w_big.stringlen(bg_str)
    try:
        big_h = font_big.height()
    except Exception:
        big_h = font_big.get_ch("0")[1]

    x_bg = max(0, (lcd.width - bg_w) // 2)
    y_bg = max(0, (lcd.height - big_h) // 2)

    w_big.setcolor(WHITE, BLACK)
    w_big.set_textpos(lcd, y_bg, x_bg)
    w_big.printstring(bg_str)

    lcd.show()
    print("BG:", bg_str)

        # ---- Live loop: fetch every 5s, show "X mins ago" + big BG ----
    last_bg_str = None
    last_age_str = None

    while True:

        entry, err = ns_fetch_latest(NS_URL, NS_TOKEN, API_ENDPOINT)
        if err:
            draw_status(w_small, lcd, [("Iris Classic", WHITE), ("NS FAIL", RED), (err[:22], WHITE)])
            utime.sleep(5)
            continue

        sgv = entry.get("sgv", None)
        date_ms = entry.get("date", None)  # Nightscout timestamp in ms

        if sgv is None or date_ms is None:
            draw_status(w_small, lcd, [("Iris Classic", WHITE), ("Bad NS data", RED)])
            utime.sleep(5)
            continue

        # BG string
        bg_val, _unit = mgdl_to_display(sgv, DISPLAY_UNITS)
        if DISPLAY_UNITS == "mgdl":
            bg_str = str(int(bg_val))
        else:
            bg_str = "{:.1f}".format(bg_val)

        # Age string (minutes ago)
        now_s = utime.time()
        reading_s = int(date_ms // 1000)
        age_min = (now_s - reading_s) / 60.0
        age_min_int = int(age_min + 0.5)  # rounded minutes

        if age_min_int == 1:
            age_str = "1 min ago"
        else:
            age_str = str(age_min_int) + " mins ago"

        # Only redraw if something changed (reduces flicker)
        if bg_str != last_bg_str or age_str != last_age_str:
            lcd.fill(BLACK)

            # ---- Top line: centered "X mins ago" ----
            w_small.setcolor(WHITE, BLACK)
            top_w = w_small.stringlen(age_str)
            x_top = max(0, (lcd.width - top_w) // 2)
            y_top = 10
            w_small.set_textpos(lcd, y_top, x_top)
            w_small.printstring(age_str)

            # ---- Big BG: centered ----
            w_big.setcolor(WHITE, BLACK)
            bg_w = w_big.stringlen(bg_str)
            try:
                big_h = font_big.height()
            except Exception:
                big_h = font_big.get_ch("0")[1]

            # Center vertically, but keep room for the top line
            usable_top = y_top + 30
            usable_h = lcd.height - usable_top
            y_bg = usable_top + max(0, (usable_h - big_h) // 2)
            x_bg = max(0, (lcd.width - bg_w) // 2)

            w_big.set_textpos(lcd, y_bg, x_bg)
            w_big.printstring(bg_str)

            lcd.show()

            last_bg_str = bg_str
            last_age_str = age_str

        utime.sleep(5)



main()

