# boot.py - Iris Classic ESP32-S3
# Runs on every boot, before main.py

from machine import Pin
import time
import os

print("\n" + "="*50)
print("Iris Classic - Boot Check")
print("="*50)

# GPIO 0 = BOOT button on ESP32-S3 dev boards
BOOT_BTN = Pin(0, Pin.IN, Pin.PULL_UP)

# Check if BOOT button is held during power-on
if BOOT_BTN.value() == 0:
    print("⚠️  BOOT button detected at startup")
    print("Checking for factory reset intent...")
    
    # Wait 1 second to confirm intent (prevents accidental triggers)
    time.sleep(1)
    
    if BOOT_BTN.value() == 0:
        print("\n" + "!"*50)
        print("🔧 FACTORY RESET TRIGGERED")
        print("!"*50)
        
        # Delete configuration file
        try:
            os.remove("config.py")
            print("✓ config.py deleted")
        except OSError:
            print("ℹ️  config.py not found (already in setup mode)")
        
        print("\nFactory reset complete!")
        print("Rebooting to setup mode in 2 seconds...")
        time.sleep(2)
        
        # Reboot to start fresh
        import machine
        machine.reset()
    else:
        print("BOOT released early - continuing normal boot")

print("✓ Normal boot sequence")
print("="*50 + "\n")
