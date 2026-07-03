---
name: web-scraper
description: Fetch and extract text content from web pages
context: same
args:
  - name: url
    description: URL of the web page to fetch
    required: true
  - name: max_length
    description: Maximum characters to return
    required: false
  - name: include_images
    description: Include image URLs in the output
    required: false
tags: [web, fetch, scrape, extract]
category: web
version: "1.0.0"
author: cognitrix
allowed-tools: [WebFetch]
safety:
  risk-level: low
---

# Web Scraper

Fetch and extract text content from web pages using the WebFetch tool.

## Usage

### Fetch a web page
```
WebFetch url="https://example.com"
```

### Fetch with more content
```
WebFetch url="https://example.com" max_length=10000
```

### Include image URLs
```
WebFetch url="https://example.com" include_images=true
```

## Notes

- Strips HTML tags and returns plain text
- Limits output to 5000 characters by default
- Handles errors gracefully
- Returns error message if URL is invalid or unreachable
