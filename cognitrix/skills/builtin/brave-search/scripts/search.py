#!/usr/bin/env python3
"""Search the web using Brave Search API.

Usage:
    python search.py <search_query>

Arguments:
    search_query    The query to search for

Requires: requests (pip install requests)
Environment: BRAVE_API_KEY (set via environment variable)
"""

import os
import sys


def main():
    if len(sys.argv) < 2:
        print("Usage: python search.py <search_query>", file=sys.stderr)
        sys.exit(1)

    query = sys.argv[1]
    api_key = os.environ.get("BRAVE_API_KEY")

    if not api_key:
        print("Error: BRAVE_API_KEY environment variable not set.", file=sys.stderr)
        print("Set it with: export BRAVE_API_KEY=your-api-key", file=sys.stderr)
        sys.exit(1)

    try:
        import requests
    except ImportError:
        print("Error: requests is required but not installed.", file=sys.stderr)
        print("Install with: pip install requests", file=sys.stderr)
        sys.exit(1)

    url = "https://api.search.brave.com/res/v1/web/search"
    params = {"q": query, "summary": 1}
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": api_key,
    }

    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()

        data = response.json()
        results = data.get("web", {}).get("results", [])[:10]

        for r in results:
            print(f"Title: {r.get('title', 'N/A')}")
            print(f"Description: {r.get('description', 'N/A')}")
            print(f"URL: {r.get('url', 'N/A')}")
            print("---")

    except requests.exceptions.HTTPError as e:
        if response.status_code == 401:
            print("Error: Invalid API key", file=sys.stderr)
        else:
            print(f"HTTP Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()