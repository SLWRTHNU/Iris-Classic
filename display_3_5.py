from machine import Pin, SPI, PWM
import framebuf
import time
import micropython

LCD_DC  = 14
LCD_CS  = 9
SCK     = 10
MOSI    = 11
MISO    = 12
LCD_RST = 13
LCD_BL  = 15

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

class lcd_st7796(framebuf.FrameBuffer):
    def __init__(self, fb=None, baud=40_000_000, bl=100):
        self.width = 480
        self.height = 320

        self.cs  = Pin(LCD_CS,  Pin.OUT, value=1)
        self.rst = Pin(LCD_RST, Pin.OUT, value=1)
        self.dc  = Pin(LCD_DC,  Pin.OUT, value=1)

        self._bl_pwm = PWM(Pin(LCD_BL))
        self._bl_pwm.freq(1000)
        self._bl_pwm.duty_u16(0)

        self.spi = SPI(1, baud, polarity=0, phase=0,
                       sck=Pin(SCK), mosi=Pin(MOSI), miso=Pin(MISO))

        # Always keep a line buffer (small)
        self._linebuf = bytearray(self.width * 2)

        # If a framebuffer is provided, use it. Otherwise, don't allocate one.
        self.buffer = fb

        if fb is None:
            # dummy 2-byte buffer so FrameBuffer can be constructed without huge RAM
            dummy = bytearray(2)
            super().__init__(dummy, 1, 1, framebuf.RGB565)
        else:
            super().__init__(fb, self.width, self.height, framebuf.RGB565)

        self.palette = Palette()

        # Initialize display hardware BEFORE turning on backlight
        self._init_display()

        # Push a clean black frame to controller RAM before enabling backlight.
        # The controller retains its last frame after a soft reset, so without
        # this the stale image (e.g. logo from previous boot stage) would flash
        # briefly the moment the backlight comes on.
        if fb is not None:
            self.fill(0x0000)
            self.show()

        # Backlight on — screen is already black, no stale-frame flash.
        self.bl_ctrl(bl)

    def write_cmd(self, cmd):
        self.dc(0); self.cs(0)
        self.spi.write(bytearray([cmd]))
        self.cs(1)

    def write_data(self, data):
        self.dc(1); self.cs(0)
        self.spi.write(bytearray([data]) if isinstance(data, int) else data)
        self.cs(1)

    def bl_ctrl(self, duty):
        self._bl_pwm.duty_u16(int(duty * 655.35))

    def _set_window(self, x0, y0, x1, y1):
        self.write_cmd(0x2A)
        self.write_data(bytearray([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF]))
        self.write_cmd(0x2B)
        self.write_data(bytearray([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF]))
        self.write_cmd(0x2C)

    def show_rgb565_bin(self, path, w=480, h=320):
        # path is raw RGB565 LITTLE-ENDIAN (your converter output)
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

                # swap to what LCD expects on the wire
                _bswap16_inplace(row)
                self.spi.write(row)
                _bswap16_inplace(row)

        self.cs(1)

    def _init_display(self):
        """Initialize display hardware - called from __init__"""
        import utime as time

        # Hardware reset
        self.rst(0)
        time.sleep_ms(20)
        self.rst(1)
        time.sleep_ms(120)

        # Sleep out
        self.write_cmd(0x11)
        time.sleep_ms(120)

        # Pixel / addressing setup
        self.write_cmd(0x36); self.write_data(0x28)  # MADCTL
        self.write_cmd(0x3A); self.write_data(0x05)  # COLMOD: RGB565
        self.write_cmd(0xB4); self.write_data(0x01)  # Display inversion
        self.write_cmd(0x21)  # Inversion ON

        # Display ON
        self.write_cmd(0x29)
        time.sleep_ms(20)


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
