#app_main.py

from machine import Pin
import utime
hb_state = True
wdt = None
factory_reset_exit_requested = False

BUZ = Pin(17, Pin.IN, Pin.PULL_UP)
utime.sleep_ms(10)
BUZ.init(Pin.OUT, value=1)  # Active-low: 1 = OFF

import gc
import network
import uasyncio as asyncio

gc.collect()

# ---------- Config ----------
import config

def cfg(name, default):
    return getattr(config, name, default)

WIFI_SSID     = cfg("WIFI_SSID", "")
WIFI_PASSWORD = cfg("WIFI_PASSWORD", "")
NS_URL        = cfg("NS_URL", "")
NS_TOKEN      = cfg("API_SECRET", "")
API_ENDPOINT  = cfg("API_ENDPOINT", "/api/v1/entries/sgv.json?count=2")
DISPLAY_UNITS = cfg("UNITS", "mmol")
DATA_SOURCE    = cfg("DATA_SOURCE", "nightscout")   # "nightscout" or "dexcom_share"
DEXCOM_USERNAME = cfg("DEXCOM_USERNAME", "")
DEXCOM_PASSWORD = cfg("DEXCOM_PASSWORD", "")
DEXCOM_REGION   = cfg("DEXCOM_REGION", "us")        # "us" or "ous" (outside US)


LOW_THRESHOLD  = float(cfg("THRESHOLD_LOW", 4.0))
HIGH_THRESHOLD = float(cfg("THRESHOLD_HIGH", 11.0))
STALE_MIN      = int(cfg("STALE_MINS", 7))

ALERT_DOUBLE_UP   = cfg("ALERT_DOUBLE_UP", True)
ALERT_DOUBLE_DOWN = cfg("ALERT_DOUBLE_DOWN", True)

UNIX_2000_OFFSET = 946684800
last = None          # replaces main() local "last"


# ---------- Display driver ----------
from display_2inch import lcd_st7789 as LCD_Driver

# ---------- Fonts / Writer ----------
from writer import CWriter
import small_font as font_small
import age_small_font as age_font_small
import arrows_font as font_arrows
import heart as font_heart
import delta as font_delta
import config_font as font_config
import mini_digits as big_digits
from big_digits_draw import draw_big_text
BIG_DIGITS_HEIGHT = big_digits.HEIGHT  # 98


# Memory monitoring removed - not needed with 8MB RAM
    
# ---------- Colors ----------
YELLOW = 0xFFE0
RED    = 0xF800
GREEN  = 0x07E0
BLACK  = 0x0000
WHITE  = 0xFFFF

# IMPORTANT: you need sta defined before connect_wifi() uses it
sta = None

# Memory monitoring removed - not needed with 8MB RAM

BTN_STOP = Pin(2, Pin.IN, Pin.PULL_UP)

buzzer_stop_requested = False

def request_buzzer_stop():
    global buzzer_mode, buzzer_snooze_until, last_mild_beep_time

    # If no alert is active, button does nothing
    if buzzer_mode == 0:
        return

    # Stop everything
    BUZ.value(1)
    buzzer_mode = 0

    now = utime.ticks_ms()

    # Snooze ALL alerts for 10 minutes
    buzzer_snooze_until = utime.ticks_add(now, BUZZER_SNOOZE_MS)

    # Restart mild timer so it won't fire immediately after snooze ends
    last_mild_beep_time = now


# ESP32-S3: Factory reset now uses onboard BOOT button (in boot.py)
# No external button needed
# FACTORY_BTN = Pin(16, Pin.IN, Pin.PULL_UP)


# --- Logo Config ---
LOGO_FILE = "logo.bin"
LOGO_W = 320
LOGO_H = 240

def show_logo(lcd):
    expected = LOGO_W * LOGO_H * 2  # 307200
    try:
        import os
        st = os.stat(LOGO_FILE)
        if st[6] == expected:
            with open(LOGO_FILE, "rb") as f:
                f.readinto(lcd.buffer)
            lcd.show()
            return True
    except:
        pass
    return False


# ---------- Helpers ----------

def _show_rect(lcd, x, y, w, h):
    global _DIRTY
    if _BATCHING:
        _DIRTY = _union_rect(_DIRTY, (x, y, w, h))
        return

    if hasattr(lcd, "show_rect"):
        lcd.show_rect(x, y, w, h)
    else:
        lcd.show()




wifi_ok = False

# ---------- Batched screen flush ----------
_BATCHING = False
_DIRTY = None  # (x, y, w, h)

def _begin_batch():
    global _BATCHING, _DIRTY
    _BATCHING = True
    _DIRTY = None

def _end_batch(lcd):
    global _BATCHING, _DIRTY
    _BATCHING = False
    if not _DIRTY:
        return
    x, y, w, h = _DIRTY
    if hasattr(lcd, "show_rect"):
        lcd.show_rect(x, y, w, h)
    else:
        lcd.show()
    _DIRTY = None


def connect_wifi(ssid, password, max_attempts=2):
    import utime
    import network

    global sta, wdt

    # Feed before starting
    if wdt:
        wdt.feed()

    # Hard reset the STA interface to avoid EPERM
    if sta is not None:
        try:
            sta.active(False)
            utime.sleep_ms(200)
        except Exception:
            pass

    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    utime.sleep_ms(200)
    
    if wdt:
        wdt.feed()

    for attempt in range(1, max_attempts + 1):
        try:
            if sta.isconnected():
                return True

            sta.connect(ssid, password)
            
            if wdt:
                wdt.feed()

            start = utime.ticks_ms()
            while not sta.isconnected():
                status = sta.status()
                elapsed = utime.ticks_diff(utime.ticks_ms(), start)

                # Feed watchdog every second during connection
                if elapsed % 1000 < 100 and wdt:
                    wdt.feed()

                if elapsed > 50000:
                    break

                utime.sleep_ms(1000)

            if sta.isconnected():
                return True

        except OSError as e:
            pass

        try:
            sta.disconnect()
        except Exception:
            pass
        
        if wdt:
            wdt.feed()
        
        utime.sleep_ms(800)

    return False



