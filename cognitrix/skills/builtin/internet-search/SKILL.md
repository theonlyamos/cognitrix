---
name: internet-search
description: Search the web using Tavily API for up-to-date information
context: fork
args:
  - name: search_query
    description: The search query to look up
    required: true
  - name: mode
    description: Search mode - basic for concise results, advanced for comprehensive results
    required: false
    default: basic
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
2. Run the search script with the search_query:
   ```bash
   python ${COGNITRIX_SKILL_DIR}/scripts/search.py "$(arg search_query)" "$(arg mode)"
   ```
3. Format and return search results

## Notes

- Requires Tavily API key (free tier available)
- Set API key via: `export TAVILY_API_KEY=your-api-key`
- Returns search results with title, content snippet, and URL