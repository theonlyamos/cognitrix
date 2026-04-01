---
name: summarize-document
description: >
  Summarize a document or file. Use when asked to summarize, give an overview,
  or create a TL;DR of a file or text.
argument-hint: <file-path> [short|medium|detailed]
tags: [summary, document, tldr]
category: writing
version: "1.0.0"
author: theonlyamos
allowed-tools: [bash, read_file, python_repl]
safety:
  risk-level: low
---

# Summarize Document

Summarize the document at `$0`:

1. **Read** the entire file
2. **Identify** the main topics, arguments, and conclusions
3. **Produce** a summary at the requested depth level

## Depth Levels

Use depth "$1" (default to "medium" if not specified):

- **short**: 2-3 sentence TL;DR. Just the absolute essentials.
- **medium**: 1-2 paragraph summary with key points as bullet list.
- **detailed**: Section-by-section breakdown covering all important details.

## Output Format

### For "short":
> **TL;DR:** [2-3 sentences]

### For "medium":
**Summary:** [1-2 paragraphs]

**Key Points:**
- Point 1
- Point 2
- ...

### For "detailed":
## [Section Title]
[Section summary with key details]

## [Next Section]
...

## Conclusions
[Main takeaways]
