import utime
import bootloader
import network
import urequests as requests
import ntptime
import machine
import socket
import uos as os  # filesystem
import gc
import json
import framebuf
import uos as os


REMOTE_CHECK_INTERVAL = 30

def net_diag():
    try:
        import network, socket
    except ImportError:
        print("net_diag: missing network/socket modules")
        return

    wlan = network.WLAN(network.STA_IF)
    try:
        print("ifconfig:", wlan.ifconfig())
    except Exception as e:
        print("ifconfig error:", e)

    # DNS test to your Nightscout host
    host = "sennaloop-673ad2782247.herokuapp.com"
    try:
        info = socket.getaddrinfo(host, 443)
    except OSError as e:
        print("DNS error:", e)

    # Raw IP route test
    try:
        addr = socket.getaddrinfo("1.1.1.1", 80)[0][-1]
        s = socket.socket()
        s.settimeout(5)
        s.connect(addr)
        s.close()
    except OSError as e:
        print("RAW IP error:", e)

    print("=== END NET DIAG ===")


# ------------------------
# Heartbeat / memory monitor
# ------------------------
last_heartbeat_ms = None

def heartbeat(tag="OK"):
    """
    Print a status line with RAM usage about every 10 seconds.
    Uses utime.ticks_ms() so it works reliably in MicroPython.
    """
    global last_heartbeat_ms
    now = utime.ticks_ms()
    if last_heartbeat_ms is None or utime.ticks_diff(now, last_heartbeat_ms) >= 10000:
        gc.collect()
        print(
            "HB", tag,
            "| free RAM:", gc.mem_free(),
            "| alloc RAM:", gc.mem_alloc(),
        )
        last_heartbeat_ms = now

# ---------- Boot logo helpers ----------

LOGO_FILE = "logo.bin"
LOGO_W = 160   # image width in pixels
LOGO_H = 128   # image height in pixels

_boot_logo_fb = None
_boot_logo_ready = False


def load_boot_logo():
    """
    Load logo.bin as an RGB565 FrameBuffer once.
    """
    global _boot_logo_fb, _boot_logo_ready

    if _boot_logo_ready:
        return

    try:
        with open(LOGO_FILE, "rb") as f:
            buf = f.read()
    except OSError as e:
        print("Boot logo: failed to open", LOGO_FILE, ":", e)
        _boot_logo_fb = None
        _boot_logo_ready = True
        return

    expected = LOGO_W * LOGO_H * 2
    if len(buf) != expected:
        print("Boot logo: size mismatch, got", len(buf), "expected", expected)
        _boot_logo_fb = None
        _boot_logo_ready = True
        return

    try:
        import framebuf
        fb = framebuf.FrameBuffer(bytearray(buf), LOGO_W, LOGO_H, framebuf.RGB565)
        _boot_logo_fb = fb
        _boot_logo_ready = True
        print("Boot logo loaded, bytes:", len(buf))
    except Exception as e:
        print("Boot logo: FrameBuffer init failed:", e)
        _boot_logo_fb = None
        _boot_logo_ready = True

# ---------- Main Boot Sequence with Animation ----------

def boot_sequence(lcd):
    """
    Handles the entire animated boot process:
    1. Animated Wi-Fi connection.
    2. Animated initial data fetch.
    Returns the initial parsed data or raises RuntimeError on failure.
    """
    # Use a counter for the animation state
    dots_counter = 0
    
    # --- 1. Wi-Fi Connection Phase (Animated) ---
    wlan = network.WLAN(network.STA_IF)
    status_msg = "Wi-Fi: {}".format(WIFI_SSID) # Static message for subline
    
    print("Attempting Wi-Fi connection...")
    
    # Ensure AP is off and STA is on before trying to connect
    ap = network.WLAN(network.AP_IF)
    ap.active(False)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    
    max_wait_ms = 15000 # 15 seconds total wait for connection
    start_time = utime.ticks_ms()
    
    while wlan.status() != 3:
        if utime.ticks_diff(utime.ticks_ms(), start_time) >= max_wait_ms:
            # Manually trigger the connect_wifi logic to display the failure message
            try:
                connect_wifi(lcd) 
            except Exception:
                pass 
            raise RuntimeError("Wi-Fi connection timed out.")

        dots_counter = (dots_counter + 1) % 4
        # Draw the connection status message. Swapping:
        # - "Connecting" is the animated status (Y=140)
        # - status_msg ("Wi-Fi: <SSID>") is the static subline (Y=150)
        draw_logo_with_status(lcd, "Connecting", status_msg, dots_counter)
        utime.sleep_ms(500) # Half-second update for animation

    # Now that we are connected, perform NTP sync and display final 'Wi-Fi OK' message
    # This will skip the long connection loop inside connect_wifi
    connect_wifi(lcd)


    # --- 2. Initial Data Fetch Phase (Animated) ---
    data = None
    max_fetch_attempts = 5
    
    print("Fetching initial data...")
    
    for fetch_attempt in range(max_fetch_attempts):
        dots_counter = (dots_counter + 1) % 4
        # Use the simpler loading screen while fetching (draw_loading_screen is fine)
        draw_loading_screen(lcd, dots_counter)
        utime.sleep_ms(200) # Quick animation update before blocking fetch
        
        # Attempt to fetch data (this is the blocking call)
        raw = fetch_glucose_data()
        if raw:
            # Process immediately to confirm validity
            data = parse_and_calculate(raw)
            if data:
                print("Initial data fetched successfully.")
                return data
        
        # *** CHANGE MADE HERE ***
        print("Data fetch failed, retrying in 5s...")
        
        # Animate "Retrying..." during the 5-second wait time
        wait_seconds = 3 # Changed from 3 to 5
        for i in range(wait_seconds * 2): # Animate twice a second (10 total iterations)
            dots_counter = (dots_counter + 1) % 4
            # New: "Retrying" is animated status, "NO DATA" is static subline
            draw_logo_with_status(lcd, "Retrying", "NO DATA", dots_counter)
            utime.sleep_ms(500)
            
    # If initial data fetch failed across all attempts, raise fatal error
    raise RuntimeError("Failed to fetch initial data.")

