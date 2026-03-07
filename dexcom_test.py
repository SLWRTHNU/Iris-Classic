# dexcom_test.py - Run this directly in the MicroPython shell to test Dexcom Share
# Paste into Thonny editor and run with %Run -c $EDITOR_CONTENT

import usocket, ssl, utime

# ---- CONFIG ----
USERNAME = "sennaloop"
PASSWORD = "YOUR_PASSWORD_HERE"   # fill in
# ----------------

APP_ID = "d8665ade-9673-4e27-9ff6-92db4ce13d13"

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

    # Extract HTTP status code
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


def try_server(host):
    print("\n--- Trying:", host, "---")
    login_body = '{{"accountName":"{}","password":"{}","applicationId":"{}"}}'.format(
        USERNAME, PASSWORD, APP_ID)
    status, body = post(host, "/ShareWebServices/Services/General/LoginPublisherAccountByName", login_body)
    if not body:
        print("  Login: no response (HTTP {})".format(status))
        return False

    if status != 200:
        print("  Login HTTP {}: {}".format(status, body[:80]))
        return False

    session = body.strip().strip('"')
    if session == "00000000-0000-0000-0000-000000000000" or session.startswith('{'):
        print("  Login rejected:", session[:60])
        return False

    print("  Login OK (HTTP {}), session: {} ...".format(status, session[:8]))

    path = "/ShareWebServices/Services/Publisher/ReadPublisherLatestGlucoseValues?sessionId={}&minutes=1440&maxCount=2".format(session)
    status, body = post(host, path)
    print("  Readings HTTP {}: {}".format(status, repr(body[:100]) if body else "None"))

    if not body or body.strip() == "[]":
        print("  => Empty []")
        if body is not None and body.strip() == "[]":
            print()
            print("  ** G7/Share checklist (auth is valid, but no readings returned): **")
            print("  1. Open Dexcom G7 app -> Settings -> Share -> confirm Share is ON")
            print("  2. You MUST have at least one follower who has ACCEPTED the invite")
            print("     via the Dexcom Follow app (just sending the invite is not enough)")
            print("  3. Sensor must not be in warmup (2-hour warmup returns no data)")
            print("  4. G7 app must be running & connected in the background")
        return False

    # Parse
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
    return True


print("=== Dexcom Share server test ===")
found = False
for srv in ("share2.dexcom.com", "shareous1.dexcom.com"):
    if try_server(srv):
        print("\nWORKING SERVER:", srv)
        found = True
        break

if not found:
    print("\nNeither server returned readings.")
