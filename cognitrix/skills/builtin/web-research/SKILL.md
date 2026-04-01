---
name: web-research
description: >
  Research a topic on the web. Use when the user asks to research, find information,
  or needs up-to-date data about a topic.
context: fork
argument-hint: <topic>
tags: [research, web, search]
category: research
version: "1.0.0"
author: theonlyamos
---

# Web Research

Research "$ARGUMENTS" thoroughly:

1. **Search the web** for the topic using the Internet Search tool
2. **Scrape the top 5** most relevant results using the Web Scraper tool
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
