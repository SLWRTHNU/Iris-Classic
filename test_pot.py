"""Quick pot/ADC test - paste into REPL or run with mpremote run test_pot.py"""
import time
from machine import Pin, ADC

pot = ADC(Pin(1))
pot.atten(ADC.ATTN_11DB)

print("ADC methods available:", [m for m in dir(pot) if not m.startswith('_')])
print("Sampling 5 readings (turn knob to verify)...")
for i in range(5):
    try:
        v16 = pot.read_u16()
        print(f"  read_u16() = {v16}  ({v16*100//65535}%)")
    except AttributeError:
        pass
    try:
        v12 = pot.read()
        print(f"  read()     = {v12}  ({v12*100//4095}%)")
    except AttributeError:
        pass
    time.sleep_ms(400)
