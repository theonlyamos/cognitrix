#!/usr/bin/env python3
"""Open a URL in the default web browser.

Usage:
    python open.py <url>

Arguments:
    url             The URL to open in the browser

Platform-specific:
    - Linux: xdg-open, firefox, google-chrome
    - macOS: open
    - Windows: start, cmd /c start
"""

import sys
import subprocess
import shutil
import platform


def get_open_command(url: str) -> list[str]:
    """Get the appropriate command to open a URL in browser."""
    system = platform.system()

    if system == "Windows":
        return ["cmd", "/c", "start", "", url]
    elif system == "Darwin":  # macOS
        return ["open", url]
    else:  # Linux
        # Try common commands in order
        for cmd in ["xdg-open", "firefox", "google-chrome", "chromium-browser"]:
            if shutil.which(cmd):
                return [cmd, url]
        # Fallback to xdg-open
        return ["xdg-open", url]


def main():
    if len(sys.argv) < 2:
        print("Usage: python open.py <url>", file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1]

    # Ensure URL has a scheme
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        cmd = get_open_command(url)
        subprocess.run(cmd, check=True)
        print(f"Opened: {url}")
    except subprocess.CalledProcessError as e:
        print(f"Error opening URL: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("Error: No browser command found", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()