# display_2inch.py
# ST7789V display driver for Waveshare 2inch LCD Module
# Resolution: 320x240 (landscape), RGB565
# Controller: ST7789V via 4-wire SPI

from machine import Pin, SPI, PWM
import framebuf
import micropython

# ── GPIO pin assignments (same ESP32-S3 board as Iris Classic) ──────────────
# Adjust these to match your physical wiring if different.
LCD_DC  = 14   # Data/Command
LCD_CS  = 9    # Chip Select
SCK     = 10   # SPI Clock
MOSI    = 11   # SPI MOSI
LCD_RST = 13   # Reset (active-low pulse)
LCD_BL  = 15   # Backlight PWM


@micropython.viper
def _bswap16_inplace(buf):
    b = ptr8(buf)
    n = int(len(buf))
    i = 0
    while i < n:
        t = b[i]
        b[i] = b[i + 1]
        b[i + 1] = t
        i += 2


@micropython.viper
def _bswap16_copy(src, src_off: int, dst, nbytes: int):
    s = ptr8(src)
    d = ptr8(dst)
    i = 0
    while i < nbytes:
        lo = s[src_off + i]
        hi = s[src_off + i + 1]
        d[i] = hi
        d[i + 1] = lo
        i += 2


class Palette(framebuf.FrameBuffer):
    def __init__(self):
        buf = bytearray(4)
        super().__init__(buf, 2, 1, framebuf.RGB565)
    def bg(self, color): self.pixel(0, 0, color)
    def fg(self, color): self.pixel(1, 0, color)


