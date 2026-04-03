---
name: web-research
description: >
  Research a topic on the web. Use when the user asks to research, find information,
  or needs up-to-date data about a topic.
context: same
args:
  - name: topic
    description: The topic to research
    required: true
tags: [research, web, search]
category: research
version: "1.0.0"
author: theonlyamos
allowed-tools: [Search, WebFetch]
safety:
  risk-level: low
---

# Web Research

Research "$(arg topic)" thoroughly:

1. **Search the web** for the topic using the `Search` tool
2. **Fetch the top results** using the `WebFetch` tool
3. **Synthesize** the information into a well-structured markdown summary
4. **Include citations** with [n] notation
5. **List all source URLs** at the end

## Requirements

- Be thorough but concise
- Use headers and bullet points for readability
- Cite specific claims with numbered references
- Focus on recent, authoritative sources
- If information conflicts between sources, note the discrepancy

## Output Format

```
# Research: [Topic]

## Executive Summary
Brief 2-3 sentence overview.

## Key Findings
### [Subtopic 1]
...

### [Subtopic 2]
...

## Analysis
...

## Sources
1. [Title](url)
2. [Title](url)
```

## Notes

- Use `Search` with query parameter to search the web
- Use `WebFetch` with url parameter to fetch individual pages
- Requires TAVILY_API_KEY environment variable for search