def draw_boot_logo(lcd):
    """
    Draw the static logo full-screen once during boot.
    """
    load_boot_logo()

    lcd.fill(BLACK)
    if _boot_logo_fb is not None:
        # Logo is 160×128, display is 128×160; rotate 90° to fit.
        try:
            lcd.blit(_boot_logo_fb, 0, 0)
        except Exception as e:
            print("Boot logo blit failed:", e)
    else:
        lcd.text("IRIS", 40, 60, WHITE)

    # ADDED: Draw Device ID
    _draw_device_id(lcd)

    lcd.show()
    

def draw_loading_screen(lcd, dots_count):
    """
    Draws the logo plus a simple "Loading..." message with animated dots.
    """
    # Use the existing function, setting the status to 'Loading'
    # and clearing the subline. This keeps the logo and device ID visible.

def draw_logo_with_status(lcd, status, subline, dots_count):
    """
    Draw logo + status text + animated dots.
    dots_count: 0..3 -> ".", "..", "...", "" (new sequence)
    """
    load_boot_logo()

    lcd.fill(BLACK)
    if _boot_logo_fb is not None:
        try:
            lcd.blit(_boot_logo_fb, 0, 0)
        except Exception as e:
            print("Logo status blit failed:", e)

    # NEW DOT LOGIC: 0->., 1->.., 2->..., 3->""
    DOT_MAP = [".", "..", "...", ""] 
    dots = DOT_MAP[dots_count]
    
    # Apply dots to the main status line (e.g., "Loading.", "Connecting..")
    msg = "{}{}".format(status, dots)

    # Draw status text (animated line) (Bottom Left: X=10, Y=140)
    lcd.text(msg, 10, 140, BLACK)
    if subline:
        # Draw the subline (static info) (Bottom Left: X=10, Y=150)
        lcd.text(subline, 10, 150, BLACK)

    # Draw Device ID
    _draw_device_id(lcd)
    
    lcd.show()


LOGO_BUFFER = None  # raw 16-bit 128x160 (or 160x128) image from logo.bin


def load_logo_if_needed():
    """
    Lazy-load logo.bin into RAM once.
    logo.bin must be a RGB565 raw buffer: width * height * 2 bytes.
    """
    global LOGO_BUFFER
    if LOGO_BUFFER is not None:
        return

    try:
        with open("logo.bin", "rb") as f:
            LOGO_BUFFER = f.read()

    except OSError as e:
        print("No logo.bin found:", e)
        LOGO_BUFFER = None  # don't keep retrying





# ---------- Display driver ----------
try:
    from Pico_LCD_1_8 import LCD_1inch8 as ST7735
except ImportError:
    print("FATAL: Display not found. Rebooting in 5 seconds...")
    utime.sleep(0)
    machine.reset()

from writer import CWriter
import small_20 as font_main
import large_65 as font_bg
import arrows_30 as font_arrows

# ---------- Colours ----------
BLACK  = 0x0000
WHITE  = 0xffff
RED    = 0x07E0
GREEN  = 0x07E0
YELLOW = 0xc5df
BLUE   = 0x1200

# ---------- Display / font globals ----------
DISPLAY_WIDTH  = 128
DISPLAY_HEIGHT = 160

SMALL_H = font_main.height()
BG_H    = font_bg.height()
ARROWS  = font_arrows.height()
LINE_GAP = 8

wri_small  = None
wri_bg     = None
wri_arrows = None

SYNC_INTERVAL = 60  # NTP re-sync cycles

# ---------- Config globals ----------
WIFI_SSID     = ""
WIFI_PASSWORD = ""
NS_URL        = ""
NS_TOKEN      = ""
API_ENDPOINT  = ""

# Alert/display config (variables loaded from config.py)
DISPLAY_UNITS       = None # "mmol" or "mgdl"
STALE_MIN           = None
LOW_THRESHOLD       = None
HIGH_THRESHOLD      = None
ALERT_DOUBLE_UP     = None
ALERT_DOUBLE_DOWN   = None
GITHUB_TOKEN = "1234"

# ---------- Detect existing config ----------
try:
    import config as user_config
    if hasattr(user_config, "WIFI_SSID") and hasattr(user_config, "NS_URL"):
        HAS_CONFIG = True
    else:
        user_config = None
        HAS_CONFIG = False
