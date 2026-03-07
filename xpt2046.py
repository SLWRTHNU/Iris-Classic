# xpt2046.py - XPT2046 resistive touch controller driver
#
# Wiring (shares display SPI bus, 2 new wires only):
#   T_CLK  -> GPIO 10  (tap into existing SCK wire)
#   T_DIN  -> GPIO 11  (tap into existing MOSI wire)
#   T_DO   -> GPIO 12  (tap into existing MISO wire)
#   T_CS   -> GPIO 5   (new wire - keep HIGH for PENIRQ standby mode)
#   T_IRQ  -> GPIO 3   (new wire - goes LOW when screen is pressed)

from machine import Pin
import utime

TOUCH_CS  = 5
TOUCH_IRQ = 3


class XPT2046:
    """
    XPT2046 touch controller.

    Tap detection uses only T_IRQ (no SPI read required).
    T_CS is held HIGH, which puts the XPT2046 in PENIRQ standby mode —
    the chip actively monitors touch pressure and drives T_IRQ LOW on contact.

    Call poll_tap() every ~50 ms to get debounced tap events.
    """

    def __init__(self, cs_pin=TOUCH_CS, irq_pin=TOUCH_IRQ, debounce_ms=400):
        self.cs  = Pin(cs_pin,  Pin.OUT, value=1)   # HIGH = PENIRQ standby
        self.irq = Pin(irq_pin, Pin.IN, Pin.PULL_UP) # LOW when touched
        self._debounce_ms = debounce_ms
        self._last_tap_ms = 0

    def is_touched(self):
        """True if the screen is currently being pressed."""
        return self.irq.value() == 0

    def poll_tap(self):
        """
        Returns True once per tap event (leading edge, debounced).
        Call from an async task every ~50 ms.
        """
        if not self.is_touched():
            return False
        now = utime.ticks_ms()
        if utime.ticks_diff(now, self._last_tap_ms) < self._debounce_ms:
            return False
        self._last_tap_ms = now
        return True
