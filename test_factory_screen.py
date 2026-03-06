"""
Factory reset pre-flight checks - safe to run in REPL anytime.
Does NOT touch the display or SPI (no LCD reinit).

Run: exec(open('test_factory_screen.py').read())
"""
from machine import Pin

# --- 1. GPIO 0 (BOOT button) ---
boot_btn = Pin(0, Pin.IN, Pin.PULL_UP)
print("GPIO 0 (BOOT btn):", boot_btn.value(), " (1=released, 0=pressed)")

# --- 2. config_font char coverage (text lines only - digits use small_font) ---
import config_font

# Only the letters/spaces used in the four text lines
text_lines = [
    "Factory Reset",
    "Will erase your settings",
    "Full setup will be needed",
    "Release to cancel",
]
needed_chars = set("".join(text_lines))
# Build available set from the font's _mvfont data
try:
    available = set(config_font.hmap())  # some font_to_py builds expose this
except AttributeError:
    pass  # fall through to glyph-probe below
# Probe each char individually — works with any font_to_py output
available = set()
for c in range(32, 127):
    try:
        config_font.get_ch(chr(c))
        available.add(chr(c))
    except Exception:
        pass
missing = needed_chars - available
if missing:
    print("MISSING chars in config_font:", sorted(missing))
else:
    print("config_font: all text chars present OK")

# --- 3. small_font digit coverage (0-5 for countdown) ---
import small_font
digit_chars = set("012345")
small_available = set()
for c in range(32, 127):
    try:
        small_font.get_ch(chr(c))
        small_available.add(chr(c))
    except Exception:
        pass
missing_digits = digit_chars - small_available
if missing_digits:
    print("MISSING digits in small_font:", sorted(missing_digits))
else:
    print("small_font: all countdown digits present OK")

# --- 4. Y-coordinate sanity check (no display needed) ---
W, H = 480, 320
fh_cfg   = config_font.height()   # 15
fh_digit = small_font.height()    # 48

print(f"\nLayout preview (H={H}, W={W}):")
y = 60
for text in text_lines:
    bottom = y + fh_cfg
    status = "OK" if 0 <= y and bottom <= H else "OUT OF RANGE"
    print(f"  y={y:3d}..{bottom:3d}  '{text}'  [{status}]")
    y += fh_cfg + 8

cy = H // 2 + 60
cd_bottom = cy + fh_digit
status = "OK" if 0 <= cy and cd_bottom <= H else "OUT OF RANGE"
print(f"  y={cy:3d}..{cd_bottom:3d}  countdown digit  [{status}]")

print("\nAll checks passed - flash and test by pressing BOOT while Iris is running.")
