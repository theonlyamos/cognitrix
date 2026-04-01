---
name: read-pdf
description: >
  Extract and analyze content from PDF files. Use when the user asks to read,
  parse, summarize, or extract information from a PDF document.
args:
  - name: pdf_file_path
    description: Path to the PDF file to read
    required: true
  - name: page_range
    description: Optional page range (e.g., "5", "1-10", "1,3,5")
    required: false
tags: [pdf, document, extract, read]
category: document
version: "1.0.0"
author: theonlyamos
allowed-tools: [bash, read_file]
dependencies:
  pip: [pymupdf]
safety:
  risk-level: low
---

# Read PDF

Extract content from the PDF at "$(arg pdf_file_path)":

1. **Extract text** using the helper script:
   ```bash
   python ${COGNITRIX_SKILL_DIR}/scripts/extract_pdf.py "$(arg pdf_file_path)" "$(arg page_range)"
   ```
2. **Read the extracted output** and present it clearly
3. If the user asked a specific question about the PDF, **answer it** based on the extracted content
4. If no specific question, provide a **structured overview** of the document contents

## Page Range (optional)

The page_range argument is optional:
- `5` — extract only page 5
- `1-10` — extract pages 1 through 10
- `1,3,5` — extract specific pages
- (omitted) — extract all pages

## Output Format

### For full extraction:
```
## Document: [filename]
**Pages:** N total

### Page 1
[content]

### Page 2
[content]
...
```

### For targeted questions:
Answer the question directly, citing specific pages where the information was found.

## Notes
- If the script fails due to a missing dependency, install it:
  `pip install pymupdf`
- For scanned/image PDFs, note that text extraction may be limited
