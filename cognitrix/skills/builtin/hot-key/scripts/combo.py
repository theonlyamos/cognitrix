#!/usr/bin/env python3
"""Press multiple keys simultaneously (hotkey) using pyautogui.

Usage:
    python combo.py <key1> [key2] [key3] ...

Arguments:
    key1, key2, ...    Keys to press together (e.g., 'ctrl', 'c')

Requires: pyautogui (pip install pyautogui)
"""

import sys


def main():
    if len(sys.argv) < 2:
        print("Usage: python combo.py <key1> [key2] [key3] ...", file=sys.stderr)
        sys.exit(1)

    hotkeys = [key.lower() for key in sys.argv[1:]]

    try:
        import pyautogui
        pyautogui.hotkey(*hotkeys)
        print(f"Hotkey {'+'.join(hotkeys)} pressed successfully")
    except ImportError:
        print("Error: pyautogui is required but not installed.", file=sys.stderr)
        print("Install with: pip install pyautogui", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error pressing hotkey {'+'.join(hotkeys)}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()