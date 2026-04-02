---
name: summarize-document
description: >
  Summarize a document or file. Use when asked to summarize, give an overview,
  or create a TL;DR of a file or text.
args:
  - name: file_path
    description: Path to the file to summarize
    required: true
  - name: depth
    description: Summary depth - short (TL;DR), medium (key points), detailed (section-by-section)
    required: false
    default: medium
tags: [summary, document, tldr]
category: writing
version: "1.0.0"
author: theonlyamos
allowed-tools: [bash, read-file]
safety:
  risk-level: low
---

# Summarize Document

Summarize the document at "$(arg file_path)":

1. **Read** the entire file
2. **Identify** the main topics, arguments, and conclusions
3. **Produce** a summary at the requested depth level

## Depth Levels

Use depth "$(arg depth)" (defaults to "medium" if not specified):

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
