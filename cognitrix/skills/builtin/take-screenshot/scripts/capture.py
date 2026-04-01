#!/usr/bin/env python3
"""Take a screenshot of the entire screen or a specific region.

Usage:
    python capture.py [output_path]

Arguments:
    output_path    Optional: Path to save the screenshot (default: screenshot.png)

Requires: pyautogui (pip install pyautogui)
"""

import sys
from pathlib import Path


def main():
    try:
        import pyautogui
    except ImportError:
        print("Error: pyautogui is required but not installed.", file=sys.stderr)
        print("Install with: pip install pyautogui", file=sys.stderr)
        sys.exit(1)

    # Determine output path
    if len(sys.argv) > 1:
        output_path = Path(sys.argv[1])
    else:
        output_path = Path.cwd() / "screenshot.png"

    try:
        screenshot = pyautogui.screenshot()
        screenshot.save(str(output_path))
        print(f"Screenshot saved to: {output_path.absolute()}")
    except Exception as e:
        print(f"Error taking screenshot: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()