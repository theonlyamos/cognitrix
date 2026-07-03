---
name: internet-search
description: Search the web for up-to-date information using Tavily API
context: same
args:
  - name: query
    description: The search query
    required: true
  - name: max_results
    description: Maximum number of results
    required: false
tags: [search, web, tavily]
category: web
version: "1.0.0"
author: cognitrix
allowed-tools: [Search]
safety:
  risk-level: low
---

# Internet Search

Search the web for current information using the Search tool.

## Usage

### Basic search
```
Search query="Python async await best practices"
```

### More results
```
Search query="AI news 2026" max_results=20
```

## Notes

- Requires TAVily API key (free tier available)
- Set API key via: `export TAVILY_API_KEY=your-api-key`
- Or set `tavily_api_key` in configuration
- Returns search results with title, content snippet, and URL
