#!/usr/bin/env python3
"""Search YouTube for a topic and return the first video URL.

Usage:
    python search.py <search_topic>

Arguments:
    search_topic    The topic to search for on YouTube

Requires: requests (pip install requests)
"""

import sys
from pathlib import Path


def search_youtube(topic: str) -> str:
    """Search YouTube and return first video URL."""
    import requests

    url = f"https://www.youtube.com/results?q={topic.replace(' ', '+')}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()

    data = str(response.content)
    parts = data.split('"')

    for i, part in enumerate(parts):
        if part == "WEB_PAGE_TYPE_WATCH":
            # Extract video ID
            video_path = parts[i + 2]
            if "watch?v=" in video_path:
                video_id = video_path.split("watch?v=")[-1].split('"')[0].split("&")[0]
                return f"https://www.youtube.com/watch?v={video_id}"

    raise ValueError("No video found for this topic")


def main():
    if len(sys.argv) < 2:
        print("Usage: python search.py <search_topic>", file=sys.stderr)
        sys.exit(1)

    topic = sys.argv[1]

    try:
        import requests
    except ImportError:
        print("Error: requests is required but not installed.", file=sys.stderr)
        print("Install with: pip install requests", file=sys.stderr)
        sys.exit(1)

    try:
        video_url = search_youtube(topic)
        print(video_url)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()