class lcd_st7789(framebuf.FrameBuffer):
    """
    ST7789V driver for Waveshare 2inch LCD Module in landscape orientation.
    Physical display: 240(H) x 320(V) pixels.
    With MADCTL=0x70 (MX+MV swap) the logical dimensions become 320 wide x 240 tall.
    """

    def __init__(self, fb=None, baud=40_000_000, bl=100):
        # Landscape: width=320, height=240
        self.width  = 320
        self.height = 240

        self.cs  = Pin(LCD_CS,  Pin.OUT, value=1)
        self.rst = Pin(LCD_RST, Pin.OUT, value=1)
        self.dc  = Pin(LCD_DC,  Pin.OUT, value=1)

        self._bl_pwm = PWM(Pin(LCD_BL))
        self._bl_pwm.freq(1000)
        self._bl_pwm.duty_u16(0)

        # ST7789V is write-only; no MISO needed
        self.spi = SPI(1, baud, polarity=0, phase=0,
                       sck=Pin(SCK), mosi=Pin(MOSI))

        # Line buffer for partial-update row streaming
        self._linebuf = bytearray(self.width * 2)

        self.buffer = fb

        if fb is None:
            dummy = bytearray(2)
            super().__init__(dummy, 1, 1, framebuf.RGB565)
        else:
            super().__init__(fb, self.width, self.height, framebuf.RGB565)

        self.palette = Palette()

        self._init_display()

        # Push a clean black frame before enabling backlight to avoid stale-frame flash
        if fb is not None:
            self.fill(0x0000)
            self.show()

        self.bl_ctrl(bl)

    # ── Low-level SPI helpers ────────────────────────────────────────────────

    def write_cmd(self, cmd):
        self.dc(0); self.cs(0)
        self.spi.write(bytearray([cmd]))
        self.cs(1)

    def write_data(self, data):
        self.dc(1); self.cs(0)
        self.spi.write(bytearray([data]) if isinstance(data, int) else data)
        self.cs(1)

    def bl_ctrl(self, duty):
        """Set backlight brightness 0–100."""
        self._bl_pwm.duty_u16(int(duty * 655.35))

    def _set_window(self, x0, y0, x1, y1):
        self.write_cmd(0x2A)
        self.write_data(bytearray([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF]))
        self.write_cmd(0x2B)
        self.write_data(bytearray([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF]))
        self.write_cmd(0x2C)

    # ── Display initialisation ───────────────────────────────────────────────

    def _init_display(self):
        import utime as time

        # Hardware reset
        self.rst(0)
        time.sleep_ms(15)
        self.rst(1)
        time.sleep_ms(120)

        # Sleep out
        self.write_cmd(0x11)
        time.sleep_ms(120)

        # Memory access control
        # MADCTL = 0x70: MY=0, MX=1, MV=1, ML=1, RGB — landscape 320×240
        # If colours appear wrong try 0x60 (without ML) or swap the BGR bit (0x78 / 0x68).
        self.write_cmd(0x36); self.write_data(0x70)

        # Colour format: 16-bit RGB565
        self.write_cmd(0x3A); self.write_data(0x05)

        # Porch setting
        self.write_cmd(0xB2)
        self.write_data(bytearray([0x0C, 0x0C, 0x00, 0x33, 0x33]))

        # Gate control
        self.write_cmd(0xB7); self.write_data(0x35)

        # VCOM setting
        self.write_cmd(0xBB); self.write_data(0x19)

        # LCM control
        self.write_cmd(0xC0); self.write_data(0x2C)

        # VDV and VRH enable
        self.write_cmd(0xC2); self.write_data(0x01)

        # VRH set
        self.write_cmd(0xC3); self.write_data(0x12)

        # VDV set
        self.write_cmd(0xC4); self.write_data(0x20)

        # Frame rate control (60 Hz)
        self.write_cmd(0xC6); self.write_data(0x0F)

        # Power control 1
        self.write_cmd(0xD0)
        self.write_data(bytearray([0xA4, 0xA1]))

        # Positive voltage gamma
        self.write_cmd(0xE0)
        self.write_data(bytearray([
            0xD0, 0x04, 0x0D, 0x11, 0x13, 0x2B, 0x3F,
            0x54, 0x4C, 0x18, 0x0D, 0x0B, 0x1F, 0x23
        ]))

        # Negative voltage gamma
        self.write_cmd(0xE1)
        self.write_data(bytearray([
            0xD0, 0x04, 0x0C, 0x11, 0x13, 0x2C, 0x3F,
            0x44, 0x51, 0x2F, 0x1F, 0x1F, 0x20, 0x23
        ]))

        # Display inversion on (required for correct colours on ST7789V)
        self.write_cmd(0x21)

        # Display on
        self.write_cmd(0x29)
        time.sleep_ms(20)

    # ── Framebuffer flush methods ────────────────────────────────────────────

    def show(self):
        if self.buffer is None:
            raise RuntimeError("No framebuffer allocated. Use show_rgb565_bin().")
        self._set_window(0, 0, self.width - 1, self.height - 1)
        self.dc(1); self.cs(0)
        _bswap16_inplace(self.buffer)
        self.spi.write(self.buffer)
        _bswap16_inplace(self.buffer)
        self.cs(1)

    def show_rect(self, x, y, w, h):
        if self.buffer is None:
            raise RuntimeError("No framebuffer allocated. Use show_rgb565_bin().")
        if x < 0: w += x; x = 0
        if y < 0: h += y; y = 0
        if x + w > self.width:  w = self.width - x
        if y + h > self.height: h = self.height - y
        if w <= 0 or h <= 0:
            return

        x0, y0 = x, y
        x1, y1 = x + w - 1, y + h - 1
        self._set_window(x0, y0, x1, y1)

        self.dc(1); self.cs(0)

        row_bytes = self.width * 2
        start = y0 * row_bytes + x0 * 2
        copy_bytes = w * 2

        src = self.buffer
        linebuf = self._linebuf

        for row in range(h):
            si = start + row * row_bytes
            _bswap16_copy(src, si, linebuf, copy_bytes)
            self.spi.write(memoryview(linebuf)[:copy_bytes])

        self.cs(1)

    def show_rgb565_bin(self, path, w=320, h=240):
        """Stream a raw RGB565 little-endian binary file directly to the display."""
        if w != self.width or h != self.height:
            raise ValueError("Expected %dx%d" % (self.width, self.height))

        self._set_window(0, 0, self.width - 1, self.height - 1)

        row = self._linebuf
        self.dc(1); self.cs(0)

        with open(path, "rb") as f:
            for _ in range(self.height):
                n = f.readinto(row)
                if n != self.width * 2:
                    self.cs(1)
                    raise ValueError("Unexpected EOF / wrong row size: %d" % n)
                _bswap16_inplace(row)
                self.spi.write(row)
                _bswap16_inplace(row)

        self.cs(1)
