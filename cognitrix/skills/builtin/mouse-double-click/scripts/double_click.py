#!/usr/bin/env python3
"""Perform a double mouse click at specified coordinates using pyautogui.

Usage:
    python double_click.py <x> <y>

Arguments:
    x    X coordinate (pixels from left edge)
    y    Y coordinate (pixels from top edge)

Requires: pyautogui (pip install pyautogui)
"""

import sys


def main():
    if len(sys.argv) < 3:
        print("Usage: python double_click.py <x> <y>", file=sys.stderr)
        sys.exit(1)

    try:
        x = int(sys.argv[1])
        y = int(sys.argv[2])
    except ValueError:
        print("Error: x and y must be integers", file=sys.stderr)
        sys.exit(1)

    try:
        import pyautogui
        pyautogui.doubleClick(x, y)
        print(f"Double-clicked at ({x}, {y})")
    except ImportError:
        print("Error: pyautogui is required but not installed.", file=sys.stderr)
        print("Install with: pip install pyautogui", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error double-clicking at ({x}, {y}): {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()