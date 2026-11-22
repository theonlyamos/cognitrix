"""Thumbnails and the details a preview needs, made from a local copy.

Everything here is best effort: a file that can't be read just gets no
thumbnail. Runs in a worker thread (``asyncio.to_thread``).
"""
from __future__ import annotations

import csv
import io
import json
import logging
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Any

logger = logging.getLogger('cognitrix.log')

THUMB_WIDTH = 640
CARD_ROWS = 6
CARD_COLS = 6
VIEW_ROWS = 500
VIEW_COLS = 40
MAX_SHEETS = 12
MAX_ENTRIES = 500
MAX_TEXT = 200_000
PEAKS = 48


def _jpeg(img) -> bytes:
    img = img.convert('RGB') if img.mode not in ('RGB', 'L') else img
    if img.width > THUMB_WIDTH:
        img = img.resize((THUMB_WIDTH, max(1, round(img.height * THUMB_WIDTH / img.width))))
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=78)
    return buf.getvalue()


def _ffprobe(path: Path) -> dict[str, Any]:
    exe = shutil.which('ffprobe')
    if not exe:
        return {}
    try:
        got = subprocess.run([exe, '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams',  # noqa: S603
                              str(path)], capture_output=True, timeout=30)
        data = json.loads(got.stdout or b'{}')
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    out: dict[str, Any] = {}
    try:
        out['duration'] = round(float((data.get('format') or {}).get('duration') or 0), 2) or None
    except (TypeError, ValueError):
        pass
    for stream in data.get('streams') or []:
        if stream.get('codec_type') == 'video' and stream.get('width'):
            out.setdefault('width', int(stream['width']))
            out.setdefault('height', int(stream['height']))
    return {k: v for k, v in out.items() if v}


