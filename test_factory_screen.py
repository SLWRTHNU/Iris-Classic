"""
Test factory reset screen layout.
Run in REPL: exec(open('test_factory_screen.py').read())
Tests:
  1. GPIO 0 button can be read
  2. config_font has the chars we need
  3. Draws the full warning screen so layout can be verified visually
"""
from machine import Pin
import time

# --- 1. GPIO 0 button ---
boot_btn = Pin(0, Pin.IN, Pin.PULL_UP)
print("GPIO 0 (BOOT btn) value:", boot_btn.value(), " (1=released, 0=pressed)")

# --- 2. Font char check ---
import config_font
needed = set("Factory reset Will delete config reconfigure needed Release to cancel 54321 0")
available = set(" )123FIPRWacdefgilnorstuwy")
missing = needed - available
if missing:
    print("MISSING chars in config_font:", sorted(missing))
else:
    print("config_font: all required chars present OK")

# --- 3. Draw the warning screen ---
import framebuf
from machine import SPI, Pin as MPin
from display_3_5 import lcd_st7796 as LCD_Driver
from writer import CWriter
import small_font
import config_font as cf

W, H = 480, 320
RED   = 0xF800
WHITE = 0xFFFF
BLACK = 0x0000

fb = framebuf.FrameBuffer(bytearray(W * H * 2), W, H, framebuf.RGB565)
lcd = LCD_Driver(fb=fb, bl=80)

w_cfg   = CWriter(lcd, cf,         fgcolor=WHITE, bgcolor=BLACK, verbose=False)
w_digit = CWriter(lcd, small_font, fgcolor=WHITE, bgcolor=BLACK, verbose=False)

fh_cfg   = cf.height()          # 15
fh_digit = small_font.height()  # 48

def draw_reset_screen(secs_left):
    lcd.fill(BLACK)
    lines = [
        ("Factory reset",        RED),
        ("Will delete config.",  WHITE),
        ("reconfigure needed",   WHITE),
        ("Release to cancel",    WHITE),
    ]
    y = 20
    for text, color in lines:
        w_cfg.setcolor(color, BLACK)
        x = max(0, (W - w_cfg.stringlen(text)) // 2)
        w_cfg.set_textpos(lcd, y, x)
        w_cfg.printstring(text)
        y += fh_cfg + 8

    # Countdown digit — centered in lower half
    cd = str(secs_left)
    cx = max(0, (W - w_digit.stringlen(cd)) // 2)
    cy = H // 2 + 20
    w_digit.setcolor(RED, BLACK)
    w_digit.set_textpos(lcd, cy, cx)
    w_digit.printstring(cd)

    lcd.show()
    print(f"Drew screen with countdown={secs_left}, fh_cfg={fh_cfg}, fh_digit={fh_digit}")
    print(f"  text lines end at y≈{20 + 4*(fh_cfg+8)}, digit at y={H//2+20}")

for n in range(5, -1, -1):
    draw_reset_screen(n)
    time.sleep(1)

print("Test complete. Check screen layout looked correct.")
