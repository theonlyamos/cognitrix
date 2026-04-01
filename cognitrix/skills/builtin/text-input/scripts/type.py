#!/usr/bin/env python3
"""Type text using pyautogui.

Usage:
    python type.py <text>

Arguments:
    text    Text to type

Requires: pyautogui (pip install pyautogui)
"""

import sys


def main():
    if len(sys.argv) < 2:
        print("Usage: python type.py <text>", file=sys.stderr)
        sys.exit(1)

    text = sys.argv[1]

    try:
        import pyautogui
        pyautogui.write(text, interval=0.05)
        print(f"Typed: {text}")
    except ImportError:
        print("Error: pyautogui is required but not installed.", file=sys.stderr)
        print("Install with: pip install pyautogui", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error typing text: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()