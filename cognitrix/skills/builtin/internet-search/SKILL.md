---
name: internet-search
description: Search the web using Tavily API for up-to-date information
context: fork
argument-hint: <search_query> [basic|advanced]
tags: [search, web, tavily]
category: web
version: "1.0.0"
author: cognitrix
allowed-tools: [bash]
dependencies:
  pip: [tavily]
safety:
  risk-level: low
---

# Internet Search

Search the web using Tavily API for current information.

## Input Format

Arguments: `<search_query> [basic|advanced]`

- `basic` (default): Returns concise results
- `advanced`: Returns more comprehensive results

## Steps

1. Ensure TAVILY_API_KEY environment variable is set
2. Run the search script:
   ```bash
   python ${COGNITRIX_SKILL_DIR}/scripts/search.py "$ARGUMENTS"
   ```
3. Format and return search results

## Notes

- Requires Tavily API key (free tier available)
- Set API key via: `export TAVILY_API_KEY=your-api-key`
- Returns search results with title, content snippet, and URL