def now_unix_s():
    t = utime.time()
    return t + UNIX_2000_OFFSET if t < 1200000000 else t

def ntp_sync():
    try:
        import ntptime
        before = now_unix_s()
        ntptime.settime()
        after = now_unix_s()
        drift = after - before
        return True
    except Exception as e:
        return False


def ensure_count2(endpoint: str) -> str:
    # Force count=2 exactly (Nightscout uses count=)
    if "count=" in endpoint:
        # replace any count=NUMBER with count=2
        import re
        return re.sub(r"count=\d+", "count=2", endpoint)
    joiner = "&" if "?" in endpoint else "?"
    return endpoint + joiner + "count=2"


def fetch_ns_text():
    import usocket
    import network
    import utime
    import ssl

    global wdt
    gc.collect()
    
    if wdt:
        wdt.feed()  # Feed before network operations
    
    # Must have WiFi before DNS/getaddrinfo, or it can block forever
    try:
        wlan = network.WLAN(network.STA_IF)
        if not wlan.active() or not wlan.isconnected():
            return None
    except Exception as e:
        return None

    if not NS_URL:
        return None

    # Build URL (may be http or https depending on NS_URL)
    url = NS_URL + ensure_count2(API_ENDPOINT)

    MIN_FREE = 20000 # Reduced slightly to be less aggressive
    free = gc.mem_free()
    if free < MIN_FREE:
        return None
    

    def _parse_url(u):
        # Returns (scheme, host, port, path)
        if u.startswith("http://"):
            scheme = "http"
            rest = u[7:]
            default_port = 80
        elif u.startswith("https://"):
            scheme = "https"
            rest = u[8:]
            default_port = 443
        else:
            raise ValueError("URL must start with http:// or https://")

        # split host[:port] and /path
        if "/" in rest:
            hostport, path = rest.split("/", 1)
            path = "/" + path
        else:
            hostport = rest
            path = "/"

        if ":" in hostport:
            host, port_s = hostport.split(":", 1)
            port = int(port_s)
        else:
            host = hostport
            port = default_port

        return scheme, host, port, path

    def _one_request(u, max_body=2048):
        scheme, host, port, path = _parse_url(u)

        s = None
        try:
            if wdt:
                wdt.feed()  # Feed before DNS lookup
            gc.collect()

            addr = usocket.getaddrinfo(host, port)[0][-1]
            s = usocket.socket()
            s.settimeout(2)
            s.connect(addr)

            # TLS if https
            if scheme == "https":
                try:
                    import ssl
                    s = ssl.wrap_socket(s, server_hostname=host)
                except Exception as e:
                    try:
                        s.close()
                    except:
                        pass
                    return None, None, None

            if wdt:
                wdt.feed()  # Feed after connection established

            headers = [
                "GET {} HTTP/1.1".format(path),
                "Host: {}".format(host),
                "Accept: application/json",
                "Connection: close",
            ]
            if NS_TOKEN:
                headers.append("api-secret: {}".format(NS_TOKEN))
            req = "\r\n".join(headers) + "\r\n\r\n"
            s.send(req.encode("utf-8"))

            # Read response with a cap (avoid ENOMEM)
            buf = bytearray()
            CAP = max_body + 512  # header + body cap
            t_recv0 = utime.ticks_ms()
            RECV_BUDGET_MS = 1200

            while True:
                if wdt:
                    wdt.feed()
                if utime.ticks_diff(utime.ticks_ms(), t_recv0) > RECV_BUDGET_MS:
                    break
                chunk = s.recv(256)
                if not chunk:
                    break
                if (len(buf) + len(chunk)) > CAP:
                    # append only what fits, then stop
                    take = CAP - len(buf)
                    if take > 0:
                        buf.extend(chunk[:take])
                    break
                buf.extend(chunk)


        except Exception as e:
            pass
            return None, None, None

        finally:
            try:
                if s:
                    s.close()
            except:
                pass

        # Parse status line + headers/body split
        raw = bytes(buf)
        sep = raw.find(b"\r\n\r\n")
        if sep == -1:
            return None, None, None

        head = raw[:sep].decode("utf-8", "ignore")
        body = raw[sep + 4 : sep + 4 + max_body]

        # status code
        status = None
        try:
            status_line = head.split("\r\n", 1)[0]
            parts = status_line.split(" ")
            if len(parts) >= 2:
                status = int(parts[1])
        except Exception as e:
            pass

        # headers dict (lowercased keys)
        hdrs = {}
        try:
            for line in head.split("\r\n")[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    hdrs[k.strip().lower()] = v.strip()
        except:
            pass

        return status, hdrs, body

    try:
        # 1) first request (may redirect)
        status, hdrs, body = _one_request(url, max_body=2048)
        if status is None:
            return None

        # follow one redirect
        if status in (301, 302, 303, 307, 308):
            loc = (hdrs or {}).get("location")
            if not loc:
                return None
            status, hdrs, body = _one_request(loc, max_body=2048)
            if status is None:
                return None

        if status != 200:
            return None

        if not body:
            return None

        return body.decode("utf-8", "ignore")

    finally:
        gc.collect()



def mgdl_to_units(val_mgdl: float) -> float:
    try:
        if str(DISPLAY_UNITS).lower() == "mgdl":
            return float(val_mgdl)
        return round(float(val_mgdl) / 18.0, 1)
    except:
        return 0.0

def direction_to_arrow(direction: str) -> str:
    return {
        "Flat": "J",
        "SingleUp": "O",
        "DoubleUp": "OO",
        "SingleDown": "P",
        "DoubleDown": "PP",
        "FortyFiveUp": "L",
        "FortyFiveDown": "N",
        "NOT COMPUTABLE": "--",
        "NONE": "--",
    }.get(direction or "NONE", "")

def _find_int_after(s, key, start=0):
    i = s.find(key, start)
    if i < 0:
        return None, -1
    i += len(key)

    # Allow spaces, tabs, CR, LF
    while i < len(s) and s[i] in " \t\r\n":
        i += 1

    j = i

    # Optional minus sign
    if j < len(s) and s[j] == "-":
        j += 1

    while j < len(s) and s[j].isdigit():
        j += 1

    if j == i or (j == i + 1 and s[i] == "-"):
        return None, -1

    return int(s[i:j]), j


def _find_str_after(s, key, start=0):
    i = s.find(key, start)
    if i < 0:
        return None, -1
    i += len(key)

    # Allow spaces, tabs, CR, LF before the quote
    while i < len(s) and s[i] in " \t\r\n":
        i += 1

    q1 = s.find('"', i)
    if q1 < 0:
        return None, -1
    q2 = s.find('"', q1 + 1)
    if q2 < 0:
        return None, -1

    return s[q1 + 1:q2], q2 + 1


# ---------------------------------------------------------------------------
# Dexcom Share API
# ---------------------------------------------------------------------------
_DEXCOM_APP_ID   = "d8665ade-9673-4e27-9ff6-92db4ce13d13"
_DEXCOM_TREND_MAP = {
    1: "DoubleUp", 2: "SingleUp", 3: "FortyFiveUp", 4: "Flat",
    5: "FortyFiveDown", 6: "SingleDown", 7: "DoubleDown",
}
_dexcom_session = None  # cached session GUID


def _dexcom_post(host, path, json_body=""):
    """HTTPS POST to Dexcom Share host. Returns (status, body_str) or (None, None)."""
    import usocket, ssl, utime

    body_bytes = json_body.encode("utf-8") if json_body else b""
    req = (
        "POST {} HTTP/1.1\r\n"
        "Host: {}\r\n"
        "Content-Type: application/json\r\n"
        "Accept: application/json\r\n"
        "User-Agent: Dexcom Share/3.0.2.11 CFNetwork/711.2.23 Darwin/14.0.0\r\n"
        "Content-Length: {}\r\n"
        "Connection: close\r\n\r\n"
    ).format(path, host, len(body_bytes)).encode("utf-8") + body_bytes

    s = None
    try:
        if wdt:
            wdt.feed()
        print("[Dexcom] POST", host, path[:50])
        addr = usocket.getaddrinfo(host, 443)[0][-1]
        s = usocket.socket()
        s.settimeout(10)
        s.connect(addr)
        s = ssl.wrap_socket(s, server_hostname=host)
        if wdt:
            wdt.feed()
        s.send(req)

        buf = bytearray()
        t0 = utime.ticks_ms()
        while utime.ticks_diff(utime.ticks_ms(), t0) < 8000:
            if wdt:
                wdt.feed()
            try:
                chunk = s.recv(256)
            except OSError:
                break
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > 2048:
                break

        raw = bytes(buf)
        sep = raw.find(b"\r\n\r\n")
        if sep < 0:
            print("[Dexcom] No HTTP header in response, raw[:80]:", bytes(buf)[:80])
            return None, None

        head = raw[:sep].decode("utf-8", "ignore")
        body_raw = raw[sep + 4:].decode("utf-8", "ignore")
        parts = head.split("\r\n", 1)[0].split(" ")
        status = int(parts[1]) if len(parts) >= 2 else None

        # Decode chunked transfer encoding if used
        if "transfer-encoding: chunked" in head.lower():
            body_str = ""
            pos = 0
            while pos < len(body_raw):
                nl = body_raw.find("\r\n", pos)
                if nl < 0:
                    break
                try:
                    chunk_len = int(body_raw[pos:nl], 16)
                except Exception:
                    break
                if chunk_len == 0:
                    break
                body_str += body_raw[nl + 2: nl + 2 + chunk_len]
                pos = nl + 2 + chunk_len + 2  # skip trailing \r\n
        else:
            body_str = body_raw

        print("[Dexcom] HTTP", status, "| body[:100]:", body_str[:100])
        return status, body_str

    except Exception as e:
        print("[Dexcom] POST error:", e)
        return None, None
    finally:
        try:
            if s:
                s.close()
        except:
            pass


def fetch_dexcom():
    """Fetch latest glucose readings from Dexcom Share. Returns same dict as parse_entries_from_text."""
    global _dexcom_session

    if not DEXCOM_USERNAME or not DEXCOM_PASSWORD:
        return None

    host = "shareous1.dexcom.com" if DEXCOM_REGION.lower() == "ous" else "share2.dexcom.com"
    login_path = "/ShareWebServices/Services/General/LoginPublisherAccountByName"
    read_path_tmpl = (
        "/ShareWebServices/Services/Publisher/ReadPublisherLatestGlucoseValues"
        "?sessionId={}&minutes=1440&maxCount=2"
    )

    def _login():
        global _dexcom_session
        body = '{{"accountName":"{}","password":"{}","applicationId":"{}"}}'.format(
            DEXCOM_USERNAME, DEXCOM_PASSWORD, _DEXCOM_APP_ID
        )
        print("[Dexcom] Logging in as:", DEXCOM_USERNAME)
        status, resp = _dexcom_post(host, login_path, body)
        if status == 200 and resp:
            sid = resp.strip().strip('"')
            if len(sid) > 10 and sid != "00000000-0000-0000-0000-000000000000":
                _dexcom_session = sid
                print("[Dexcom] Login OK, session:", sid[:8], "...")
                return True
            if sid == "00000000-0000-0000-0000-000000000000":
                print("[Dexcom] Login rejected: invalid credentials (check username/password)")
            else:
                print("[Dexcom] Login 200 but unexpected body:", resp[:80])
        else:
            print("[Dexcom] Login failed, status:", status)
        _dexcom_session = None
        return False

    for _attempt in range(2):
        if not _dexcom_session:
            if not _login():
                print("[Dexcom] Aborting after failed login")
                return None

        status, resp = _dexcom_post(host, read_path_tmpl.format(_dexcom_session))

        if status in (None, 401, 500):
            print("[Dexcom] Readings request returned", status, "- will re-login")
            _dexcom_session = None  # session expired; retry with fresh login
            continue

        if status != 200 or not resp:
            return None

        resp = resp.strip()
        if not resp or resp[0] != '[':
            return None

        # Parse first entry: {"DT":"/Date(ms+tz)/","Trend":4,"Value":120,...}
        cur_val, p1 = _find_int_after(resp, '"Value":')
        if cur_val is None:
            return None

        cur_trend, _ = _find_int_after(resp, '"Trend":')

        # Timestamp: find first /Date(ms...)/ pattern
        time_ms = 0
        dt_start = resp.find("/Date(")
        if dt_start >= 0:
            dt_end = resp.find(")", dt_start)
            dt_inner = resp[dt_start + 6:dt_end]   # e.g. "1709123456789+0000"
            try:
                sign = dt_inner.find("+")
                if sign < 0:
                    sign = dt_inner.find("-")
                time_ms = int(dt_inner[:sign] if sign > 0 else dt_inner)
            except Exception:
                time_ms = 0

        prev_val, _ = _find_int_after(resp, '"Value":', p1)
        delta = None
        if prev_val is not None:
            diff = float(cur_val) - float(prev_val)
            delta = diff if str(DISPLAY_UNITS).lower() == "mgdl" else diff / 18.0

        direction = _DEXCOM_TREND_MAP.get(cur_trend, "NONE") if cur_trend is not None else "NONE"
        return {
            "bg":        mgdl_to_units(cur_val),
            "time_ms":   time_ms,
            "direction": direction,
            "arrow":     direction_to_arrow(direction),
            "delta":     delta,
        }

    return None


def fetch_and_parse():
    """Unified data fetch: routes to Nightscout or Dexcom Share based on DATA_SOURCE config."""
    if DATA_SOURCE == "dexcom_share":
        return fetch_dexcom()
    # Default: Nightscout
    txt = fetch_ns_text()
    return parse_entries_from_text(txt)


# ---------------------------------------------------------------------------

def parse_entries_from_text(txt):
    if not txt:
        return None

    cur_sgv, p = _find_int_after(txt, '"sgv":')
    if cur_sgv is None:
        return None

    cur_mills, p2 = _find_int_after(txt, '"mills":', p)
    if cur_mills is None:
        cur_mills, _ = _find_int_after(txt, '"date":', p)

    direction, p3 = _find_str_after(txt, '"direction":', 0)  # search from start, not p, so field order doesn't matter

    # Fallback: some CGM bridges omit "direction" and only send a numeric "trend"
    if not direction or direction == "NONE":
        trend_num, _ = _find_int_after(txt, '"trend":', 0)
        if trend_num is not None:
            direction = {1:"DoubleUp", 2:"SingleUp", 3:"FortyFiveUp", 4:"Flat",
                         5:"FortyFiveDown", 6:"SingleDown", 7:"DoubleDown"}.get(trend_num)

    prev_sgv, _ = _find_int_after(txt, '"sgv":', p)

    delta_units = None
    if prev_sgv is not None:
        diff = float(cur_sgv) - float(prev_sgv)
        if str(DISPLAY_UNITS).lower() == "mgdl":
            delta_units = diff
        else:
            delta_units = diff / 18.0

    return {
        "bg": mgdl_to_units(cur_sgv),
        "time_ms": int(cur_mills or 0),
        "direction": direction or "NONE",
        "arrow": direction_to_arrow(direction),
        "delta": delta_units,
    }

def fmt_bg(bg_val) -> str:
    if bg_val is None:
        return "---"
    try:
        if str(DISPLAY_UNITS).lower() == "mgdl":
            return str(int(round(bg_val)))
        return "{:.1f}".format(float(bg_val))
    except:
        return "ERR"

def fmt_delta(delta_val) -> str:
    if delta_val is None:
        return ""
    return "{:+.0f}".format(delta_val) if str(DISPLAY_UNITS).lower() == "mgdl" else "{:+.1f}".format(delta_val)

# ============================
# PARTIAL UPDATE DRAW SECTION
# ============================

class ScreenState:
    def __init__(self):
        self.factory_mode = False
        self.age_text = None
        self.age_color = None

        self.bg_text = None
        self.bg_color = None

        self.arrow_text = None
        self.arrow_color = None

        self.delta_text = None
        self.heart_on = None

        self.last_have_data = False
        self.wifi_lost = False



def _clear_rect(lcd, x, y, w, h, color=BLACK):
    if x < 0:
        w += x
        x = 0
    if y < 0:
        h += y
        y = 0
    if x + w > lcd.width:
        w = lcd.width - x
    if y + h > lcd.height:
        h = lcd.height - y
    if w <= 0 or h <= 0:
        return
    lcd.fill_rect(x, y, w, h, color)

def _bbox_text(wr, text, x, y, pad=2):
    tw = wr.stringlen(text)
    th = wr.font.height()
    return (x - pad, y - pad, tw + pad * 2, th + pad * 2)


def _union_rect(r1, r2):
    # rect = (x, y, w, h)
    if not r1:
        return r2
    if not r2:
        return r1
    x1, y1, w1, h1 = r1
    x2, y2, w2, h2 = r2
    ax1, ay1, ax2, ay2 = x1, y1, x1 + w1, y1 + h1
    bx1, by1, bx2, by2 = x2, y2, x2 + w2, y2 + h2
    ux1 = ax1 if ax1 < bx1 else bx1
    uy1 = ay1 if ay1 < by1 else by1
    ux2 = ax2 if ax2 > bx2 else bx2
    uy2 = ay2 if ay2 > by2 else by2
    return (ux1, uy1, ux2 - ux1, uy2 - uy1)



def _draw_age_if_changed(lcd, w_age_small, new_text, new_color, st, y_age):
    W = lcd.width
    new_w = w_age_small.stringlen(new_text)
    x_new = (W - new_w) // 2

    if st.age_text == new_text and st.age_color == new_color:
        return

    old_bbox = None
    if st.age_text is not None:
        old_w = w_age_small.stringlen(st.age_text)
        x_old = (W - old_w) // 2
        old_bbox = _bbox_text(w_age_small, st.age_text, x_old, y_age, pad=3)

    new_bbox = _bbox_text(w_age_small, new_text, x_new, y_age, pad=3)
    dirty = _union_rect(old_bbox, new_bbox)

    # One clear + draw, then ONE flush
    _clear_rect(lcd, dirty[0], dirty[1], dirty[2], dirty[3], BLACK)

    w_age_small.setcolor(new_color, BLACK)
    w_age_small.set_textpos(lcd, y_age, x_new)
    w_age_small.printstring(new_text)

    _show_rect(lcd, dirty[0], dirty[1], dirty[2], dirty[3])

    st.age_text = new_text
    st.age_color = new_color


def _draw_heart_if_changed(lcd, w_heart, heart_on, st, x_heart, y_heart, pad=2):
    if st.heart_on == heart_on:
        return

    heart_w = w_heart.stringlen("T")
    heart_h = w_heart.font.height()

    dirty = (x_heart - pad, y_heart - pad, heart_w + pad * 2, heart_h + pad * 2)

    _clear_rect(lcd, dirty[0], dirty[1], dirty[2], dirty[3], BLACK)

    if heart_on:
        w_heart.setcolor(RED, BLACK)
        w_heart.set_textpos(lcd, y_heart, x_heart)
        w_heart.printstring("T")

    _show_rect(lcd, dirty[0], dirty[1], dirty[2], dirty[3])
    st.heart_on = heart_on


def _big_text_width(s, spacing=2):
    w = 0
    for ch in s:
        g = big_digits.GLYPHS.get(ch)
        if g:
            w += g[0] + spacing
    return max(0, w - spacing)

def _draw_bg_if_changed(lcd, new_text, new_color, st, y_bg):
    W = lcd.width
    H = BIG_DIGITS_HEIGHT
    spacing = 2

    new_w = _big_text_width(new_text, spacing=spacing)
    x_new = (W - new_w) // 2

    if st.bg_text == new_text and st.bg_color == new_color:
        return

    old_bbox = None
    if st.bg_text is not None:
        old_w = _big_text_width(st.bg_text, spacing=spacing)
        x_old = (W - old_w) // 2
        old_bbox = (x_old - 6, y_bg - 6, old_w + 12, H + 12)

    new_bbox = (x_new - 6, y_bg - 6, new_w + 12, H + 12)
    dirty = _union_rect(old_bbox, new_bbox)

    # Clear the union area once
    _clear_rect(lcd, dirty[0], dirty[1], dirty[2], dirty[3], BLACK)

    # Draw AND flush once (draw_big_text flushes)
    draw_big_text(lcd, new_text, x_new, y_bg, fg=new_color, bg=BLACK, spacing=spacing, flush=False)
    # Mark the dirty area so the final batch flush updates it
    _show_rect(lcd, dirty[0], dirty[1], dirty[2], dirty[3])


    st.bg_text = new_text
    st.bg_color = new_color





def _draw_arrow_if_changed(lcd, w_arrow, new_text, new_color, st, x_arrow, y_arrow, x_offset=0, y_offset=0):
    #new_text = "O"
    
    x_arrow = x_arrow + x_offset
    y_arrow = y_arrow + y_offset
    
    if st.arrow_text == new_text and st.arrow_color == new_color:
        return

    old_bbox = None
    if st.arrow_text is not None:
        old_bbox = _bbox_text(w_arrow, st.arrow_text, x_arrow, y_arrow, pad=3)

    new_bbox = _bbox_text(w_arrow, new_text, x_arrow, y_arrow, pad=3)
    dirty = _union_rect(old_bbox, new_bbox)

    _clear_rect(lcd, dirty[0], dirty[1], dirty[2], dirty[3], BLACK)

    w_arrow.setcolor(new_color, BLACK)
    w_arrow.set_textpos(lcd, y_arrow, x_arrow)
    w_arrow.printstring(new_text)

    _show_rect(lcd, dirty[0], dirty[1], dirty[2], dirty[3])

    st.arrow_text = new_text
    st.arrow_color = new_color


def _draw_delta_if_changed(lcd, w_small, w_delta_icon, new_delta_text, st, y_delta, right_margin=4):
    if st.delta_text == new_delta_text:
        return

    W = lcd.width
    gap = 12
    v_offset = -8
    NUM_Y_OFFSET = -5   # negative = up, positive = down
    NUM_X_OFFSET = -5    # negative = left, positive = right


    # Compute old box
    old_bbox = None
    if st.delta_text:
        old_sign = st.delta_text[0]
        old_num = st.delta_text[1:] if len(st.delta_text) > 1 else ""

        num_w = w_small.stringlen(old_num)
        sign_w = w_delta_icon.stringlen(old_sign)
        total_w = sign_w + gap + num_w

        h = max(w_small.font.height(), w_delta_icon.font.height())
        x = W - right_margin - total_w - 6
        y = y_delta - 8
        old_bbox = (x, y, total_w + 12, h + 16)

    # If new is empty, just clear old and flush once
    if not new_delta_text:
        if old_bbox:
            _clear_rect(lcd, old_bbox[0], old_bbox[1], old_bbox[2], old_bbox[3], BLACK)
            _show_rect(lcd, old_bbox[0], old_bbox[1], old_bbox[2], old_bbox[3])
        st.delta_text = new_delta_text
        return

    # Compute new box
    sign = new_delta_text[0]
    val_num = new_delta_text[1:]

    h_small = w_small.font.height()
    h_delta = w_delta_icon.font.height()
    y_delta_centered = y_delta + (h_small - h_delta) // 2 + v_offset

    num_w = w_small.stringlen(val_num)
    sign_w = w_delta_icon.stringlen(sign)

    x_num = W - right_margin - num_w
    x_sign = x_num - sign_w - gap

    # new bbox (combined)
    total_w = (W - right_margin) - x_sign
    h = max(h_small, h_delta)
    x = x_sign - 6
    y = min(y_delta_centered, y_delta) - 8
    new_bbox = (x, y, total_w + 12, h + 16)

    dirty = _union_rect(old_bbox, new_bbox)

    _clear_rect(lcd, dirty[0], dirty[1], dirty[2], dirty[3], BLACK)

    w_delta_icon.setcolor(WHITE, BLACK)
    w_small.setcolor(WHITE, BLACK)

    w_delta_icon.set_textpos(lcd, y_delta_centered, x_sign)
    w_delta_icon.printstring(sign)

    w_small.set_textpos(lcd, y_delta + NUM_Y_OFFSET, x_num + NUM_X_OFFSET)
    w_small.printstring(val_num)

    _show_rect(lcd, dirty[0], dirty[1], dirty[2], dirty[3])

    st.delta_text = new_delta_text


def draw_loading_once(lcd, writer, st):
    gc.collect()
    # DON'T clear - logo is already showing
    # Just overlay the message at the bottom
    writer.setcolor(WHITE, BLACK)
    writer.set_textpos(lcd, 195, 220)  # Bottom of 320x240 screen
    writer.printstring(":)")

    # Only update the small region where we wrote
    if hasattr(lcd, 'show_rect'):
        lcd.show_rect(220, 190, 80, 40)
    else:
        lcd.show()

    

def draw_all_fields_if_needed(
    lcd,
    w_small, w_age_small, w_arrow, w_heart, w_delta_icon,
    hb_state,
    st
):
    global last

    if st.factory_mode:
        return

    if st.wifi_lost:
        return

    W, H = lcd.width, lcd.height

    y_age = 6

    if not last:
        return

    # If this is the FIRST time drawing data, clear the logo
    if not st.last_have_data:
        lcd.fill(BLACK)
        lcd.show()
        st.last_have_data = True
        # Reset all state so everything draws fresh
        st.age_text = None
        st.bg_text = None
        st.arrow_text = None
        st.delta_text = None
        st.heart_on = None

    # Calculate layout positions
    heart_right_margin = 10

    age_small_h = w_age_small.font.height()
    heart_h = w_heart.font.height()
    heart_w = w_heart.stringlen("T")

    x_heart = W - heart_right_margin - heart_w
    y_heart = y_age + (age_small_h - heart_h) // 4

    big_h = BIG_DIGITS_HEIGHT
    small_h = w_small.font.height()
    arrow_h = w_arrow.font.height()
    bottom_h = max(small_h, arrow_h)

    y_bg = (H - big_h) // 2

    y_bottom_base = H - bottom_h - 1
    arrow_offset = -2
    x_arrow = 10
    y_arrow = (y_bottom_base + (bottom_h - arrow_h) // 2) + arrow_offset
    y_delta = y_bottom_base + (bottom_h - small_h) // 2

    # Draw all data fields
    raw_s = last["time_ms"] // 1000
    age_s = now_unix_s() - raw_s
    if age_s < 0:
        age_s = 0
    mins = int((age_s + 30) // 60)

    if mins == 1:
        age_text = "1 min ago"
    else:
        age_text = str(mins) + " mins ago"
    
    age_color = RED if mins >= STALE_MIN else WHITE
    bg_val = last["bg"]
    bg_text = fmt_bg(bg_val)

    bg_color = GREEN
    if bg_val <= LOW_THRESHOLD:
        bg_color = RED
    elif bg_val >= HIGH_THRESHOLD:
        bg_color = YELLOW

    direction = last["direction"]
    arrow_text = last["arrow"]
    arrow_color = WHITE
    if ALERT_DOUBLE_UP and direction == "DoubleUp":
        arrow_color = YELLOW
    elif ALERT_DOUBLE_DOWN and direction == "DoubleDown":
        arrow_color = RED

    delta_text = fmt_delta(last["delta"])

    _begin_batch()
    _draw_age_if_changed(lcd, w_age_small, age_text, age_color, st, y_age)
    _draw_heart_if_changed(lcd, w_heart, hb_state, st, x_heart, y_heart, pad=2)
    _draw_bg_if_changed(lcd, bg_text, bg_color, st, y_bg)
    _draw_arrow_if_changed(lcd, w_arrow, arrow_text, arrow_color, st, x_arrow, y_arrow, x_offset=10, y_offset=-10)
    _draw_delta_if_changed(lcd, w_small, w_delta_icon, delta_text, st, y_delta, right_margin=4)
    _end_batch(lcd)


def draw_wifi_lost_screen(lcd, w_small, st):
    if st.wifi_lost:
        return
    st.wifi_lost = True
    W, H = lcd.width, lcd.height
    lcd.fill(BLACK)
    fh = w_small.font.height()
    w_small.setcolor(WHITE, BLACK)
    for msg, y in (("WiFi Lost", H // 2 - fh - 4), ("Retrying...", H // 2 + 4)):
        x = max(0, (W - w_small.stringlen(msg)) // 2)
        w_small.set_textpos(lcd, y, x)
        w_small.printstring(msg)
    lcd.show()


_BOOT_BTN = Pin(0, Pin.IN, Pin.PULL_UP)

async def task_factory_reset_button(lcd, w_small, st):
    """Hold BOOT (GPIO 0) while running to trigger a 5-second countdown factory reset."""
    import os
    from machine import reset as machine_reset
    W, H = lcd.width, lcd.height
    # config_font (15px) has the letters we need; w_small (48px digits) for the countdown
    w_cfg = CWriter(lcd, font_config, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    fh_cfg = font_config.height()  # 15px
    while True:
        # Wait for button press (active-low)
        if _BOOT_BTN.value() != 0:
            await asyncio.sleep_ms(50)
            continue
        # --- Button is pressed: show warning screen ---
        st.factory_mode = True
        aborted = False
        for secs_left in range(5, -1, -1):
            # Re-check: if button was released, abort
            if _BOOT_BTN.value() != 0:
                aborted = True
                break
            # Draw warning screen using config_font for text, w_small for digit
            lcd.fill(BLACK)
            lines = [
                ("Factory Reset",       RED),
                ("Will erase your settings", WHITE),
                ("Full setup will be needed",  WHITE),
                ("Release to cancel",   WHITE),
            ]
            y = 60
            for text, color in lines:
                w_cfg.setcolor(color, BLACK)
                x = max(0, (W - w_cfg.stringlen(text)) // 2)
                w_cfg.set_textpos(lcd, y, x)
                w_cfg.printstring(text)
                y += fh_cfg + 8
            # Countdown digit — centered, lower half of screen
            countdown = str(secs_left)
            cx = max(0, (W - w_small.stringlen(countdown)) // 2)
            cy = H // 2 + 60
            w_small.setcolor(RED, BLACK)
            w_small.set_textpos(lcd, cy, cx)
            w_small.printstring(countdown)
            lcd.show()
            if secs_left == 0:
                break  # countdown done — execute reset below
            # Poll button every 100 ms for up to 1 second so release is detected fast
            for _ in range(10):
                await asyncio.sleep_ms(100)
                if _BOOT_BTN.value() != 0:
                    aborted = True
                    break
            if aborted:
                break
        if aborted:
            # Restore normal screen
            st.factory_mode = False
            global factory_reset_exit_requested
            factory_reset_exit_requested = True
            # Debounce: wait for button to be fully released
            while _BOOT_BTN.value() == 0:
                await asyncio.sleep_ms(50)
            continue
        # Countdown reached 0 — perform factory reset
        try:
            os.remove("config.py")
        except OSError:
            pass
        machine_reset()


async def task_heartbeat(lcd, w_small, w_age_small, w_arrow, w_heart, w_delta_icon, st):
    global hb_state, last, wdt, factory_reset_exit_requested
    
    while True:
        if wdt:
            wdt.feed()
        
        # Check if we need to exit factory reset
        if factory_reset_exit_requested:
            factory_reset_exit_requested = False
            
            # Clear screen
            lcd.fill(BLACK)
            lcd.show()
            
            # Reset state to force full redraw
            st.age_text = None
            st.bg_text = None
            st.arrow_text = None
            st.delta_text = None
            st.heart_on = None

        hb_state = not hb_state
        draw_all_fields_if_needed(
            lcd, w_small, w_age_small, w_arrow, w_heart, w_delta_icon,
            hb_state, st
        )
        await asyncio.sleep(1)


async def task_age_redraw(lcd, w_small, w_age_small, w_arrow, w_heart, w_delta_icon, st):
    global last, hb_state
    while True:
        if last:
            draw_all_fields_if_needed(
                lcd, w_small, w_age_small, w_arrow, w_heart, w_delta_icon,
                hb_state, st
            )
        await asyncio.sleep(60)


async def task_glucose_fetch(lcd, w_small, w_age_small, w_arrow, w_heart, w_delta_icon, st):
    global last, hb_state, wifi_ok, wdt

    await asyncio.sleep(1 if wifi_ok else 60)

    while True:
        if wdt:
            wdt.feed()

        # Detect WiFi drop
        try:
            wlan = network.WLAN(network.STA_IF)
            connected = wlan.active() and wlan.isconnected()
        except Exception:
            connected = False

        if not connected:
            draw_wifi_lost_screen(lcd, w_small, st)
            await asyncio.sleep_ms(5000)
            continue

        # WiFi is back - clear error state and force a full redraw
        if st.wifi_lost:
            st.wifi_lost = False
            st.last_have_data = False
            st.age_text = None
            st.bg_text = None
            st.arrow_text = None
            st.delta_text = None
            st.heart_on = None

        try:
            parsed = fetch_and_parse()
            if parsed:
                last = parsed
                check_glucose_alerts(last["bg"])
                draw_all_fields_if_needed(
                    lcd, w_small, w_age_small, w_arrow, w_heart, w_delta_icon,
                    hb_state, st
                )
            gc.collect()
        except Exception as e:
            pass

        await asyncio.sleep_ms(5000)

async def task_buzzer_stop_button():
    # Simple debounce + edge detect
    last_state = 1
    stable_count = 0
    DEBOUNCE_MS = 30
    POLL_MS = 10

    while True:
        s = BTN_STOP.value()
        if s == last_state:
            stable_count += POLL_MS
        else:
            stable_count = 0
            last_state = s

        # Press detected (active-low) and stable long enough
        if s == 0 and stable_count >= DEBOUNCE_MS:
            request_buzzer_stop()
            # Wait until release so it only fires once per press
            while BTN_STOP.value() == 0:
                await asyncio.sleep_ms(20)
            stable_count = 0
            last_state = 1

        await asyncio.sleep_ms(POLL_MS)

async def task_buzzer_driver():
    global buzzer_mode, last_mild_beep_time

    while True:
        now = utime.ticks_ms()
        snoozed = utime.ticks_diff(now, buzzer_snooze_until) < 0

        # Mode 1: SEVERE solid tone (ignores snooze)
        if buzzer_mode == 1:
            BUZ.value(0)  # ON solid
            await asyncio.sleep_ms(50)
            continue

        # Mode 2: MILD pattern (respects snooze)
        if buzzer_mode == 2 and not snoozed:
            if utime.ticks_diff(now, last_mild_beep_time) >= MILD_COOLDOWN_MS:
                # 3-beep sequence, repeated 3 times with a 1-second gap
                stopped = False
                for _rep in range(3):
                    if stopped:
                        break
                    for _ in range(3):
                        if BTN_STOP.value() == 0:
                            request_buzzer_stop()
                            stopped = True
                            break
                        BUZ.value(0); await asyncio.sleep_ms(150)
                        BUZ.value(1); await asyncio.sleep_ms(150)
                    if not stopped and _rep < 2:
                        await asyncio.sleep_ms(1000)
                last_mild_beep_time = utime.ticks_ms()

            BUZ.value(1)
            await asyncio.sleep_ms(100)
            continue

        # Otherwise OFF
        BUZ.value(1)
        await asyncio.sleep_ms(100)



async def task_wifi_reconnect(st):
    global wdt
    while True:
        await asyncio.sleep(10)
        if not st.wifi_lost:
            continue
        if wdt:
            wdt.feed()
        connect_wifi(WIFI_SSID, WIFI_PASSWORD)
        if wdt:
            wdt.feed()


async def async_main(lcd, w_small, w_age_small, w_arrow, w_heart, w_delta_icon, st):
    global wdt

    asyncio.create_task(task_factory_reset_button(lcd, w_small, st))
    asyncio.create_task(task_buzzer_stop_button())
    asyncio.create_task(task_buzzer_driver())

    await asyncio.sleep(2)

    asyncio.create_task(task_heartbeat(lcd, w_small, w_age_small, w_arrow, w_heart, w_delta_icon, st))
    asyncio.create_task(task_age_redraw(lcd, w_small, w_age_small, w_arrow, w_heart, w_delta_icon, st))
    asyncio.create_task(task_glucose_fetch(lcd, w_small, w_age_small, w_arrow, w_heart, w_delta_icon, st))
    asyncio.create_task(task_wifi_reconnect(st))

    while True:
        if wdt:
            wdt.feed()  # Feed in main loop
        await asyncio.sleep(5)  # Feed every 5 seconds



# --- Buzzer Configuration ---
# Read alert settings from config.py

# Snooze duration from config
BUZZER_SNOOZE_MS = cfg("ALERT_SNOOZE_MINUTES", 10) * 60 * 1000
buzzer_snooze_until = 0

# Alert thresholds from config
ALERT_LOW_ENABLED = cfg("ALERT_LOW_ENABLED", True)
ALERT_LOW_USE_THRESHOLD = cfg("ALERT_LOW_USE_THRESHOLD", True)
ALERT_LOW_CUSTOM = float(cfg("ALERT_LOW_CUSTOM", 4.0))
ALERT_SEVERE_ENABLED = cfg("ALERT_SEVERE_ENABLED", True)
ALERT_SEVERE_THRESHOLD = float(cfg("ALERT_SEVERE_THRESHOLD", 3.0))

# Determine which low threshold to use
if ALERT_LOW_USE_THRESHOLD:
    MILD_LOW_THRESHOLD = LOW_THRESHOLD  # Use the color threshold
else:
    MILD_LOW_THRESHOLD = ALERT_LOW_CUSTOM  # Use custom alert threshold

SEVERE_LOW_THRESHOLD = ALERT_SEVERE_THRESHOLD

# Buzzer mode: 0=off, 1=severe solid, 2=mild pattern
buzzer_mode = 0

last_mild_beep_time = utime.ticks_add(utime.ticks_ms(), -BUZZER_SNOOZE_MS)
MILD_COOLDOWN_MS = BUZZER_SNOOZE_MS


def check_glucose_alerts(bg_value):
    global buzzer_mode

    if bg_value is None:
        return

    now = utime.ticks_ms()
    snoozed = utime.ticks_diff(now, buzzer_snooze_until) < 0

    # If snoozed, force off (no alerts at all)
    if snoozed:
        buzzer_mode = 0
        return

    # SEVERE has priority (if enabled)
    if ALERT_SEVERE_ENABLED and bg_value <= SEVERE_LOW_THRESHOLD:
        buzzer_mode = 1
        return

    # MILD (if enabled)
    if ALERT_LOW_ENABLED and bg_value <= MILD_LOW_THRESHOLD:
        buzzer_mode = 2
        return

    # No alerts triggered
    buzzer_mode = 0

            
# ============================
# MAIN LOOP SECTION
# ============================

def main(framebuffer=None):
    import utime
    import network

    global last, hb_state, wdt, wifi_ok
    last = None
    hb_state = True
    wdt = None

    # 1. INIT DISPLAY
    lcd = LCD_Driver(fb=framebuffer, bl=80)
    globals()["LCD"] = lcd
    
    # Start with a black screen; data draw will populate it immediately.
    lcd.fill(BLACK)
    lcd.show()

    # DELETE BOOTLOADER FONTS IMMEDIATELY
    import sys
    for mod in ['config_font', 'config_font_title', 'writer']:
        if mod in sys.modules:
            del sys.modules[mod]
    gc.collect()

    # 2. INIT WRITERS
    w_small = CWriter(lcd, font_small, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_age_small = CWriter(lcd, age_font_small, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_arrow = CWriter(lcd, font_arrows, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_heart = CWriter(lcd, font_heart, fgcolor=RED, bgcolor=BLACK, verbose=False)
    w_delta_icon = CWriter(lcd, font_delta, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    gc.collect()

    w_small.set_spacing(3)
    w_age_small.set_spacing(2)
    w_arrow.set_spacing(8)

    st = ScreenState()

    # 4. WIFI
    sta = network.WLAN(network.STA_IF)
    wifi_ok = sta.isconnected() or connect_wifi(WIFI_SSID, WIFI_PASSWORD)
    gc.collect()

    if wifi_ok:
        ntp_sync()

    # 5. INITIAL DATA FETCH
    try:
        parsed = fetch_and_parse()
        if parsed:
            last = parsed
            check_glucose_alerts(last["bg"])
    except:
        pass

    # 6. If we have data, draw it NOW
    if last:
        draw_all_fields_if_needed(
            lcd, w_small, w_age_small, w_arrow, w_heart, w_delta_icon,
            hb_state, st
        )
        gc.collect()

    # 7. START ASYNC LOOP
    asyncio.run(async_main(
        lcd, w_small, w_age_small, w_arrow, w_heart, w_delta_icon, st
    ))


if __name__ == "__main__":
    try:
        # ESP32-S3 has 8MB PSRAM — landscape 320×240 framebuffer
        fb = bytearray(320 * 240 * 2)  # 153KB framebuffer
        print("Framebuffer allocated: %d bytes" % len(fb))
        main(framebuffer=fb)
    except Exception as e:
        print("CRITICAL CRASH:", e)
        utime.sleep(5)
        from machine import reset
        reset()