def _video_poster(path: Path, duration: float | None) -> bytes | None:
    exe = shutil.which('ffmpeg')
    if not exe:
        return None
    at = '1' if not duration or duration > 2 else '0'
    try:
        got = subprocess.run([exe, '-v', 'error', '-ss', at, '-i', str(path), '-frames:v', '1',  # noqa: S603
                              '-vf', f'scale=min({THUMB_WIDTH}\\,iw):-2', '-f', 'image2', '-c:v', 'mjpeg', '-'],
                             capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return got.stdout or None


def _audio_peaks(path: Path) -> list[float]:
    """A rough waveform: PEAKS bar heights from 0 to 1."""
    exe = shutil.which('ffmpeg')
    if not exe:
        return []
    try:
        got = subprocess.run([exe, '-v', 'error', '-t', '1200', '-i', str(path), '-ac', '1', '-ar', '2000',  # noqa: S603
                              '-f', 's16le', '-'], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    raw = got.stdout or b''
    count = len(raw) // 2
    if count < PEAKS:
        return []
    import struct
    samples = struct.unpack(f'<{count}h', raw[:count * 2])
    step = count // PEAKS
    peaks = [max(abs(s) for s in samples[i * step:(i + 1) * step]) for i in range(PEAKS)]
    top = max(peaks) or 1
    return [round(p / top, 2) for p in peaks]


def _pdf(path: Path) -> tuple[dict[str, Any], bytes | None]:
    try:
        import fitz  # pymupdf
    except ImportError:
        try:
            from pypdf import PdfReader
            return {'pages': len(PdfReader(str(path)).pages)}, None
        except Exception:
            return {}, None
    try:
        with fitz.open(str(path)) as doc:
            info: dict[str, Any] = {'pages': doc.page_count}
            if not doc.page_count:
                return info, None
            page = doc.load_page(0)
            zoom = THUMB_WIDTH / max(1.0, page.rect.width)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            return info, pix.tobytes('jpeg')
    except Exception:
        logger.debug('Could not read a PDF for its preview', exc_info=True)
        return {}, None


def _cell(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)[:200]


def read_sheets(path: Path, rows: int = VIEW_ROWS, cols: int = VIEW_COLS) -> list[dict[str, Any]]:
    """[{name, rows: [[...]], total_rows}] for csv, tsv and xlsx."""
    suffix = path.suffix.lower()
    if suffix in ('.csv', '.tsv'):
        with open(path, encoding='utf-8', errors='replace', newline='') as fh:
            sample = fh.read(4096)
            fh.seek(0)
            delimiter = '\t' if suffix == '.tsv' else ','
            if suffix == '.csv':
                try:
                    delimiter = csv.Sniffer().sniff(sample, delimiters=',;\t|').delimiter
                except csv.Error:
                    pass
            out, total = [], 0
            for row in csv.reader(fh, delimiter=delimiter):
                total += 1
                if len(out) < rows:
                    out.append([_cell(c) for c in row[:cols]])
            return [{'name': path.stem, 'rows': out, 'total_rows': total}]
    if suffix in ('.xlsx', '.xlsm'):
        return _read_xlsx(path, rows, cols)
    return []


# Excel files are zips of XML; reading the few cells a preview needs doesn't
# need a spreadsheet library. Parts are streamed, so a huge sheet stops early.
_NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
_REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
MAX_SHARED_STRINGS = 200_000
MAX_COUNTED_ROWS = 1_000_000


def _col_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def _xml_text(el) -> str:
    return ''.join(t.text or '' for t in el.iter(f'{_NS}t'))


def _read_xlsx(path: Path, rows: int, cols: int) -> list[dict[str, Any]]:
    from xml.etree import ElementTree as ET

    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared: list[str] = []
        if 'xl/sharedStrings.xml' in names:
            with z.open('xl/sharedStrings.xml') as fh:
                for _, el in ET.iterparse(fh):
                    if el.tag == f'{_NS}si':
                        shared.append(_xml_text(el)[:200])
                        el.clear()
                        if len(shared) >= MAX_SHARED_STRINGS:
                            break
        targets: dict[str, str] = {}
        if 'xl/_rels/workbook.xml.rels' in names:
            for rel in ET.fromstring(z.read('xl/_rels/workbook.xml.rels')):
                target = rel.get('Target') or ''
                target = target.lstrip('/') if target.startswith('/') else f'xl/{target}'
                targets[rel.get('Id') or ''] = target
        book = ET.fromstring(z.read('xl/workbook.xml'))
        sheets = []
        for i, sheet in enumerate(book.iter(f'{_NS}sheet')):
            if i >= MAX_SHEETS:
                break
            part = targets.get(sheet.get(_REL) or '') or f'xl/worksheets/sheet{i + 1}.xml'
            if part not in names:
                continue
            out: list[list[str]] = []
            total = 0
            with z.open(part) as fh:
                for _, el in ET.iterparse(fh):
                    if el.tag == f'{_NS}dimension':
                        ref = (el.get('ref') or '').split(':')[-1]
                        digits = ''.join(ch for ch in ref if ch.isdigit())
                        total = max(total, int(digits) if digits else 0)
                    elif el.tag == f'{_NS}row':
                        number = int(el.get('r') or len(out) + 1)
                        total = max(total, number)
                        if number <= rows:
                            line = [''] * cols
                            for c in el.iter(f'{_NS}c'):
                                at = _col_index(c.get('r') or '') if c.get('r') else None
                                if at is None or not 0 <= at < cols:
                                    continue
                                kind = c.get('t') or 'n'
                                v = c.find(f'{_NS}v')
                                raw = v.text if v is not None else None
                                if kind == 's' and raw is not None and raw.isdigit():
                                    value: Any = shared[int(raw)] if int(raw) < len(shared) else ''
                                elif kind == 'inlineStr':
                                    value = _xml_text(c)
                                elif kind == 'b':
                                    value = 'TRUE' if raw == '1' else 'FALSE'
                                elif kind == 'n' and raw is not None:
                                    try:
                                        value = float(raw)
                                    except ValueError:
                                        value = raw
                                else:
                                    value = raw
                                line[at] = _cell(value)
                            while len(out) < number - 1:
                                out.append([''] * cols)
                            out.append(line)
                        el.clear()
                        if number >= MAX_COUNTED_ROWS or (number > rows and total > number):
                            break
            width = max((max((j + 1 for j, v in enumerate(r) if v), default=0) for r in out), default=0)
            out = [r[:width] for r in out]
            while out and not any(out[-1]):
                out.pop()
            sheets.append({'name': sheet.get('name') or f'Sheet {i + 1}', 'rows': out,
                           'total_rows': total or len(out)})
        return sheets


def list_archive(path: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    total = 0
    count = 0
    lower = path.name.lower()
    if lower.endswith('.zip'):
        with zipfile.ZipFile(path) as z:
            for item in z.infolist():
                count += 1
                total += item.file_size
                if len(entries) < MAX_ENTRIES:
                    entries.append({'name': item.filename, 'size': item.file_size, 'dir': item.is_dir()})
    elif lower.endswith(('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tar.xz')):
        with tarfile.open(path) as t:
            for item in t:
                count += 1
                total += item.size
                if len(entries) < MAX_ENTRIES:
                    entries.append({'name': item.name, 'size': item.size, 'dir': item.isdir()})
    else:
        return {}
    return {'entries': entries, 'count': count, 'unpacked_size': total}


def _doc_excerpt(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    try:
        if suffix == '.docx':
            import docx  # python-docx, when installed
            doc = docx.Document(str(path))
            text = '\n'.join(p.text for p in doc.paragraphs if p.text.strip())
            return {'excerpt': text[:1200]} if text else {}
        if suffix == '.pptx':
            from pptx import Presentation  # python-pptx, when installed
            deck = Presentation(str(path))
            titles = []
            for slide in deck.slides:
                title = getattr(slide.shapes, 'title', None)
                titles.append((title.text if title is not None and title.has_text_frame else '').strip())
            return {'slides': len(titles), 'titles': [t for t in titles if t][:20]}
    except Exception:
        logger.debug('Could not read a document for its preview', exc_info=True)
    return {}


def read_text(path: Path, limit: int = MAX_TEXT) -> str:
    with open(path, 'rb') as fh:
        data = fh.read(limit + 1)
    text = data[:limit].decode('utf-8', 'replace')
    return text + ('\n…' if len(data) > limit else '')


def make(path: Path, kind: str) -> tuple[dict[str, Any], bytes | None]:
    """(details for the card, thumbnail JPEG or None)."""
    info: dict[str, Any] = {}
    thumb: bytes | None = None
    try:
        if kind == 'image':
            from PIL import Image
            if path.suffix.lower() == '.svg':
                return info, None
            with Image.open(path) as img:
                info.update(width=img.width, height=img.height)
                img.seek(0)
                thumb = _jpeg(img.copy())
        elif kind == 'video':
            info.update(_ffprobe(path))
            thumb = _video_poster(path, info.get('duration'))
        elif kind == 'audio':
            info.update(_ffprobe(path))
            peaks = _audio_peaks(path)
            if peaks:
                info['peaks'] = peaks
        elif kind == 'pdf':
            more, thumb = _pdf(path)
            info.update(more)
        elif kind == 'sheet':
            sheets = read_sheets(path, rows=CARD_ROWS, cols=CARD_COLS)
            if sheets:
                info['preview'] = sheets[0]['rows']
                info['sheets'] = len(sheets)
                info['rows'] = sheets[0]['total_rows']
        elif kind in ('doc', 'slides'):
            info.update(_doc_excerpt(path))
        elif kind == 'archive':
            listing = list_archive(path)
            if listing:
                info.update(count=listing['count'], unpacked_size=listing['unpacked_size'],
                            first=[e['name'] for e in listing['entries'] if not e['dir']][:5])
        elif kind in ('text', 'data', 'html'):
            text = read_text(path, 600)
            info['excerpt'] = text
    except Exception:
        logger.debug('Could not make a preview for %s', path.name, exc_info=True)
    return info, thumb