except ImportError:
    user_config = None
    HAS_CONFIG = False


def center_x(writer, text: str) -> int:
    width = writer.stringlen(text)
    return (DISPLAY_WIDTH - width) // 2


def check_factory_reset(lcd):
    """
    Hold GP15 LOW at boot to perform a factory reset:
    - Deletes config.py (if present)
    - Reboots, which will fall back to AP config portal
    Wiring: GP15 -> momentary button -> GND (active-low).
    If no button is wired, pull-up keeps it inactive.
    """
    RESET_PIN = 15
    try:
        pin = machine.Pin(RESET_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
    except Exception as e:
        print("Factory reset failed:", e)
        return

    if pin.value() == 0:
        lcd.fill(BLACK)
        lcd.text("Factory reset", 5, 40, WHITE)
        lcd.text("Erasing config", 5, 60, WHITE)
        lcd.show()
        try:
            os.remove("config.py")
            print("Factory reset complete")
            lcd.text("Factory reset OK", 5, 80, WHITE)
        except OSError as e:
            print("Factory reset: no config.py or error:", e)
            lcd.text("No config.py", 5, 80, WHITE)
        lcd.show()
        utime.sleep(0)
        machine.reset()


# ---------- Config helpers ----------

def url_decode(s):
    s = s.replace("+", " ")
    out = ""
    i = 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s):
            try:
                out += chr(int(s[i+1:i+3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out += s[i]
        i += 1
    return out


def parse_query(query):
    params = {}
    if not query:
        return params
    for pair in query.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[k] = url_decode(v)
    return params


def write_config_py(ssid, pwd, ns_url, token, endpoint,
                    units, stale_min, low_thr, high_thr,
                    alert_du, alert_dd,
                    github_token=""):
    def esc(x):
        return x.replace('"', '\\"')

    units_str = "mmol" if units == "mmol" else "mgdl"

    src = (
        'WIFI_SSID = "{ssid}"\n'
        'WIFI_PASSWORD = "{pwd}"\n'
        'NS_URL = "{ns_url}"\n'
        'NS_TOKEN = "{token}"\n'
        'API_ENDPOINT = "{endpoint}"\n'
        'DISPLAY_UNITS = "{units}"\n'
        'STALE_MIN = {stale}\n'
        'LOW_THRESHOLD = {low}\n'
        'HIGH_THRESHOLD = {high}\n'
        'ALERT_DOUBLE_UP = {alert_du}\n'
        'ALERT_DOUBLE_DOWN = {alert_dd}\n'
        'GITHUB_TOKEN = "{gh_token}"\n'
    ).format(
        ssid=esc(ssid),
        pwd=esc(pwd),
        ns_url=esc(ns_url),
        token=esc(token),
        endpoint=esc(endpoint),
        units=units_str,
        stale=stale_min,
        low=low_thr,
        high=high_thr,
        alert_du="True" if alert_du else "False",
        alert_dd="True" if alert_dd else "False",
        gh_token=esc(github_token),
    )

    print("Writing config.py with:")
    print(src)

    with open("config.py", "w") as f:
        f.write(src)

    try:
        st = os.stat("config.py")
        print("config.py written OK, stat:", st)
    except OSError as e:
        print("Failed to stat config.py:", e)



# ---------- Config portal HTML ----------

CONFIG_FORM_HTML = """HTTP/1.1 200 OK
Content-Type: text/html; charset=utf-8

<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Device Configuration Portal</title>
    
    <style>
        :root {
            --primary-color: #005A9C;
            --primary-hover: #00457A;
            --accent-color: #1A936F;
            --text-color: #333333;
            --light-bg: #f7f9fc;
            --border-color: #e0e6ed;
            --help-text-color: #7f8c8d;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: var(--light-bg);
            padding: 20px;
            display: flex;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            color: var(--text-color);
        }
        .professional-form {
            max-width: 650px;
            width: 100%;
            background: #ffffff;
            padding: 40px;
            border-radius: 12px;
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
            border-top: 6px solid var(--primary-color);
        }
        .form-header {
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border-color);
        }
        .form-header h1 {
            color: var(--primary-color);
            font-size: 2em;
            font-weight: 700;
            margin: 0;
        }
        .form-header p {
            color: var(--help-text-color);
            margin-top: 10px;
            font-size: 0.95em;
        }
        fieldset {
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 30px;
            transition: border-color 0.3s;
        }
        fieldset:hover {
            border-color: #cccccc;
        }
        legend {
            font-size: 1.2em;
            font-weight: 600;
            color: var(--primary-color);
            padding: 0 10px;
        }
        .form-group {
            margin-bottom: 18px;
        }
        .form-group label {
            display: block;
            margin-bottom: 6px;
            color: var(--text-color);
            font-weight: 500;
            font-size: 0.95em;
        }
        .form-group label.required::after {
            content: '*';
            color: var(--primary-color);
            margin-left: 4px;
        }
        .form-group input[type="text"],
        .form-group input[type="password"],
        .form-group input[type="url"],
        .form-group input[type="number"],
        .form-group select {
            width: 100%;
            padding: 12px;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            box-sizing: border-box;
            font-size: 1em;
            transition: border-color 0.3s, box-shadow 0.3s;
        }
        .form-group input:focus,
        .form-group select:focus {
            border-color: var(--accent-color);
            box-shadow: 0 0 0 3px rgba(26, 147, 111, 0.15);
            outline: none;
        }
        .help-text {
            display: block;
            font-size: 0.8em;
            color: var(--help-text-color);
            margin-top: 5px;
        }
        .checkbox-group {
            display: flex;
            align-items: center;
            margin-bottom: 10px;
        }
        .checkbox-group input[type="checkbox"] {
            margin-right: 10px;
            transform: scale(1.1);
            cursor: pointer;
            accent-color: var(--primary-color);
        }
        .checkbox-group label {
            font-weight: normal;
            color: var(--text-color);
            cursor: pointer;
        }
        .form-actions {
            padding-top: 20px;
            text-align: right;
        }
        .submit-button {
            background-color: var(--primary-color);
            color: white;
            padding: 14px 30px;
            border: none;
            border-radius: 8px;
            font-size: 1.05em;
            font-weight: 600;
            cursor: pointer;
            transition: background-color 0.3s, transform 0.1s;
            box-shadow: 0 4px 10px rgba(0, 90, 156, 0.3);
        }
        .submit-button:hover {
            background-color: var(--primary-hover);
            box-shadow: 0 6px 15px rgba(0, 90, 156, 0.4);
        }
        .submit-button:active {
            transform: translateY(1px);
        }
    </style>
</head>
<body>

<form action="/save" method="GET" class="professional-form">
  
  <div class="form-header">
    <h1>Device Configuration Portal</h1>
  </div>
  
  <fieldset>
    <legend>📡 Wi-Fi Setup</legend>
    <div class="form-group">
      <label for="ssid" class="required">Wi-Fi SSID (Network Name)</label>
      <input type="text" id="ssid" name="ssid" placeholder="HomeNetwork" required>
    </div>
    <div class="form-group">
      <label for="pwd" class="required">Wi-Fi Password</label>
      <input type="password" id="pwd" name="pwd" placeholder="Enter secure password" required>
    </div>
  </fieldset>

  <fieldset>
    <legend>☁️ Nightscout Integration</legend>
    <div class="form-group">
      <label for="ns_url" class="required">Nightscout URL</label>
      <input type="url" id="ns_url" name="ns_url" placeholder="https://your-site.herokuapp.com" required>
      <span class="help-text">Use the full URL starting with "https://"</span>
    </div>
    <div class="form-group">
      <label for="token" class="required">API Secret (Access Token)</label>
      <input type="text" id="token" name="token" placeholder="e.g., your-api-secret" required>
      <span class="help-text">This is your Nightscout connection security key.</span>
    </div>
    <div class="form-group">
      <label for="endpoint">API Endpoint</label>
      <input type="text" id="endpoint" name="endpoint" value="/api/v1/entries/sgv.json?count=2">
      <span class="help-text">Generally, this default value does not need to be changed.</span>
    </div>
  </fieldset>

  <fieldset>
    <legend>📈 Display &amp; Alert Settings</legend>
    
    <div class="form-group">
      <label for="units">Glucose Measurement Units</label>
      <select id="units" name="units">
        <option value="mmol">mmol/L</option>
        <option value="mgdl">mg/dL</option>
      </select>
    </div>

    <div class="form-group">
      <label for="stale">Data Stale Threshold (minutes)</label>
      <input type="number" id="stale" name="stale" value="7" min="1" max="60" required>
      <span class="help-text">Sets a warning if data has not been updated within this time.</span>
    </div>
    <div class="form-group">
      <label for="low">Low Blood Glucose Alert Threshold</label>
      <input type="number" id="low" name="low" value="4.0" step="0.1" required>
    </div>
    <div class="form-group">
      <label for="high">High Blood Glucose Alert Threshold</label>
      <input type="number" id="high" name="high" value="11.0" step="0.1" required>
    </div>
  </fieldset>

  <fieldset>
    <legend>🚨 Trend Alert Options</legend>
    <div class="checkbox-group">
      <input type="checkbox" id="alert_du" name="alert_du" checked="checked">
      <label for="alert_du">Enable urgent alert for Double Up trend</label>
    </div>
    <div class="checkbox-group">
      <input type="checkbox" id="alert_dd" name="alert_dd" checked="checked">
      <label for="alert_dd">Enable urgent alert for Double Down trend</label>
    </div>
  </fieldset>

  <div class="form-actions">
    <button type="submit" class="submit-button">Save Configuration &amp; Reboot</button>
  </div>
</form>

</body>
</html>
"""

CONFIG_SAVED_HTML = """HTTP/1.1 200 OK
Content-Type: text/html; charset=utf-8

<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Configuration Saved - Success</title>
    <style>
        :root {
            --primary-color: #005A9C;
            --accent-color: #1A936F;
            --text-color: #333333;
            --light-bg: #f7f9fc;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: var(--light-bg);
            padding: 20px;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            color: var(--text-color);
            text-align: center;
        }
        .confirmation-card {
            max-width: 450px;
            width: 100%;
            background: #ffffff;
            padding: 40px;
            border-radius: 12px;
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.15);
            border-top: 6px solid var(--accent-color);
        }
        .success-icon {
            font-size: 4em;
            color: var(--accent-color);
            margin-bottom: 20px;
            line-height: 1;
        }
        .confirmation-card h1 {
            font-size: 2em;
            font-weight: 700;
            margin-top: 0;
            margin-bottom: 10px;
        }
        .confirmation-card p {
            color: #555555;
            font-size: 1.1em;
            margin-bottom: 25px;
            line-height: 1.5;
        }
        .status-message {
            display: inline-block;
            background-color: #e6f7f2;
            color: var(--accent-color);
            padding: 10px 20px;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.95em;
        }
    </style>
</head>
<body>
     <div class="confirmation-card">
    <div class="success-icon">
        ✓
    </div>
    <h1>Configuration Saved Successfully!</h1>
    <p>Your new settings have been saved.</p>
    <div class="status-message">
      The device will now apply your settings and start using your Nightscout data.
    </div>
    </div>
</body>
</html>
"""


def handle_config_save_from_query(query, lcd):
    params = parse_query(query)

    ssid     = params.get("ssid", "").strip()
    pwd      = params.get("pwd", "").strip()
    ns_url   = params.get("ns_url", "").strip()
    token    = params.get("token", "").strip()
    endpoint = params.get("endpoint", "").strip() or "/api/v1/entries/sgv.json?count=2"
    units    = params.get("units", "mmol").strip().lower()
    if units not in ("mmol", "mgdl"):
        units = "mmol"

    def to_float(name, default):
        v = params.get(name, "").strip()
        if not v:
            return default
        try:
            return float(v)
        except ValueError:
            return default

    stale_min = to_float("stale", 7.0)
    low_thr   = to_float("low", 4.0)
    high_thr  = to_float("high", 11.0)

    alert_du = "alert_du" in params
    alert_dd = "alert_dd" in params

    print("Form values:", ssid, ns_url, units, stale_min, low_thr, high_thr, alert_du, alert_dd)

    if not (ssid and ns_url):
        print("Missing required fields, redisplaying form")
        return False, CONFIG_FORM_HTML

    lcd.fill(BLACK)
    lcd.text("Saving config", 5, 60, WHITE)
    lcd.show()

    write_config_py(
        ssid, pwd, ns_url, token, endpoint,
        units, stale_min, low_thr, high_thr,
        alert_du, alert_dd
    )

    return True, CONFIG_SAVED_HTML



# ---------- HTTP send helper ----------

def send_http_response(cl, html):
    """
    Reliably send a full HTTP response string in chunks.
    """
    if isinstance(html, str):
        html = html.encode("utf-8")

    mv = memoryview(html)
    total = 0
    length = len(mv)

    while total < length:
        sent = cl.send(mv[total:total+512])
        if sent is None:
            break
        total += sent


# ---------- AP config portal ----------

def ap_config_portal(lcd):
    lcd.fill(BLACK)
    lcd.text("AP Setup Mode", 5, 30, WHITE)
    lcd.text("SSID: NSDisplay", 5, 50, WHITE)
    lcd.text("Pass: nsdisplay", 5, 70, WHITE)
    lcd.text("Go: 192.168.4.1", 5, 90, WHITE)
    lcd.show()

    # Turn off STA when in config mode
    sta = network.WLAN(network.STA_IF)
    sta.active(False)

    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid="NSDisplay", password="nsdisplay")

    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(1)
    print("Config portal on 192.168.4.1:80")

    while True:
        cl, addr = s.accept()
        print("Config request from", addr)
        req = cl.recv(1024)
        if not req:
            cl.close()
            continue

        req_str = req.decode("utf-8", "ignore")
        first_line = req_str.split("\r\n", 1)[0]
        parts = first_line.split()
        if len(parts) < 2:
            send_http_response(cl, CONFIG_FORM_HTML)
            cl.close()
            continue

        method, path = parts[0], parts[1]

        if path.startswith("/save?"):
            query = path.split("?", 1)[1]
            ok, resp_html = handle_config_save_from_query(query, lcd)
            send_http_response(cl, resp_html)
            cl.close()
            if ok:
                # Give browser a moment to receive the response
                utime.sleep(0)
                print("Config saved, performing full reset")
                machine.reset()   # same as pressing RESET
                # no return needed; reset never returns
        else:
            send_http_response(cl, CONFIG_FORM_HTML)
            cl.close()



# ---------- Wi-Fi + NTP ----------

# ---------------- Config helpers (copied from bootloader) ----------------

DEVICE_ID_FILE = "device_id.txt"

def load_device_id():
    """
    Read a persistent device ID from device_id.txt (root of Pico filesystem).
    Returns a string or None if missing/empty.
    """
    try:
        with open(DEVICE_ID_FILE, "r") as f:
            devid = f.read().strip()
            if devid:
                return devid
    except OSError:
        pass
    return None

def _draw_device_id(lcd):
    """
    Draws the device ID in the bottom right corner of the screen.
    """
    dev_id = load_device_id()
    if not dev_id:
        dev_id = "N/A" # Fallback if ID file is missing

    # Format the message
    id_msg = "ID: {}".format(dev_id)

    # Assume 8x8 font for lcd.text() (standard MicroPython/ST7735)
    FONT_WIDTH = 8
    FONT_HEIGHT = 8
    
    # Calculate text position (bottom right corner)
    # The X position is determined by the screen width minus the length of the string
    x = lcd.width - (len(id_msg) * FONT_WIDTH)
    # The Y position is determined by the screen height minus the font height
    y = lcd.height - FONT_HEIGHT
    
    # Draw the text in BLACK (0x0000)
    # NOTE: To draw *in* black *on* the logo, you need a background color.
    # The LCD only supports one foreground color in text(). Let's use WHITE text
    # on a black background for visibility, or if the logo is colored, BLACK text on 
    # the logo's background. Since the request specified "in black" on top of the logo:
    lcd.text(id_msg, x, y, BLACK)
    
# ---------------- End of Config helpers ----------------
# ---------- Wi-Fi + NTP ----------

def connect_wifi(lcd):
    """
    Blocks until Wi-Fi is connected and NTP time is synchronized.
    Does NOT draw any animation or dots, only final status/error message.
    """
    
    # Make sure AP is off in normal run mode
    ap = network.WLAN(network.AP_IF)
    ap.active(False)

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)

    max_wait = 15 # Increased wait time to 15 seconds for robustness
    
    # Wait for connection, but do not animate here (animation is handled higher up)
    while max_wait > 0:
        status = wlan.status()
        if status < 0 or status >= 3:
            break
        
        # NOTE: We skip utime.sleep(0) here as the calling function will handle waiting/animation
        # We need a small yield to allow the core to breathe, but not a full second.
        utime.sleep_ms(100) 
        max_wait -= 1

    # Only treat *Wi-Fi* failure as fatal
    if wlan.status() != 3:
        lcd.fill(BLACK)
        lcd.text("Wi-Fi FAILED", 5, 40, WHITE)
        lcd.text("Status: {}".format(wlan.status()), 5, 60, WHITE)
        lcd.text("Retrying...", 5, 80, WHITE)
        lcd.show()
        raise RuntimeError("Network connection failed (status {})".format(wlan.status()))

    ip = wlan.ifconfig()[0]

    # NTP is *best effort* – never raise here
    try:
        ntptime.settime()
        print("Time synchronized.")
    except Exception as e:
        print("NTP error:", e)
        # Just log; do not raise, so boot can continue.

    # Display connection success on the screen
    # Since we are immediately calling fetch_data after this, we can skip
    # the 2-second sleep and just let the animation handle the brief wait.
# 
    utime.sleep(0) # Pause for human to see

# ---------- Nightscout fetch ----------


def fetch_glucose_data():
    headers = {}
    if NS_TOKEN:
        headers["api-secret"] = NS_TOKEN

    full_url = NS_URL + API_ENDPOINT
    print("Fetching:", full_url)
    
    # Store response object outside the try block for use in exception handler
    resp = None
    
    try:
        resp = requests.get(full_url, headers=headers)
        status = getattr(resp, "status_code", getattr(resp, "status", "unknown"))
        print("HTTP status:", status)
        data = resp.json()
        resp.close()
        return data
    
    except OSError as e:
        # Network-level error (TCP, route, etc.)
        print("HTTP network error in fetch_glucose_data:", repr(e))
        if resp:
            try:
                resp.close()
            except Exception:
                pass
        return None
    
    except Exception as e:
        # JSON parse or other unexpected error
        print("HTTP/JSON error in fetch_glucose_data:", repr(e))
        
        # --- DIAGNOSTIC CODE ---
        if resp:
            try:
                # Log the raw text that failed to parse as JSON
                raw_text = resp.text
                print("HTTP response raw text (JSON fail):", raw_text)
            except Exception as e_text:
                print("Could not read response text for diagnosis:", e_text)
        # --- END DIAGNOSTIC CODE ---
        
        if resp:
            try:
                resp.close()
            except Exception:
                pass
        return None


# ---------- Parse JSON ----------

def parse_and_calculate(data):
    global DISPLAY_UNITS

    if not data:
        print("parse_and_calculate: data is empty or None")
        return None

    if not isinstance(data, list):
        print("parse_and_calculate: expected list, got", type(data))
        return None

    if len(data) < 1:
        print("parse_and_calculate: list length < 1")
        return None

    if "sgv" not in data[0]:
        print('parse_and_calculate: "sgv" not in first element')
        return None

    current_bg_mgdl = data[0]["sgv"]
    current_time_ms = data[0]["date"]
    trend_direction = data[0].get("direction", "NONE")

    if DISPLAY_UNITS == "mgdl":
        current_val = float(current_bg_mgdl)
    else:
        current_val = round(current_bg_mgdl / 18, 1)

    delta_val = None
    if len(data) > 1 and "sgv" in data[1]:
        prev_bg_mgdl = data[1]["sgv"]
        delta_mgdl = current_bg_mgdl - prev_bg_mgdl
        if DISPLAY_UNITS == "mgdl":
            delta_val = float(delta_mgdl)
        else:
            delta_val = round(delta_mgdl / 18, 1)

    arrow = {
        "Flat": "A ",
        "SingleUp": "C  ",
        "DoubleUp": "CC ",
        "TripleUp": "CCC ",
        "SingleDown": "D ",
        "DoubleDown": "DD ",
        "TripleDown": "DDD ",
        "FortyFiveUp": "G ",
        "FortyFiveDown": "H ",
        "NOT COMPUTABLE": "-- ",
        "NONE": "-- ",
    }.get(trend_direction, "?")

    return {
        "bg_val": current_val,
        "delta": delta_val,
        "arrow": arrow,
        "raw_time_ms": current_time_ms,
        "direction": trend_direction,
    }


# ---------- Determine alert colour ----------

def get_alert_color(bg_data, minutes):
    global STALE_MIN, LOW_THRESHOLD, HIGH_THRESHOLD
    global ALERT_DOUBLE_UP, ALERT_DOUBLE_DOWN

    bg_val    = bg_data["bg_val"]
    direction = bg_data["direction"]

    stale       = minutes >= STALE_MIN
    low         = bg_val < LOW_THRESHOLD
    double_up   = ALERT_DOUBLE_UP   and direction == "DoubleUp"
    double_down = ALERT_DOUBLE_DOWN and direction == "DoubleDown"
    high        = bg_val > HIGH_THRESHOLD

    if stale or low or double_up or double_down:
        return RED

    if high:
        return YELLOW
    return WHITE


# ---------- Drawing ----------

def draw_screen(lcd, bg_data):
    global DISPLAY_UNITS

    lcd.fill(BLACK)

    if not bg_data:
        lcd.text("NO DATA", 5, 50, WHITE)
        lcd.text("Retrying...", 5, 70, WHITE)
        lcd.show()
        return

    BOTTOM_ROW_H = max(SMALL_H, ARROWS)
    total_height = SMALL_H + BG_H + BOTTOM_ROW_H + 2 * LINE_GAP

    y_offset = (DISPLAY_HEIGHT - total_height) // 2

    y_time  = y_offset
    y_bg    = y_time  + SMALL_H + LINE_GAP
    y_delta = y_bg    + BG_H    + LINE_GAP

    raw_time_s = bg_data["raw_time_ms"] // 1000
    minutes = int((utime.time() - raw_time_s) // 60)
    if minutes < 0:
        minutes = 0
    time_str = "{} mins ago".format(minutes)

    alert_color = get_alert_color(bg_data, minutes)

    CWriter.set_textpos(lcd, y_time, center_x(wri_small, time_str))
    wri_small.setcolor(alert_color, BLACK)
    wri_small.printstring(time_str)

    bg_val = bg_data["bg_val"]
    if DISPLAY_UNITS == "mgdl":
        bg_text = str(int(round(bg_val)))
    else:
        bg_text = "{:.1f}".format(bg_val)

    CWriter.set_textpos(lcd, y_bg, center_x(wri_bg, bg_text))
    wri_bg.setcolor(alert_color, BLACK)
    wri_bg.printstring(bg_text)

    arrow_text = bg_data["arrow"]
    delta_val = bg_data["delta"]

    if delta_val is None:
        delta_text = "N/A"
    else:
        if DISPLAY_UNITS == "mgdl":
            delta_text = "{:+.0f}".format(delta_val)
        else:
            delta_text = "{:+.1f}".format(delta_val)

    half = DISPLAY_WIDTH // 2
    arrow_w = wri_arrows.stringlen(arrow_text)
    delta_w = wri_small.stringlen(delta_text)

    x_arrow = (half - arrow_w) // 2
    x_delta = half + (half - delta_w) // 2

    delta_v_offset = (ARROWS - SMALL_H) // 2

    CWriter.set_textpos(lcd, y_delta, x_arrow)
    wri_arrows.setcolor(alert_color, BLACK)
    wri_arrows.printstring(arrow_text)

    CWriter.set_textpos(lcd, y_delta + delta_v_offset, x_delta)
    wri_small.setcolor(alert_color, BLACK)
    wri_small.printstring(delta_text)

    lcd.show()


# ---------- Main ----------

def main():
    print("Iris Classic: app_main v1.0.1 starting")
    """
    Safe entry point for app_main.
    Any fatal error will be shown on the LCD and then we reboot,
    so the device does not freeze on a stale screen.
    """
    lcd = None
    try:
        lcd = ST7735()
        lcd.fill(BLACK)
        lcd.show()

        _main_impl(lcd)

    except Exception as e:
        print("FATAL error in app_main.main():", e)
        try:
            if lcd is None:
                lcd = ST7735()
            lcd.fill(BLACK)
            lcd.text("APP ERROR", 5, 40, WHITE)
            lcd.text("Rebooting...", 5, 60, WHITE)
            lcd.show()
        except Exception:
            pass

        utime.sleep(0)
        machine.reset()


def _main_impl(lcd):
    global DISPLAY_WIDTH, DISPLAY_HEIGHT
    global wri_small, wri_bg, wri_arrows
    global WIFI_SSID, WIFI_PASSWORD, NS_URL, NS_TOKEN, API_ENDPOINT
    global DISPLAY_UNITS, STALE_MIN, LOW_THRESHOLD, HIGH_THRESHOLD
    global ALERT_DOUBLE_UP, ALERT_DOUBLE_DOWN
    global user_config, HAS_CONFIG

    # Show the logo immediately
    draw_boot_logo(lcd)

    DISPLAY_WIDTH  = lcd.width
    DISPLAY_HEIGHT = lcd.height

    check_factory_reset(lcd)

    # -------- Config handling --------
    if not HAS_CONFIG:
        print("No valid config.py found, starting AP config portal")
        ap_config_portal(lcd)

        # After portal, try to load the new config
        try:
            import config as cfg
            user_config = cfg
            if hasattr(user_config, "WIFI_SSID") and hasattr(user_config, "NS_URL"):
                HAS_CONFIG = True
            else:
                HAS_CONFIG = False
        except ImportError:
            user_config = None
            HAS_CONFIG = False

        if not HAS_CONFIG:
            lcd.fill(BLACK)
            lcd.text("Config error", 5, 40, WHITE)
            lcd.text("Rebooting...", 5, 60, WHITE)
            lcd.show()
            utime.sleep(0)
            machine.reset()
            return

    # Load config values
    WIFI_SSID     = getattr(user_config, "WIFI_SSID", "")
    WIFI_PASSWORD = getattr(user_config, "WIFI_PASSWORD", "")
    NS_URL        = getattr(user_config, "NS_URL", "")
    NS_TOKEN      = getattr(user_config, "NS_TOKEN", "")
    API_ENDPOINT  = getattr(user_config, "API_ENDPOINT", "/api/v1/entries/sgv.json?count=2")

    DISPLAY_UNITS = getattr(user_config, "DISPLAY_UNITS", "mmol")
    if DISPLAY_UNITS not in ("mmol", "mgdl"):
        DISPLAY_UNITS = "mmol"

    STALE_MIN      = float(getattr(user_config, "STALE_MIN", 7.0))
    LOW_THRESHOLD  = float(getattr(user_config, "LOW_THRESHOLD", 4.0))
    HIGH_THRESHOLD = float(getattr(user_config, "HIGH_THRESHOLD", 11.0))

    ALERT_DOUBLE_UP   = bool(getattr(user_config, "ALERT_DOUBLE_UP", True))
    ALERT_DOUBLE_DOWN = bool(getattr(user_config, "ALERT_DOUBLE_DOWN", True))

    print("Using units:", DISPLAY_UNITS)
    print("Stale >=", STALE_MIN, "Low <", LOW_THRESHOLD, "High >", HIGH_THRESHOLD)
    print("Alert DoubleUp:", ALERT_DOUBLE_UP, "DoubleDown:", ALERT_DOUBLE_DOWN)

    wri_small  = CWriter(lcd, font_main,   fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    wri_bg     = CWriter(lcd, font_bg,     fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    wri_arrows = CWriter(lcd, font_arrows, fgcolor=WHITE, bgcolor=BLACK, verbose=False)

    # -------- Wi-Fi boot with animated logo --------
    wifi_fail = 0
    while True:
        try:
            # animate "Connecting" while we try Wi-Fi
            for i in range(3):
                draw_logo_with_status(lcd, "Connecting", "Wi-Fi", i % 4)
                utime.sleep(0)

            connect_wifi(lcd)
            wifi_fail = 0
            break
        except Exception as e:
            wifi_fail += 1
            print("Wi-Fi/NTP error during boot (attempt {}):".format(wifi_fail), e)
            utime.sleep(0)
            if wifi_fail >= 3:
                print("Too many Wi-Fi failures, rebooting")
                lcd.fill(BLACK)
                lcd.text("Wi-Fi FAILED", 5, 40, WHITE)
                lcd.text("Rebooting...", 5, 60, WHITE)
                lcd.show()
                utime.sleep(0)
                machine.reset()
                return

    print("Post-connect wait to stabilize Wi-Fi...")
    for i in range(3):
        draw_logo_with_status(lcd, "Starting", "", i % 4)
        utime.sleep(0)
        heartbeat("wifi-wait")

    # -------- Main loop --------
    processed_data = None
    sync_counter = 0
    last_cmd_check = utime.time()

    while True:
        # Periodic NTP re-sync
        if sync_counter >= SYNC_INTERVAL:
            try:
                ntptime.settime()
                
            except Exception as e:
                print("NTP re-sync error:", e)
            sync_counter = 0

        sync_counter += 1

        print("\n--- Fetching new data ---")
        try:
            raw = fetch_glucose_data()
        except OSError as e:
            print("Network error in main loop:", e)
            try:
                connect_wifi(lcd)
            except Exception as e2:
                print("Re-connect failed:", e2)
            raw = None
        except Exception as e:
            print("Other error in main loop:", e)
            raw = None

        new_data = None
        if raw is not None:
            new_data = parse_and_calculate(raw)

        if new_data:
            processed_data = new_data
        else:
            print("Fetch failed; using last data.")

        if processed_data:
            draw_screen(lcd, processed_data)
        else:
            draw_screen(lcd, None)

        # Remote command check (reboot / force update)
        now = utime.time()
        if now - last_cmd_check >= REMOTE_CHECK_INTERVAL:
            last_cmd_check = now
            try:
                bootloader.check_remote_commands()
            except Exception as e:
                print("Remote command check failed:", e)

        heartbeat("main-loop")
        utime.sleep(5)


# Keep this at the very end of app_main.py
if __name__ == "__main__":
    main()





