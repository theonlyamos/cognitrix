"""Turn an uploaded file into plain text, and text into overlapping passages."""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import PurePath
from xml.etree import ElementTree

TEXT_TYPES = {'.txt', '.md', '.markdown', '.csv', '.tsv', '.json', '.log', '.rst', '.yaml', '.yml'}
SUPPORTED = TEXT_TYPES | {'.pdf', '.docx', '.html', '.htm'}
MAX_CHARACTERS = 2_000_000
PASSAGE_CHARS = 1000
PASSAGE_OVERLAP = 150


class ExtractError(ValueError):
    """A file that can't be read into text; the message is safe to show."""


def _decode(data: bytes) -> str:
    for encoding in ('utf-8-sig', 'utf-16', 'cp1252'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', errors='replace')


def _pdf(data: bytes) -> str:
    try:
        import fitz  # pymupdf
    except ImportError as exc:
        raise ExtractError('PDF support is not installed on this server') from exc
    try:
        with fitz.open(stream=data, filetype='pdf') as doc:
            if doc.needs_pass:
                raise ExtractError('This PDF is password protected')
            pages = [f'[Page {number}]\n{page.get_text().strip()}' for number, page in enumerate(doc, 1)]
    except ExtractError:
        raise
    except Exception as exc:
        raise ExtractError('This PDF could not be read') from exc
    return '\n\n'.join(page for page in pages if page.split('\n', 1)[-1].strip())


_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def _docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo('word/document.xml')
            if info.file_size > 50 * 1024 * 1024:
                raise ExtractError('This Word file is too large')
            root = ElementTree.fromstring(archive.read(info))
    except ExtractError:
        raise
    except Exception as exc:
        raise ExtractError('This Word file could not be read') from exc
    paragraphs = []
    for paragraph in root.iter(f'{_W}p'):
        parts = []
        for node in paragraph.iter():
            if node.tag == f'{_W}t' and node.text:
                parts.append(node.text)
            elif node.tag in (f'{_W}tab',):
                parts.append('\t')
            elif node.tag in (f'{_W}br', f'{_W}cr'):
                parts.append('\n')
        text = ''.join(parts).strip()
        if text:
            paragraphs.append(text)
    return '\n\n'.join(paragraphs)


def _html(data: bytes) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(_decode(data), 'html.parser')
    for tag in soup(['script', 'style', 'noscript', 'template']):
        tag.decompose()
    return re.sub(r'\n{3,}', '\n\n', soup.get_text('\n')).strip()


def to_text(name: str, data: bytes) -> str:
    suffix = PurePath(name).suffix.lower()
    if suffix not in SUPPORTED:
        raise ExtractError(f'{suffix or "This kind of file"} is not supported. Use PDF, Word, text, Markdown, CSV or HTML.')
    if suffix == '.pdf':
        text = _pdf(data)
    elif suffix == '.docx':
        text = _docx(data)
    elif suffix in ('.html', '.htm'):
        text = _html(data)
    else:
        text = _decode(data)
    text = text.replace('\x00', '').strip()
    if not text:
        raise ExtractError('No text was found in this file (a scanned PDF needs text recognition first)')
    return text[:MAX_CHARACTERS]


def passages(text: str, size: int = PASSAGE_CHARS, overlap: int = PASSAGE_OVERLAP) -> list[str]:
    """Split on paragraph and sentence breaks into pieces of about ``size``
    characters, each starting with the tail of the one before."""
    pieces = [piece.strip() for piece in re.split(r'\n\s*\n', text) if piece.strip()]
    units: list[str] = []
    for piece in pieces:
        if len(piece) <= size:
            units.append(piece)
            continue
        sentences = re.split(r'(?<=[.!?])\s+|\n', piece)
        for sentence in sentences:
            sentence = sentence.strip()
            while len(sentence) > size:
                units.append(sentence[:size])
                sentence = sentence[size:]
            if sentence:
                units.append(sentence)
    out: list[str] = []
    current = ''
    for unit in units:
        if current and len(current) + len(unit) + 2 > size:
            out.append(current)
            tail = current[-overlap:] if overlap else ''
            # Start the overlap at a word boundary.
            tail = tail[tail.find(' ') + 1:] if ' ' in tail else tail
            current = f'{tail}\n{unit}' if tail else unit
        else:
            current = f'{current}\n\n{unit}' if current else unit
    if current:
        out.append(current)
    return out
