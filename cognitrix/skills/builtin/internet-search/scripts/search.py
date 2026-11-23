#!/usr/bin/env python3
"""Search the web using Tavily API.

Usage:
    python search.py <search_query> [basic|advanced]

Arguments:
    search_query    The query to search for
    depth          Optional: "basic" (default) or "advanced"

Requires: tavily (pip install tavily)
Key: the Tavily key in Cognitrix Settings, Models and keys
"""

import sys


def main():
    if len(sys.argv) < 2:
        print("Usage: python search.py <search_query> [basic|advanced]", file=sys.stderr)
        sys.exit(1)

    query = sys.argv[1]
    depth = sys.argv[2] if len(sys.argv) > 2 else "basic"
    try:
        from cognitrix.app_settings import get as app_setting
        api_key = app_setting("TAVILY_API_KEY")
    except ImportError:
        api_key = ""

    if not api_key:
        print("Error: no Tavily key. Add one in Settings, Models and keys.", file=sys.stderr)
        sys.exit(1)

    try:
        from tavily import TavilyClient
    except ImportError:
        print("Error: tavily is required but not installed.", file=sys.stderr)
        print("Install with: pip install tavily", file=sys.stderr)
        sys.exit(1)

    try:
        client = TavilyClient(api_key=api_key)
        results = client.search(query, depth)

        if results and "results" in results:
            for r in results["results"]:
                print(f"Title: {r.get('title', 'N/A')}")
                print(f"Content: {r.get('content', 'N/A')[:300]}...")
                print(f"URL: {r.get('url', 'N/A')}")
                print("---")
        else:
            print("No results found")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
