# ft6336.py - FT6336U capacitive touch controller driver
#
# Waveshare 3.5inch Capacitive Touch LCD uses:
#   ST7796S  -> display (SPI, already wired)
#   FT6336U  -> touch   (I2C, new wires)
#
# Wiring — 4 new wires:
#
#   Display connector label | Connect to ESP32-S3 GPIO
#   ----------------------- | ------------------------
#   TP_SDA  (I2C data)      | GPIO 6
#   TP_SCL  (I2C clock)     | GPIO 7
#   TP_INT  (interrupt)     | GPIO 3   (LOW when screen touched)
#   TP_RST  (reset)         | GPIO 8   (active-low; held HIGH normally)
#   GND                     | GND      (already have one, share it)
#   VCC / 3V3               | 3.3V     (already have one, share it)

from machine import Pin, I2C
import utime

_TP_SDA = 6
_TP_SCL = 7
_TP_INT = 3
_TP_RST = 8

_FT_ADDR = 0x38   # fixed I2C address for all FT6x36 variants


class FT6336:
    """
    FT6336U capacitive touch driver.

    Tap detection uses TP_INT (fast, no I2C overhead).
    get_touch() reads I2C for exact (x, y) coordinates (future use).

    poll_tap() returns True once per debounced tap — call every ~50 ms.
    """

    def __init__(self,
                 sda=_TP_SDA, scl=_TP_SCL,
                 int_pin=_TP_INT, rst_pin=_TP_RST,
                 debounce_ms=400):
        # Reset the touch controller
        self._rst = Pin(rst_pin, Pin.OUT, value=0)
        utime.sleep_ms(20)
        self._rst.value(1)
        utime.sleep_ms(300)   # FT6336U needs ~200 ms after reset to be ready

        self._irq = Pin(int_pin, Pin.IN, Pin.PULL_UP)
        self._i2c = I2C(0, sda=Pin(sda), scl=Pin(scl), freq=400_000)
        self._debounce_ms = debounce_ms
        self._last_tap_ms = 0

        # Scan so we know if the device is reachable
        found = self._i2c.scan()
        if _FT_ADDR not in found:
            print("FT6336: WARNING - device not found on I2C. Scan:", [hex(d) for d in found])
        else:
            print("FT6336: found at 0x{:02X}".format(_FT_ADDR))

        # Set interrupt mode to TRIGGER (0x01) so INT stays LOW while touched.
        # Default is POLLING (0x00) where INT only pulses briefly — too fast to
        # catch at 50 ms poll intervals.
        try:
            self._i2c.writeto_mem(_FT_ADDR, 0xA4, bytes([0x01]))
        except OSError:
            print("FT6336: could not set interrupt mode (device may not be present)")

    def is_touched(self):
        """True if screen is currently being pressed (reads INT pin, no I2C)."""
        return self._irq.value() == 0

    def poll_tap(self):
        """
        Returns True once per tap event (debounced).
        Polls via I2C — does not rely on the INT pin.
        Call from an async task every ~50 ms.
        """
        now = utime.ticks_ms()
        if utime.ticks_diff(now, self._last_tap_ms) < self._debounce_ms:
            return False
        if self.get_touch() is None:
            return False
        self._last_tap_ms = now
        return True

    def get_touch(self):
        """
        Returns (x, y) of the first touch point, or None if not touched.
        Uses I2C — for future coordinate-aware gestures.
        """
        try:
            data = self._i2c.readfrom_mem(_FT_ADDR, 0x02, 5)
            if (data[0] & 0x0F) == 0:
                return None
            x = ((data[1] & 0x0F) << 8) | data[2]
            y = ((data[3] & 0x0F) << 8) | data[4]
            return (x, y)
        except OSError:
            return None
