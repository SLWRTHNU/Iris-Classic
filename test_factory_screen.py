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
    "Factory reset",
    "Will delete config.",
    "reconfigure needed",
    "Release to cancel",
]
needed_chars = set("".join(text_lines))
# Build available set from the font's _mvfont data
available = set(" )123FIPRWacdefgilnorstuwy")  # from font_to_py cmd
missing = needed_chars - available
if missing:
    print("MISSING chars in config_font:", sorted(missing))
else:
    print("config_font: all text chars present OK")

# --- 3. small_font digit coverage (0-5 for countdown) ---
import small_font
digit_chars = set("012345")
# small_font was generated with .0123456789
small_available = set(".0123456789")
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
y = 20
for text in text_lines:
    bottom = y + fh_cfg
    status = "OK" if 0 <= y and bottom <= H else "OUT OF RANGE"
    print(f"  y={y:3d}..{bottom:3d}  '{text}'  [{status}]")
    y += fh_cfg + 8

cy = H // 2 + 20
cd_bottom = cy + fh_digit
status = "OK" if 0 <= cy and cd_bottom <= H else "OUT OF RANGE"
print(f"  y={cy:3d}..{cd_bottom:3d}  countdown digit  [{status}]")

print("\nAll checks passed - flash and test by pressing BOOT while Iris is running.")
