# dexcom_test.py - Run this directly in the MicroPython shell to test Dexcom Share
# Paste into Thonny editor and run with %Run -c $EDITOR_CONTENT

import usocket, ssl, utime

# ---- CONFIG ----
# CGM wearer's Dexcom account (the G7 user)
PUBLISHER_USER = "sennaloop"
PUBLISHER_PASS = "YOUR_PASSWORD_HERE"   # fill in

# Follower's Dexcom account (the person using Dexcom Follow app)
FOLLOWER_USER  = ""    # fill in - your own Dexcom username/email
FOLLOWER_PASS  = ""    # fill in - your own Dexcom password
# ----------------

APP_ID = "d8665ade-9673-4e27-9ff6-92db4ce13d13"
OUS_HOST = "shareous1.dexcom.com"

def post(host, path, body_str=""):
    body_bytes = body_str.encode() if body_str else b""
    req = (
        "POST {} HTTP/1.1\r\nHost: {}\r\n"
        "Content-Type: application/json\r\nAccept: application/json\r\n"
        "User-Agent: Dexcom Share/3.0.2.11 CFNetwork/711.2.23 Darwin/14.0.0\r\n"
        "Content-Length: {}\r\nConnection: close\r\n\r\n"
    ).format(path, host, len(body_bytes)).encode() + body_bytes

    addr = usocket.getaddrinfo(host, 443)[0][-1]
    s = usocket.socket()
    s.settimeout(15)
    s.connect(addr)
    s = ssl.wrap_socket(s, server_hostname=host)
    s.send(req)

    buf = bytearray()
    t0 = utime.ticks_ms()
    while utime.ticks_diff(utime.ticks_ms(), t0) < 10000:
        try:
            chunk = s.recv(512)
        except OSError:
            break
        if not chunk:
            break
        buf.extend(chunk)
    s.close()

    raw = bytes(buf)
    sep = raw.find(b"\r\n\r\n")
    if sep < 0:
        return None, None

    head = raw[:sep].decode("utf-8", "ignore")
    body_raw = raw[sep+4:].decode("utf-8", "ignore")

    status = None
    try:
        status = int(head.split(" ", 2)[1])
    except Exception:
        pass

    if "transfer-encoding: chunked" in head.lower():
        decoded = ""
        pos = 0
        while pos < len(body_raw):
            nl = body_raw.find("\r\n", pos)
            if nl < 0: break
            try:
                chunk_len = int(body_raw[pos:nl], 16)
            except:
                break
            if chunk_len == 0: break
            decoded += body_raw[nl+2 : nl+2+chunk_len]
            pos = nl + 2 + chunk_len + 2
        return status, decoded
    return status, body_raw


def login(host, username, password):
    body = '{{"accountName":"{}","password":"{}","applicationId":"{}"}}'.format(
        username, password, APP_ID)
    status, resp = post(host, "/ShareWebServices/Services/General/LoginPublisherAccountByName", body)
    if status != 200 or not resp:
        print("  Login HTTP {}: {}".format(status, (resp or "")[:60]))
        return None
    sid = resp.strip().strip('"')
    if not sid or len(sid) < 10 or sid == "00000000-0000-0000-0000-000000000000" or sid.startswith('{'):
        print("  Login rejected:", sid[:60])
        return None
    print("  Login OK, session:", sid[:8], "...")
    return sid


def read_glucose(host, session, endpoint_type):
    """endpoint_type: 'Publisher' or 'Follower'"""
    path = (
        "/ShareWebServices/Services/{}/ReadPublisherLatestGlucoseValues"
        "?sessionId={}&minutes=1440&maxCount=2"
    ).format(endpoint_type, session)
    status, body = post(host, path)
    print("  {} endpoint HTTP {}: {}".format(
        endpoint_type, status, repr(body[:80]) if body else "None"))
    if status == 200 and body and body.strip() not in ("[]", ""):
        return body
    return None


def parse_and_print(body):
    def find_int(s, key):
        i = s.find(key)
        if i < 0: return None
        i += len(key)
        while i < len(s) and s[i] in " \t": i += 1
        j = i
        if j < len(s) and s[j] == '-': j += 1
        while j < len(s) and s[j].isdigit(): j += 1
        return int(s[i:j]) if j > i else None
    mgdl  = find_int(body, '"Value":')
    trend = find_int(body, '"Trend":')
    mmol  = round(mgdl / 18.0, 1) if mgdl else None
    arrows = {1:"↑↑", 2:"↑", 3:"↗", 4:"→", 5:"↘", 6:"↓", 7:"↓↓"}
    print("  => BG: {} mg/dL / {} mmol/L  {}".format(mgdl, mmol, arrows.get(trend, "?")))


print("=== Dexcom Share server test ===")
print()

# --- Test 1: Publisher login, Publisher endpoint ---
print("-- Test 1: Publisher login + Publisher endpoint --")
sid = login(OUS_HOST, PUBLISHER_USER, PUBLISHER_PASS)
if sid:
    body = read_glucose(OUS_HOST, sid, "Publisher")
    if body:
        print("  SUCCESS via Publisher endpoint!")
        parse_and_print(body)
    else:
        # --- Test 2: Publisher login, Follower endpoint ---
        print()
        print("-- Test 2: Publisher login + Follower endpoint --")
        body = read_glucose(OUS_HOST, sid, "Follower")
        if body:
            print("  SUCCESS via Follower endpoint with publisher session!")
            print("  => Set DEXCOM_MODE = 'follower_endpoint' in config")
            parse_and_print(body)
        else:
            print("  Also empty.")

# --- Test 3: Follower login, Follower endpoint ---
print()
print("-- Test 3: Follower login + Follower endpoint --")
if not FOLLOWER_USER or not FOLLOWER_PASS:
    print("  Skipped: fill in FOLLOWER_USER / FOLLOWER_PASS above")
else:
    fsid = login(OUS_HOST, FOLLOWER_USER, FOLLOWER_PASS)
    if fsid:
        body = read_glucose(OUS_HOST, fsid, "Follower")
        if body:
            print("  SUCCESS via Follower login + Follower endpoint!")
            print("  => Use follower credentials in config")
            parse_and_print(body)
        else:
            print("  Also empty. Check that follower has accepted the invite.")
