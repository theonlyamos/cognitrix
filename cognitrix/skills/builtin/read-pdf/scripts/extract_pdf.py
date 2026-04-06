#!/usr/bin/env python3
"""Extract text content from a PDF file.

Usage:
    python extract_pdf.py <pdf_path> [page_range]

Arguments:
    pdf_path     Path to the PDF file
    page_range   Optional: "5", "1-10", "1,3,5", or omit for all pages

Requires: pymupdf (pip install pymupdf)
"""

import json
import os
import sys
from pathlib import Path


def parse_page_range(range_str: str, total_pages: int) -> list[int]:
    """Parse a page range string into a list of 0-indexed page numbers."""
    pages = []

    if '-' in range_str:
        # Range: "1-10"
        parts = range_str.split('-', 1)
        start = max(0, int(parts[0]) - 1)
        end = min(total_pages, int(parts[1]))
        pages = list(range(start, end))
    elif ',' in range_str:
        # Specific pages: "1,3,5"
        for p in range_str.split(','):
            p = p.strip()
            if p.isdigit():
                idx = int(p) - 1
                if 0 <= idx < total_pages:
                    pages.append(idx)
    elif range_str.isdigit():
        # Single page: "5"
        idx = int(range_str) - 1
        if 0 <= idx < total_pages:
            pages = [idx]
    else:
        pages = list(range(total_pages))

    return pages


def extract_with_pymupdf(pdf_path: str, pages: list[int] | None = None) -> dict:
    """Extract text using PyMuPDF (fitz)."""
    import fitz  # pymupdf

    doc = fitz.open(pdf_path)
    total_pages = len(doc)

    if pages is None:
        pages = list(range(total_pages))

    result = {
        'file': Path(pdf_path).name,
        'total_pages': total_pages,
        'extracted_pages': len(pages),
        'pages': [],
    }

    for page_num in pages:
        if page_num >= total_pages:
            continue
        page = doc[page_num]
        text = page.get_text('text')

        result['pages'].append({
            'number': page_num + 1,
            'text': text.strip(),
            'char_count': len(text.strip()),
        })

    doc.close()
    return result


def format_output(result: dict) -> str:
    """Format extracted content for display."""
    lines = []
    lines.append(f"## Document: {result['file']}")
    lines.append(f"**Pages:** {result['total_pages']} total, "
                 f"{result['extracted_pages']} extracted\n")

    for page in result['pages']:
        lines.append(f"### Page {page['number']}")
        if page['text']:
            text = page['text'].encode('ascii', 'ignore').decode('ascii')
            lines.append(text)
        else:
            lines.append("*(no text content — possibly a scanned/image page)*")
        lines.append("")

    return '\n'.join(lines)


def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_pdf.py <pdf_path> [page_range]")
        sys.exit(1)

    pdf_path = os.path.normpath(sys.argv[1])
    page_range = sys.argv[2] if len(sys.argv) > 2 else None

    # Validate file
    path = Path(pdf_path)
    if not path.exists():
        print(f"Error: File not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    if path.suffix.lower() != '.pdf':
        print(f"Warning: File may not be a PDF: {path.suffix}", file=sys.stderr)

    try:
        import fitz  # noqa: F401 — check if pymupdf is available
    except ImportError:
        print(
            "Error: pymupdf is required but not installed.\n"
            "Install with: pip install pymupdf",
            file=sys.stderr,
        )
        sys.exit(1)

    # Extract
    try:
        # Parse page range if given
        if page_range:
            import fitz
            doc = fitz.open(pdf_path)
            total = len(doc)
            doc.close()
            pages = parse_page_range(page_range, total)
        else:
            pages = None

        result = extract_with_pymupdf(pdf_path, pages)
        print(format_output(result))

    except Exception as e:
        print(f"Error extracting PDF: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
