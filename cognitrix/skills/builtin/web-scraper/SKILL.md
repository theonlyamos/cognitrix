---
name: web-scraper
description: Extract text content from web pages by scraping HTML
context: fork
argument-hint: <url> [url2] [url3] ...
tags: [web, scrape, extract]
category: web
version: "1.0.0"
author: cognitrix
allowed-tools: [bash]
dependencies:
  pip: [requests, beautifulsoup4]
---

# Web Scraper

Extract text content from one or more web pages.

## Input Format

One or more URLs (space-separated):
- `https://example.com`
- `https://example.com https://example.org`

## Steps

1. Run the scrape script:
   ```bash
   python ${COGNITRIX_SKILL_DIR}/scripts/scrape.py "$ARGUMENTS"
   ```
2. Return the extracted text content from each URL

## Notes

- Requires requests and beautifulsoup4: `pip install requests beautifulsoup4`
- Strips HTML tags and returns plain text
- Limits output to 5000 characters per page
- Handles errors gracefully for each URL individually