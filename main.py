# main.py - ESP32-S3 Auto-boot
# Runs bootloader which checks for config.py
# If config exists: runs app_main
# If no config: runs setup mode

print("Iris Classic - Starting...")

try:
    import bootloader
    bootloader.main()
except Exception as e:
    print(f"Bootloader error: {e}")
    import sys
    sys.print_exception(e)
