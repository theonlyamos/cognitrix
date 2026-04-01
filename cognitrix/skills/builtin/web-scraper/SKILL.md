---
name: web-scraper
description: Extract text content from web pages by scraping HTML
context: fork
args:
  - name: urls
    description: One or more URLs to scrape (space-separated)
    required: true
tags: [web, scrape, extract]
category: web
version: "1.0.0"
author: cognitrix
allowed-tools: [bash]
dependencies:
  pip: [requests, beautifulsoup4]
safety:
  risk-level: low
---

# Web Scraper

Extract text content from one or more web pages.

## Input Format

One or more URLs (space-separated):
- `https://example.com`
- `https://example.com https://example.org`

## Steps

1. Run the scrape script with the urls:
   ```bash
   python ${COGNITRIX_SKILL_DIR}/scripts/scrape.py "$(arg urls)"
   ```
2. Return the extracted text content from each URL

## Notes

- Requires requests and beautifulsoup4: `pip install requests beautifulsoup4`
- Strips HTML tags and returns plain text
- Limits output to 5000 characters per page
- Handles errors gracefully for each URL individually