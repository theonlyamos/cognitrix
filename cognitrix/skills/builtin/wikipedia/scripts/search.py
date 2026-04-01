#!/usr/bin/env python3
"""Search Wikipedia and return information about a topic.

Usage:
    python search.py <search_query> [basic|advanced]

Arguments:
    search_query    The term to search for
    depth          Optional: "basic" (default) for summary, "advanced" for full content

Requires: wikipedia (pip install wikipedia)
"""

import sys
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("Usage: python search.py <search_query> [basic|advanced]", file=sys.stderr)
        sys.exit(1)

    query = sys.argv[1]
    depth = sys.argv[2] if len(sys.argv) > 2 else "basic"

    try:
        import wikipedia as wk
    except ImportError:
        print("Error: wikipedia library is required but not installed.", file=sys.stderr)
        print("Install with: pip install wikipedia", file=sys.stderr)
        sys.exit(1)

    try:
        if depth == "advanced":
            page = wk.page(query)
            content = page.content[:5000]  # Limit output
            print(content)
        else:
            print(wk.summary(query))
    except wk.exceptions.DisambiguationError as e:
        print(f"Multiple results found: {e.options[:10]}")
    except wk.exceptions.PageError:
        print("Error: Page not found")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()