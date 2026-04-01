#!/usr/bin/env python3
"""Press a single key using pyautogui.

Usage:
    python press.py <key>

Arguments:
    key    Name of the key to press (e.g., 'enter', 'esc', 'a', 'f1')

Requires: pyautogui (pip install pyautogui)
"""

import sys


def main():
    if len(sys.argv) < 2:
        print("Usage: python press.py <key>", file=sys.stderr)
        sys.exit(1)

    key = sys.argv[1].lower()

    try:
        import pyautogui
        pyautogui.press(key)
        print(f"Key '{key}' pressed successfully")
    except ImportError:
        print("Error: pyautogui is required but not installed.", file=sys.stderr)
        print("Install with: pip install pyautogui", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error pressing key '{key}': {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()