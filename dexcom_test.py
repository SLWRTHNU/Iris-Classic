# dexcom_test.py - Run this directly in the MicroPython shell to test Dexcom Share
# Paste into Thonny editor and run with %Run -c $EDITOR_CONTENT

import usocket, ssl, utime, network

# ---- CONFIG: paste your credentials here ----
USERNAME = "sennaloop"
PASSWORD = "YOUR_PASSWORD_HERE"   # fill in
REGION   = "ous"                  # "us" or "ous"
# ---------------------------------------------

APP_ID = "d8665ade-9673-4e27-9ff6-92db4ce13d13"
host   = "shareous1.dexcom.com" if REGION == "ous" else "share2.dexcom.com"

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
        print("No headers found, raw:", raw[:200])
        return None, None, None

    head = raw[:sep].decode("utf-8", "ignore")
    body_raw = raw[sep+4:].decode("utf-8", "ignore")
    status = int(head.split("\r\n")[0].split()[1])

    # Print full headers so we can see transfer-encoding etc.
    print("--- HEADERS ---")
    print(head)
    print("--- RAW BODY ---")
    print(repr(body_raw[:300]))

    # Chunked decode
    if "transfer-encoding: chunked" in head.lower():
        print("(chunked — decoding...)")
        body_str = ""
        pos = 0
        while pos < len(body_raw):
            nl = body_raw.find("\r\n", pos)
            if nl < 0:
                break
            try:
                chunk_len = int(body_raw[pos:nl], 16)
            except:
                break
            if chunk_len == 0:
                break
            body_str += body_raw[nl+2 : nl+2+chunk_len]
            pos = nl + 2 + chunk_len + 2
    else:
        body_str = body_raw

    print("--- DECODED BODY ---")
    print(body_str[:500])
    return status, head, body_str


# Step 1: Login
print("\n=== LOGIN ===")
login_body = '{{"accountName":"{}","password":"{}","applicationId":"{}"}}'.format(
    USERNAME, PASSWORD, APP_ID)
status, head, body = post(host, "/ShareWebServices/Services/General/LoginPublisherAccountByName", login_body)
if not body:
    print("Login failed")
    raise SystemExit

session = body.strip().strip('"')
print("Session:", session)

if session == "00000000-0000-0000-0000-000000000000":
    print("BAD CREDENTIALS — wrong username or password")
    raise SystemExit

# Step 2: Fetch readings
print("\n=== READINGS ===")
path = "/ShareWebServices/Services/Publisher/ReadPublisherLatestGlucoseValues?sessionId={}&minutes=1440&maxCount=2".format(session)
status, head, body = post(host, path)

if not body or body.strip() == "[]":
    print("\nEmpty readings []")
    print("=> Dexcom Share is not set up or has no followers.")
    print("=> In the Dexcom app: Share > Enable > Invite a follower (any email).")
else:
    # Quick parse of first reading
    def find_int(s, key, start=0):
        i = s.find(key, start)
        if i < 0: return None, -1
        i += len(key)
        while i < len(s) and s[i] in " \t\r\n": i += 1
        j = i
        if j < len(s) and s[j] == '-': j += 1
        while j < len(s) and s[j].isdigit(): j += 1
        if j == i: return None, -1
        return int(s[i:j]), j

    mgdl, _ = find_int(body, '"Value":')
    trend, _ = find_int(body, '"Trend":')
    trends = {1:"↑↑", 2:"↑", 3:"↗", 4:"→", 5:"↘", 6:"↓", 7:"↓↓"}
    arrow = trends.get(trend, "?")

    mmol = round(mgdl / 18.0, 1) if mgdl else None
    print("\n=== RESULT ===")
    print("BG:    {} mg/dL  /  {} mmol/L".format(mgdl, mmol))
    print("Trend: {} ({})".format(arrow, trend))
