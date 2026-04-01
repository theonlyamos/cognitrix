#!/usr/bin/env python3
"""Scrape text content from one or more web pages.

Usage:
    python scrape.py <url> [url2] [url3] ...

Arguments:
    url             One or more URLs to scrape (space-separated)

Requires: requests, beautifulsoup4 (pip install requests beautifulsoup4)
"""

import sys


def scrape_url(url: str) -> str:
    """Scrape text content from a URL."""
    import requests
    from bs4 import BeautifulSoup

    try:
        response = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.decompose()

        text = soup.get_text()
        # Clean up whitespace
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = " ".join(chunk for chunk in chunks if chunk)

        return f"URL: {url}\n\nContent:\n{text[:5000]}"

    except requests.exceptions.RequestException as e:
        return f"URL: {url}\n\nError: {e}"


def main():
    if len(sys.argv) < 2:
        print("Usage: python scrape.py <url> [url2] [url3] ...", file=sys.stderr)
        sys.exit(1)

    urls = sys.argv[1:]

    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        print("Error: requests and beautifulsoup4 are required.", file=sys.stderr)
        print("Install with: pip install requests beautifulsoup4", file=sys.stderr)
        sys.exit(1)

    results = []
    for url in urls:
        result = scrape_url(url)
        results.append(result)
        results.append("\n" + "=" * 50 + "\n")

    print("".join(results))


if __name__ == "__main__":
    main()