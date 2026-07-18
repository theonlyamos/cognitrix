from __future__ import annotations

import contextvars
import fnmatch
import html
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import regex as regex_engine
from rich import print

from cognitrix.common.process_security import (
    HostProcessAccessError,
    HostProcessMode,
    require_host_process_authority,
)
from cognitrix.common.safe_exec import (
    DEFAULT_TIMEOUT,
    CommandNotAllowed,
    PathEscapesRoot,
    resolve_within_root,
    run_whitelisted,
)
from cognitrix.config import settings
from cognitrix.media import document_storage
from cognitrix.media.document_capabilities import storage_record
from cognitrix.media.types import MediaAccessError, MediaValidationError
from cognitrix.tools.tool import tool
from cognitrix.tools.utils import (
    DocumentCapability,
    current_execution_context,
    delegated_execution_context,
    ToolOutcome,
)

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')

MAX_TOOL_OUTPUT_CHARS = 32_000
MAX_FILE_LINE_CHARS = 4_000
MAX_READ_FILE_BYTES = 10 * 1024 * 1024
DEFAULT_PDF_PAGES = 5
MAX_PDF_PAGES_PER_READ = 10
MAX_PAGE_RANGE_CHARS = 256
MAX_SEARCH_PATTERN_CHARS = 1_000
MAX_GLOB_PATTERN_CHARS = 1_000
MAX_SEARCH_RESULTS = 100
MAX_SEARCH_FILES = 1_000
MAX_SEARCH_FILE_BYTES = 10 * 1024 * 1024
MAX_SEARCH_TOTAL_BYTES = 50 * 1024 * 1024
MAX_SEARCH_ENTRIES = 5_000
MAX_SEARCH_DIRECTORIES = 1_000
MAX_SEARCH_SECONDS = 5.0
MAX_REGEX_MATCH_SECONDS = 0.02


def _truncate_output_line(value: str) -> str:
    text = value.rstrip('\r\n')
    if len(text) <= MAX_FILE_LINE_CHARS:
        return text
    omitted = len(text) - MAX_FILE_LINE_CHARS
    return (
        text[:MAX_FILE_LINE_CHARS]
        + f'... [line truncated; {omitted} chars omitted]'
    )


def _hard_cap_output(value: str, note: str) -> str:
    if len(value) <= MAX_TOOL_OUTPUT_CHARS:
        return value
    marker = f'\n{note}'
    keep = max(0, MAX_TOOL_OUTPUT_CHARS - len(marker))
    return value[:keep] + marker[:MAX_TOOL_OUTPUT_CHARS - keep]


@dataclass
class _SearchBudget:
    deadline: float
    entries: int = 0
    directories: int = 0
    limit_reason: str | None = None

    def within_deadline(self) -> bool:
        if time.monotonic() < self.deadline:
            return True
        self.limit_reason = self.limit_reason or 'search deadline'
        return False

    def visit_entry(self) -> bool:
        if not self.within_deadline():
            return False
        if self.entries >= MAX_SEARCH_ENTRIES:
            self.limit_reason = (
                f'{MAX_SEARCH_ENTRIES}-entry traversal limit'
            )
            return False
        self.entries += 1
        return True

    def visit_directory(self) -> bool:
        if not self.within_deadline():
            return False
        if self.directories >= MAX_SEARCH_DIRECTORIES:
            self.limit_reason = (
                f'{MAX_SEARCH_DIRECTORIES}-directory traversal limit'
            )
            return False
        self.directories += 1
        return True


@dataclass(frozen=True)
class _SearchEntry:
    path: Path
    is_directory: bool


def _bounded_search_lines(
    stream,
    byte_budget: int,
    budget: _SearchBudget | None = None,
):
    """Yield bounded decoded lines and the bytes consumed for each line."""
    remaining = max(0, byte_budget)
    line_number = 0
    while remaining:
        if budget is not None and not budget.within_deadline():
            return
        read_limit = min(MAX_FILE_LINE_CHARS + 2, remaining)
        first = stream.readline(read_limit)
        if not first:
            break

        remaining -= len(first)
        line_number += 1
        line_bytes = len(first)
        complete = (
            first.endswith(b'\n')
            or len(first) < read_limit
            or remaining == 0
        )
        content = first.rstrip(b'\r\n')
        prefix = content[:MAX_FILE_LINE_CHARS]
        truncated = len(content) > MAX_FILE_LINE_CHARS or not complete

        while not complete and remaining:
            if budget is not None and not budget.within_deadline():
                return
            discard_limit = min(64 * 1024, remaining)
            discarded = stream.readline(discard_limit)
            if not discarded:
                complete = True
                break
            remaining -= len(discarded)
            line_bytes += len(discarded)
            complete = (
                discarded.endswith(b'\n')
                or len(discarded) < discard_limit
            )

        search_text = prefix.decode('utf-8', errors='replace')
        display_text = search_text
        if truncated:
            omitted = max(1, line_bytes - len(prefix))
            display_text += (
                f'... [line truncated; at least {omitted} bytes omitted]'
            )
        yield line_number, search_text, display_text, line_bytes


class ManagedUploadAccessError(ValueError):
    pass


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _lexical_path(value: str, root: Path) -> Path:
    candidate = Path(value).expanduser()
    if candidate.drive and not candidate.is_absolute():
        raise PathEscapesRoot(f"Path '{value}' is not absolute")
    joined = candidate if candidate.is_absolute() else root / candidate
    return Path(os.path.abspath(os.fspath(joined)))


def _upload_relative(path: Path, uploads: Path) -> Path | None:
    try:
        return path.relative_to(uploads)
    except ValueError:
        return None


def _managed_document_capability(value: str) -> DocumentCapability | None:
    """Resolve one exact server-minted grant without trusting a client path."""
    root = Path(settings.tools_root).expanduser().resolve()
    lexical = _lexical_path(value, root)
    uploads = root / 'uploads'
    relative = _upload_relative(lexical, uploads)
    if relative is None:
        return None
    requested = relative.as_posix()
    for capability in current_execution_context().document_capabilities:
        if requested == Path(capability.storage_key).as_posix().removeprefix(
            'uploads/'
        ):
            return capability
    raise ManagedUploadAccessError('Managed document is not granted to this turn')


def _resolve_tool_path(
    value: str,
    *,
    write: bool = False,
    allow_upload_root: bool = False,
) -> Path:
    root = Path(settings.tools_root).expanduser().resolve()
    lexical = _lexical_path(value, root)
    if not _inside(lexical, root):
        raise PathEscapesRoot(
            f"Path '{value}' resolves outside the permitted root '{root}'"
        )
    resolved = resolve_within_root(value, root)
    lexical_uploads = root / 'uploads'
    resolved_uploads = lexical_uploads.resolve()
    lexical_relative = _upload_relative(lexical, lexical_uploads)
    resolved_relative = _upload_relative(resolved, resolved_uploads)
    touches_uploads = lexical_relative is not None or resolved_relative is not None
    if not touches_uploads:
        return resolved
    if write:
        raise ManagedUploadAccessError('Managed uploads are read-only')
    # Managed documents are never traversed or opened by path. Read dispatches
    # an exact capability to identity-pinned storage before reaching here.
    raise ManagedUploadAccessError('Managed uploads are not path-readable')


def _collect_search_entries(
    root: Path,
    budget: _SearchBudget,
    *,
    recursive: bool,
    exclude_directory: str | None = None,
) -> list[_SearchEntry]:
    """Enumerate an authorized tree under explicit time/entry/dir budgets."""
    collected: list[_SearchEntry] = []
    pending = [root]
    while pending:
        if not budget.visit_directory():
            break
        current = pending.pop()
        entries = []
        entry_limit_hit = False
        try:
            with os.scandir(current) as iterator:
                for entry in iterator:
                    if not budget.visit_entry():
                        entry_limit_hit = True
                        break
                    entries.append(entry)
                entries.sort(key=lambda item: item.name.casefold())

                child_directories: list[Path] = []
                for entry in entries:
                    if not budget.within_deadline():
                        break
                    candidate = Path(entry.path)
                    try:
                        is_directory = entry.is_dir(follow_symlinks=False)
                        is_file = entry.is_file(follow_symlinks=True)
                    except OSError:
                        continue
                    if not is_directory and not is_file:
                        continue
                    try:
                        authorized = _resolve_tool_path(
                            os.fspath(candidate),
                            allow_upload_root=True,
                        )
                    except (ManagedUploadAccessError, PathEscapesRoot):
                        continue
                    if is_directory:
                        if (
                            exclude_directory
                            and fnmatch.fnmatch(entry.name, exclude_directory)
                        ):
                            continue
                        collected.append(_SearchEntry(authorized, True))
                        if recursive:
                            child_directories.append(authorized)
                    else:
                        collected.append(_SearchEntry(authorized, False))
                pending.extend(reversed(child_directories))
        except OSError:
            continue
        if entry_limit_hit or budget.limit_reason:
            break
    return collected


def _pyautogui():
    """Import pyautogui lazily. It (and its tkinter/cv2 deps) added ~0.15s+ to
    every startup and can crash on import in a headless server/worker, yet is
    only used by the screen-automation tools. Cached by sys.modules after first
    import."""
    import pyautogui
    return pyautogui

def get_file_content(full_path: Path):
    with full_path.open('rt') as file:
        lines = file.readlines()
        line_data = [(i + 1, line.rstrip()) for i, line in enumerate(lines)]
        return '\n'.join(f"{num}: {content}" for num, content in line_data)


@tool(category='system')
def open_file(path: str, filename: str | None = None):
    """Open a file or folder using the system's default application.

    Args:
        path (str|Path): The path to the file or folder. Use '~' or '~/' to reference your home directory.
        filename (str, optional): The name of the file to open

    Returns:
        str: Success or error message
    """
    try:
        try:
            context = current_execution_context()
            require_host_process_authority(context.host_process_mode)
            target = os.fspath(Path(path) / filename) if filename else path
            npath = _resolve_tool_path(target)
        except (HostProcessAccessError, ManagedUploadAccessError, PathEscapesRoot) as e:
            return f'Error: {e}'
        if filename and npath.is_file():
            os.startfile(npath)
            return 'Successfully opened file'
        elif npath.exists():
            os.startfile(npath)
            return 'Successfully opened file' if npath.is_file() else 'Successfully opened folder'
        return 'Unable to open file'
    except Exception as e:
        return str(e)


def _bounded_pdf_pages(page_range: str | None, total_pages: int) -> tuple[list[int], bool]:
    if not page_range:
        pages = list(range(min(total_pages, DEFAULT_PDF_PAGES)))
        return pages, total_pages > DEFAULT_PDF_PAGES
    if len(page_range) > MAX_PAGE_RANGE_CHARS:
        raise ValueError(f'page_range exceeds {MAX_PAGE_RANGE_CHARS} characters')

    limit = MAX_PDF_PAGES_PER_READ + 1
    if '-' in page_range:
        raw_start, raw_end = page_range.split('-', 1)
        start = max(0, int(raw_start) - 1)
        end = min(total_pages, int(raw_end), start + limit)
        requested = list(range(start, end))
    elif ',' in page_range:
        requested = []
        for raw_page in page_range.split(',')[:limit]:
            raw_page = raw_page.strip()
            if raw_page.isdigit():
                page = int(raw_page) - 1
                if 0 <= page < total_pages:
                    requested.append(page)
    elif page_range.isdigit():
        page = int(page_range) - 1
        requested = [page] if 0 <= page < total_pages else []
    else:
        raise ValueError('Invalid page_range; use forms such as 1-5, 1,3,5, or 3')

    return requested[:MAX_PDF_PAGES_PER_READ], len(requested) > MAX_PDF_PAGES_PER_READ


def _render_pdf(doc, display_name: str, page_range: str | None = None) -> str:
    try:
        total_pages = len(doc)
        pages, limited = _bounded_pdf_pages(page_range, total_pages)
        if not pages:
            return 'Error: page_range selected no pages'

        if page_range:
            summary = f'**Pages:** {total_pages} total, {len(pages)} extracted'
            if limited:
                summary += (
                    f'; selection limited to {MAX_PDF_PAGES_PER_READ} pages; '
                    'request another page_range'
                )
        else:
            summary = f'**Pages:** {total_pages} total; showing first {len(pages)} pages'

        lines = [f'## Document: {display_name}', summary, '']
        per_page_limit = max(
            1_000,
            (MAX_TOOL_OUTPUT_CHARS - 2_000) // max(1, len(pages)),
        )
        for page_number in pages:
            text = (
                doc[page_number]
                .get_text('text')
                .strip()
                .encode('ascii', 'ignore')
                .decode('ascii')
            )
            lines.append(f'### Page {page_number + 1}')
            if not text:
                lines.append('*(no text content; possibly a scanned/image page)*')
            elif len(text) <= per_page_limit:
                lines.append(text)
            else:
                omitted = len(text) - per_page_limit
                lines.append(
                    text[:per_page_limit]
                    + f'\n[Page text truncated; {omitted} chars omitted]'
                )
            lines.append('')

        return _hard_cap_output(
            '\n'.join(lines),
            '[Output truncated; request a narrower page_range]',
        )
    except Exception as exc:
        return f'Error reading PDF: {exc}'


def _read_pdf(path: Path, page_range: str | None = None) -> str:
    """Extract a bounded page selection and cap every returned PDF payload."""
    try:
        import fitz  # pymupdf
    except ImportError:
        return "Error: PyMuPDF is required to read PDF files.\nInstall with: pip install pymupdf"

    doc = None
    try:
        doc = fitz.open(str(path))
        return _render_pdf(doc, path.name, page_range)
    except Exception as exc:
        return f'Error reading PDF: {exc}'
    finally:
        if doc is not None:
            doc.close()


def _read_pdf_bytes(
    content: bytes,
    display_name: str,
    page_range: str | None = None,
) -> str:
    """Parse exact identity-pinned PDF bytes without reopening a client path."""
    try:
        import fitz  # pymupdf
    except ImportError:
        return "Error: PyMuPDF is required to read PDF files.\nInstall with: pip install pymupdf"

    doc = None
    try:
        doc = fitz.open(stream=content, filetype='pdf')
        return _render_pdf(doc, display_name, page_range)
    except Exception as exc:
        return f'Error reading PDF: {exc}'
    finally:
        if doc is not None:
            doc.close()


def _read_managed_document(
    capability: DocumentCapability,
    *,
    start_line: int,
    end_line: int | None,
    show_line_numbers: bool,
    page_range: str | None,
) -> str:
    try:
        content = document_storage.read_document_sync(storage_record(capability))
    except (MediaAccessError, MediaValidationError) as exc:
        return f'Error: {exc}'
    if capability.mime_type == 'application/pdf':
        return _read_pdf_bytes(
            content,
            capability.filename or capability.storage_key,
            page_range,
        )
    if start_line < 1:
        start_line = 1
    if end_line is not None and start_line > end_line:
        return f'Error: start_line ({start_line}) > end_line ({end_line})'

    text = content.decode('utf-8', errors='replace')
    lines = text.splitlines()
    total_lines = len(lines)
    if start_line > total_lines:
        return f'Error: start_line ({start_line}) is past end of file ({total_lines})'
    stop = min(total_lines, end_line or total_lines)
    selected: list[str] = []
    payload_budget = MAX_TOOL_OUTPUT_CHARS - 1_000
    selected_chars = 0
    resume_line = None
    for line_number in range(start_line, stop + 1):
        rendered = _truncate_output_line(lines[line_number - 1])
        if show_line_numbers:
            rendered = f'{line_number:6d}: {rendered}'
        projected = selected_chars + len(rendered) + (1 if selected else 0)
        if projected > payload_budget:
            resume_line = line_number
            break
        selected.append(rendered)
        selected_chars = projected
    shown_end = start_line + len(selected) - 1
    value = (
        f'File: {capability.filename or capability.storage_key}\n'
        f'Lines: {start_line}-{shown_end} of {total_lines}\n\n'
        + '\n'.join(selected)
    )
    if resume_line is not None:
        value += (
            f'\n[Output truncated at {MAX_TOOL_OUTPUT_CHARS} characters; '
            f'continue with start_line={resume_line}]'
        )
    return _hard_cap_output(
        value,
        f'[Output truncated; continue with start_line={resume_line or start_line}]',
    )


@tool(category='filesystem')
def Read(file_path: str, start_line: int = 1, end_line: int | None = None, show_line_numbers: bool = True, page_range: str | None = None):
    """Read the contents of a file or PDF, optionally with range selection.

    Args:
        file_path (str): Path to the file to read. Supports absolute and relative paths.
        start_line (int, optional): Starting line number for text files (1-based). Defaults to 1.
        end_line (int, optional): Ending line number for text files (1-based). If None, reads to end.
        show_line_numbers (bool, optional): Whether to show line numbers for text files. Defaults to True.
        page_range (str, optional): For PDFs only. Page range like "1-5", "1,3,5", or "3". Defaults to all pages.

    Returns:
        str: File contents with line/page numbers, or error message

    Examples:
        - Read text file: Read("path/to/file.py")
        - Read first 100 lines: Read("file.py", end_line=100)
        - Read PDF all pages: Read("document.pdf")
        - Read PDF pages 1-5: Read("document.pdf", page_range="1-5")
    """
    try:
        try:
            capability = _managed_document_capability(file_path)
            if capability is not None:
                return _read_managed_document(
                    capability,
                    start_line=start_line,
                    end_line=end_line,
                    show_line_numbers=show_line_numbers,
                    page_range=page_range,
                )
            path = _resolve_tool_path(file_path)
        except (ManagedUploadAccessError, PathEscapesRoot) as e:
            return f"Error: {e}"

        if not path.exists():
            return f"Error: File not found: {file_path}"

        if not path.is_file():
            return f"Error: Not a file: {file_path}"

        size_bytes = path.stat().st_size
        if size_bytes > MAX_READ_FILE_BYTES:
            return (
                f'Error: File exceeds the {MAX_READ_FILE_BYTES}-byte Read limit: '
                f'{size_bytes} bytes'
            )

        # Check if file is PDF
        if path.suffix.lower() == '.pdf':
            return _read_pdf(path, page_range)

        if start_line < 1:
            start_line = 1
        if end_line is not None and start_line > end_line:
            return f"Error: start_line ({start_line}) > end_line ({end_line})"

        selected: list[str] = []
        selected_chars = 0
        total_lines = 0
        last_selected = start_line - 1
        resume_line: int | None = None
        payload_budget = MAX_TOOL_OUTPUT_CHARS - 1_000
        with open(path, encoding='utf-8', errors='replace') as f:
            for line_number, line in enumerate(f, 1):
                total_lines = line_number
                if line_number < start_line:
                    continue
                if end_line is not None and line_number > end_line:
                    continue
                if resume_line is not None:
                    continue
                rendered = _truncate_output_line(line)
                if show_line_numbers:
                    rendered = f'{line_number:6d}: {rendered}'
                projected = selected_chars + len(rendered) + (1 if selected else 0)
                if projected > payload_budget:
                    if resume_line is None:
                        resume_line = line_number
                    continue
                selected.append(rendered)
                selected_chars = projected
                last_selected = line_number

        if start_line > total_lines:
            return f"Error: start_line ({start_line}) is past end of file ({total_lines})"

        shown_end = last_selected if selected else min(total_lines, end_line or total_lines)
        value = (
            f'File: {path}\nLines: {start_line}-{shown_end} of {total_lines}\n\n'
            + '\n'.join(selected)
        )
        if resume_line is not None:
            value += (
                f'\n[Output truncated at {MAX_TOOL_OUTPUT_CHARS} characters; '
                f'continue with start_line={resume_line}]'
            )
        return _hard_cap_output(
            value,
            f'[Output truncated; continue with start_line={resume_line or start_line}]',
        )

    except PermissionError:
        return f"Error: Permission denied reading: {file_path}"
    except Exception as e:
        return f"Error reading file: {str(e)}"


@tool(category='filesystem')
def Write(file_path: str, content: str, append: bool = False):
    """Write content to a file, creating it if it doesn't exist.

    Args:
        file_path (str): Path to the file to write. Supports absolute and relative paths.
        content (str): Content to write to the file.
        append (bool, optional): If True, append to file instead of overwriting. Defaults to False.

    Returns:
        str: Success message with file info, or error message

    Examples:
        - Write new file: Write("path/to/file.txt", "Hello world")
        - Append to file: Write("log.txt", "new entry\n", append=True)
    """
    try:
        try:
            path = _resolve_tool_path(file_path, write=True)
        except (ManagedUploadAccessError, PathEscapesRoot) as e:
            return f"Error: {e}"

        parent = path.parent
        if not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)

        mode = 'a' if append else 'w'
        with open(path, mode, encoding='utf-8') as f:
            f.write(content)

        action = "Appended to" if append else "Written to"
        return f"{action} file: {path}\nSize: {path.stat().st_size} bytes"

    except PermissionError:
        return f"Error: Permission denied writing to: {file_path}"
    except Exception as e:
        return f"Error writing file: {str(e)}"


@tool(category='filesystem')
def Edit(file_path: str, old_string: str, new_string: str, replace_all: bool = False, create_if_missing: bool = False):
    """Edit a file by replacing text. Supports single or all occurrences.

    Args:
        file_path (str): Path to the file to edit.
        old_string (str): The text to find and replace.
        new_string (str): The replacement text.
        replace_all (bool, optional): If True, replace all occurrences. Defaults to False (first only).
        create_if_missing (bool, optional): If True, create file with content if it doesn't exist. Defaults to False.

    Returns:
        str: Success message with changes made, or error message

    Examples:
        - Replace first occurrence: Edit("file.py", "old_text", "new_text")
        - Replace all occurrences: Edit("file.py", "old_text", "new_text", replace_all=True)
    """
    try:
        try:
            path = _resolve_tool_path(file_path, write=True)
        except (ManagedUploadAccessError, PathEscapesRoot) as e:
            return f"Error: {e}"

        if not path.exists():
            if create_if_missing:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(new_string)
                return f"Created file with content: {path}"
            return f"Error: File not found: {file_path}"

        if not old_string:
            return "Error: old_string cannot be empty"

        with open(path, encoding='utf-8', errors='replace') as f:
            content = f.read()

        if old_string not in content:
            return f"Error: String not found in file: {old_string[:50]}..."

        if replace_all:
            new_content = content.replace(old_string, new_string)
            count = content.count(old_string)
        else:
            new_content = content.replace(old_string, new_string, 1)
            count = 1

        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        return f"Replaced {count} occurrence(s) in: {path}"

    except PermissionError:
        return f"Error: Permission denied editing: {file_path}"
    except Exception as e:
        return f"Error editing file: {str(e)}"


@tool(category='filesystem')
def Grep(pattern: str, path: str = ".", include: str | None = None, exclude: str | None = None, context: int = 0, ignore_case: bool = True, max_results: int = 100):
    """Search for text patterns in files, similar to grep.

    Args:
        pattern (str): The text pattern to search for. Supports regex if valid.
        path (str, optional): Directory or file to search in. Defaults to current directory.
        include (str, optional): Glob pattern for files to include (e.g., "*.py", "*.txt").
        exclude (str, optional): Glob pattern for files to exclude (e.g., "*.log", "node_modules").
        context (int, optional): Number of lines to show before/after match. Defaults to 0.
        ignore_case (bool, optional): Case-insensitive search. Defaults to True.
        max_results (int, optional): Maximum number of matching lines to return. Defaults to 100.

    Returns:
        str: Matching lines with file:line:content format, or error message

    Examples:
        - Search all files: Grep("function_name")
        - Search Python files only: Grep("TODO", include="*.py")
        - Search with 3 lines context: Grep("error", context=3)
    """

    try:
        max_results = max(1, min(int(max_results), MAX_SEARCH_RESULTS))
        if len(pattern) > MAX_SEARCH_PATTERN_CHARS:
            return (
                f'Error: Pattern exceeds the {MAX_SEARCH_PATTERN_CHARS}-character limit'
            )
        try:
            search_path = _resolve_tool_path(path, allow_upload_root=True)
        except (ManagedUploadAccessError, PathEscapesRoot) as e:
            return f"Error: {e}"

        if not search_path.exists():
            return f"Error: Path not found: {path}"

        budget = _SearchBudget(time.monotonic() + MAX_SEARCH_SECONDS)
        results = []
        limits_hit: set[str] = set()
        flags = regex_engine.IGNORECASE if ignore_case else 0

        try:
            compiled_pattern = regex_engine.compile(pattern, flags)
        except regex_engine.error:
            pattern = regex_engine.escape(pattern)
            compiled_pattern = regex_engine.compile(pattern, flags)

        files_to_search = []

        if search_path.is_file():
            files_to_search = [search_path]
        else:
            enumerated_files = 0
            entries = _collect_search_entries(
                search_path,
                budget,
                recursive=True,
                exclude_directory=exclude,
            )
            for entry in entries:
                if entry.is_directory:
                    continue
                if enumerated_files >= MAX_SEARCH_FILES:
                    limits_hit.add(
                        f'{MAX_SEARCH_FILES}-file enumeration limit'
                    )
                    break
                enumerated_files += 1
                name = entry.path.name
                if include and not fnmatch.fnmatch(name, include):
                    continue
                if exclude and fnmatch.fnmatch(name, exclude):
                    continue
                files_to_search.append(entry.path)

        total_scanned_bytes = 0
        for file_path in files_to_search:
            if not budget.within_deadline():
                break
            try:
                file_size = file_path.stat().st_size
                if file_size > MAX_SEARCH_FILE_BYTES:
                    limits_hit.add(
                        f'{MAX_SEARCH_FILE_BYTES}-byte per-file limit'
                    )
                    continue
                remaining_total = MAX_SEARCH_TOTAL_BYTES - total_scanned_bytes
                if file_size > remaining_total:
                    limits_hit.add(
                        f'{MAX_SEARCH_TOTAL_BYTES}-byte total scan limit'
                    )
                    continue

                with open(file_path, 'rb') as f:
                    for (
                        line_num,
                        search_line,
                        display_line,
                        line_bytes,
                    ) in _bounded_search_lines(f, file_size, budget):
                        total_scanned_bytes += line_bytes
                        try:
                            matched = compiled_pattern.search(
                                search_line,
                                timeout=MAX_REGEX_MATCH_SECONDS,
                            )
                        except TimeoutError:
                            return (
                                'Error: regular expression timed out; '
                                'use a simpler or more specific pattern'
                            )
                        if matched:
                            results.append({
                                'file': str(file_path),
                                'line': line_num,
                                'content': display_line,
                            })
                            if len(results) >= max_results:
                                break
            except (OSError, UnicodeDecodeError):
                continue

            if len(results) >= max_results:
                break

        if budget.limit_reason:
            limits_hit.add(budget.limit_reason)
        limit_note = ''
        if limits_hit:
            limit_note = f"\n[Search limited by {', '.join(sorted(limits_hit))}]"
        if not results:
            return f"No matches found for: {pattern}{limit_note}"

        output = [f"Found {len(results)} match(es) for '{pattern}':\n"]
        for r in results:
            if context > 0:
                output.append(f"\n--- {r['file']} (line {r['line']}) ---")
            output.append(f"{r['file']}:{r['line']}: {r['content']}")

        return _hard_cap_output(
            '\n'.join(output) + limit_note,
            '[Output truncated; narrow the path/pattern or lower max_results]',
        )

    except Exception as e:
        return f"Error during search: {str(e)}"


@tool(category='filesystem')
def Glob(pattern: str, path: str = ".", recursive: bool = True, include_dirs: bool = False, max_results: int = 100):
    """Find files matching a glob pattern, similar to glob.

    Args:
        pattern (str): Glob pattern to match (e.g., "*.py", "**/*.js", "src/**/*.ts").
        path (str, optional): Directory to search in. Defaults to current directory.
        recursive (bool, optional): Search recursively. Defaults to True.
        include_dirs (bool, optional): Include directories in results. Defaults to False.
        max_results (int, optional): Maximum number of files to return. Defaults to 100.

    Returns:
        str: List of matching file paths, or error message

    Examples:
        - All Python files: Glob("*.py")
        - Recursive Python files: Glob("**/*.py", path="src")
        - TypeScript in src: Glob("*.ts", path="src")
    """
    try:
        max_results = max(1, min(int(max_results), MAX_SEARCH_RESULTS))
        if len(pattern) > MAX_GLOB_PATTERN_CHARS:
            return (
                'Error: Pattern exceeds the '
                f'{MAX_GLOB_PATTERN_CHARS}-character limit'
            )
        try:
            search_path = _resolve_tool_path(path, allow_upload_root=True)
        except (ManagedUploadAccessError, PathEscapesRoot) as e:
            return f"Error: {e}"

        if not search_path.exists():
            return f"Error: Directory not found: {path}"

        if not search_path.is_dir():
            return f"Error: Not a directory: {path}"

        budget = _SearchBudget(time.monotonic() + MAX_SEARCH_SECONDS)
        results = []
        limits_hit: set[str] = set()

        if '**' in pattern:
            pattern = pattern.replace('**', '*')
            recursive = True

        entries = _collect_search_entries(
            search_path,
            budget,
            recursive=recursive,
        )
        enumerated_candidates = 0
        for entry in entries:
            if entry.is_directory and not include_dirs:
                continue
            if enumerated_candidates >= MAX_SEARCH_FILES:
                limits_hit.add(
                    f'{MAX_SEARCH_FILES}-file enumeration limit'
                )
                break
            enumerated_candidates += 1
            if fnmatch.fnmatch(entry.path.name, pattern):
                results.append(str(entry.path))
            if len(results) >= max_results:
                break

        if budget.limit_reason:
            limits_hit.add(budget.limit_reason)
        limit_note = ''
        if limits_hit:
            limit_note = f"\n[Search limited by {', '.join(sorted(limits_hit))}]"

        if not results:
            return f"No files found matching: {pattern}{limit_note}"

        output = [f"Found {len(results)} file(s):\n"]
        for r in results:
            output.append(r)

        return _hard_cap_output(
            '\n'.join(output) + limit_note,
            '[Output truncated; narrow the path/pattern or lower max_results]',
        )

    except Exception as e:
        return f"Error during glob: {str(e)}"


def _clean_search_text(value: Any) -> str:
    """Convert provider snippets to plain, Unicode-preserving text."""
    return html.unescape(re.sub(r'<[^>]+>', '', str(value or ''))).strip()


@tool(category='web', retryable=False, max_attempts=1)
def Search(query: str, max_results: int = 10):
    """Search the web for current information using the Brave Search API.

    Args:
        query (str): The search query.
        max_results (int): Maximum number of results, from 1 to 20.

    Returns:
        str: Search results with titles, descriptions, and URLs.
    """
    import requests

    api_key = settings.brave_search_api_key
    if not api_key:
        return ToolOutcome.failure(
            'brave_search_not_configured',
            'Brave Search is not configured. Set BRAVE_SEARCH_API_KEY.',
        )

    result_count = max(1, min(max_results, 20))
    try:
        response = requests.get(
            'https://api.search.brave.com/res/v1/web/search',
            params={'q': query, 'count': result_count},
            headers={
                'Accept': 'application/json',
                'X-Subscription-Token': api_key,
            },
            timeout=15,
        )
        response.raise_for_status()
        results = response.json().get('web', {}).get('results', [])
    except requests.exceptions.HTTPError as exc:
        status = getattr(exc.response, 'status_code', None)
        suffix = f' (HTTP {status})' if status is not None else ''
        return ToolOutcome.failure(
            'brave_search_http_error',
            f'Brave Search request failed{suffix}.',
        )
    except requests.exceptions.RequestException:
        return ToolOutcome.failure(
            'brave_search_request_error',
            'Brave Search request failed due to a network error.',
        )
    except (TypeError, ValueError):
        return ToolOutcome.failure(
            'brave_search_response_error',
            'Brave Search returned an invalid response.',
        )

    if not results:
        return f'No results found for: {query}'

    output = [f"Search results for '{query}':\n"]
    for index, result in enumerate(results[:result_count], 1):
        output.append(f"{index}. {_clean_search_text(result.get('title')) or 'No title'}")
        output.append(
            f"   {_clean_search_text(result.get('description')) or 'No description'}"
        )
        output.append(f"   URL: {result.get('url', 'No URL')}")
        output.append('')
    return '\n'.join(output)


@tool(category='web', retryable=False, max_attempts=1)
def Tavily_Search(query: str, max_results: int = 10):
    """Search the web for information using Tavily API.

    Args:
        query (str): The search query.
        max_results (int, optional): Maximum number of results. Defaults to 10.

    Returns:
        str: Search results with titles, content, and URLs, or error message
    """

    try:
        # Lazy import: tavily (and its transitive cohere dep) added ~0.7s+ to
        # every startup but is only needed when the Search tool actually runs.
        from tavily import TavilyClient

        api_key = settings.tavily_api_key if settings.tavily_api_key else None
        if not api_key:
            api_key = os.getenv('TAVILY_API_KEY')

        if not api_key:
            return ToolOutcome.failure(
                'tavily_search_not_configured',
                'Tavily Search is not configured. Set TAVILY_API_KEY.',
            )

        client = TavilyClient(api_key=api_key)
        results = client.search(query=query, max_results=max_results)

        if not results or "results" not in results:
            return f"No results found for: {query}"

        search_results = results["results"]
        output = [f"Search results for '{query}':\n"]
        for i, result in enumerate(search_results, 1):
            output.append(f"{i}. {result.get('title', 'No title')}")
            output.append(f"   {result.get('content', 'No description')[:200]}...")
            output.append(f"   URL: {result.get('url', 'No URL')}")
            output.append("")

        return '\n'.join(output)

    except Exception as exc:
        status = getattr(getattr(exc, 'response', None), 'status_code', None)
        suffix = f' (HTTP {status})' if status is not None else ''
        return ToolOutcome.failure(
            'tavily_search_error',
            f'Tavily Search request failed{suffix}.',
        )


@tool(category='web')
def WebFetch(url: str, max_length: int = 5000, include_images: bool = False):
    """Fetch and extract content from web pages.

    Args:
        url (str): The URL to fetch content from.
        max_length (int, optional): Maximum characters to return. Defaults to 5000.
        include_images (bool, optional): Include image URLs in the output. Defaults to False.

    Returns:
        str: Extracted text content from the web page, or error message

    Examples:
        - Fetch a page: WebFetch("https://example.com")
        - Longer content: WebFetch("https://example.com", max_length=10000)
    """
    try:
        import requests
        from bs4 import BeautifulSoup

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, timeout=15, headers=headers)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        for script in soup(['script', 'style', 'nav', 'footer', 'header']):
            script.decompose()

        text = soup.get_text(separator='\n', strip=True)

        lines = [line.strip() for line in text.split('\n')]
        text = '\n'.join(line for line in lines if line)

        if len(text) > max_length:
            text = text[:max_length] + '\n... (truncated)'

        if include_images:
            images = [img.get('src') or img.get('data-src') for img in soup.find_all('img')]
            images = [img for img in images if img]
            if images:
                text += f'\n\nImages found: {", ".join(images[:10])}'

        return f"URL: {url}\n\n{text}"

    except requests.exceptions.RequestException as e:
        return f"Error fetching URL: {str(e)}"
    except Exception as e:
        return f"Error processing page: {str(e)}"


@tool(category='system')
def take_screenshot():
    """Use this tool to take a screenshot of the screen."""
    screenshot = _pyautogui().screenshot()

    return ['image', screenshot]

@tool(category='system')
def text_input(text: str):
    """Use this tool to take make text inputs.
    Args:
        text (str): Text to input.

    Returns:
        str: Text input completed.
    """
    _pyautogui().write(text, 0.15)

    return 'Text input completed'

@tool(category='system')
def key_press(key: str):
    """Use this tool to take make key presses.
    Args:
        key (str): Name of key to press.

    Returns:
        str: Keypress completed.
    """
    _pyautogui().press(key.lower())

    return 'Keypress completed'

@tool(category='system')
def hot_key(hotkeys: list):
    """Use this tool to take make hot key presses.
    Args:
        hotkeys (list): list of keys to press together.

    Returns:
        str: Keypress completed.
    """
    _pyautogui().hotkey(*hotkeys)

    return 'Keypress completed'

@tool(category='system')
def mouse_click(x: int, y: int):
    """Use this tool to take make mouse clicks.
    Args:
        x (int): X coordinate of mouse click.
        y (int): y coordinate of mouse click.

    Returns:
        str: Mouse click completed.
    """
    _pyautogui().click(x, y)

    return 'Mouse Click completed'

@tool(category='system')
def mouse_double_click(x: int, y: int):
    """Use this tool to take make mouse double clicks.
    Args:
        x (int): X coordinate of mouse click.
        y (int): y coordinate of mouse click.

    Returns:
        str: Mouse double-click completed.
    """
    _pyautogui().doubleClick(x, y)

    return 'Mouse double-click completed.'

@tool(category='system')
def mouse_right_click(x: int, y: int):
    """Use this tool to take make mouse right clicks.
    Args:
        x (int): X coordinate of mouse click.
        y (int): y coordinate of mouse click.

    Returns:
        str: Mouse right-click completed.
    """
    _pyautogui().rightClick(x, y)

    return 'Mouse double-click completed.'

@tool(category='system')
async def create_agent(name: str, provider: str, description: str, tools: list[str], model: str | None = None, temperature: float | None = None, parent: Any | None = None):
    """Use this tool to create sub agents for specific tasks.

    Args:
        name (str): The name of the agent
        provider (str): The name of the provider to use. Select from [openai, google, anthropic, groq, together, clarifai].
        description (str): Prompt describing the agent's role and functionalities. Should include the agent's role, capabilities and any other info the agent needs to be able to complete it's task. Be as thorough as possible.
        tools (list): Tools the agent needs to complete it's tasks if any.
        model (str, optional): Model id to use. Omit to use the provider's default model.
        temperature (float, optional): Sampling temperature. Omit to use the provider's default temperature.

    Returns:
        str: A message indicating whether the the sub agent was created or not.
    """

    # Local import to avoid circular-import issues.
    from cognitrix.agents import Agent  # noqa: WPS433  (allow internal import)

    agent = await Agent.create_agent(  # type: ignore[attr-defined]
        name=name,
        system_prompt=description,
        provider=provider,
        model=model or '',
        temperature=temperature,
        is_sub_agent=True if parent else False,
        parent_id=parent.id if parent else None,
        tools=tools
    )

    if agent:
        agent.system_prompt = description
        await agent.save()
        return {'status': 'success', 'message': f'Agent "{name}" created successfully'}

    return {'status': 'error', 'message': f'Error creating agent "{name}"'}


@tool(category='system')
async def list_agents():
    """List the saved agents available to delegate tasks to (e.g. with call_agent).

    Returns:
        str: Each agent's name, provider/model, and a short description.
    """
    from cognitrix.agents import Agent  # noqa: WPS433

    authority = current_execution_context()
    agents = [
        agent
        for agent in await Agent.list_agents()  # type: ignore[attr-defined]
        if authority.agent_allowed(str(getattr(agent, 'id', '')))
    ]
    if not agents:
        return "No agents found."

    lines = [f"{len(agents)} agent(s):"]
    for a in agents:
        llm = getattr(a, 'llm', None)
        provider = getattr(llm, 'provider', '') or '?'
        model = getattr(llm, 'model', '') or '?'
        desc = ' '.join((a.system_prompt or '').split())
        if len(desc) > 160:
            desc = desc[:160] + '…'
        line = f"- {a.name} [{provider}/{model}]"
        if desc:
            line += f" — {desc}"
        lines.append(line)
    return '\n'.join(lines)

# Delegation depth for call_agent: bounds agent->agent recursion (A calls B
# calls A ...). Context-local so concurrent turns don't share a counter.
_CALL_AGENT_DEPTH = contextvars.ContextVar('call_agent_depth', default=0)
MAX_AGENT_CALL_DEPTH = 3


@tool(category='system')
async def call_agent(name: str, task: str, interface: str = 'task'):
    """Run a task with a sub agent

    Args:
        name (str): Name of the agent to call
        task (str): The task|query to perform|answer
        interface (str): Filled in by the runtime; leave at its default.

    Returns:
        str: The result of the task

    Raises:
        Exception: If the agent is not found or the task fails
    """
    authority = current_execution_context()
    # Durable execution is already planned from immutable agent/tool
    # snapshots. Delegating to a live saved agent here would escape that
    # snapshot and make resume behavior depend on mutable configuration.
    if authority.run_id is not None:
        return 'Error calling agent: delegation is unavailable inside durable task runs'

    depth = _CALL_AGENT_DEPTH.get()
    if depth >= MAX_AGENT_CALL_DEPTH:
        return f"Error calling agent: delegation depth limit ({MAX_AGENT_CALL_DEPTH}) reached"
    token = _CALL_AGENT_DEPTH.set(depth + 1)
    try:
        from cognitrix.agents import Agent  # noqa: WPS433
        from cognitrix.sessions.base import Session  # noqa: WPS433

        agent = await Agent.find_one({'name': name})
        if not agent:
            return f"Error calling agent: {name} not found"
        if not authority.agent_allowed(str(agent.id)):
            # Keep target existence opaque to restricted credentials.
            return f"Error calling agent: {name} was not found or is not allowed"
        child_context = delegated_execution_context(authority)
        # Preserve identity when there is no turn-scoped authority to drop.
        if child_context == authority:
            child_context = authority

        chunks: list[str] = []

        async def capture(payload=None, *args, **kwargs):
            content = payload.get('content', '') if isinstance(payload, dict) else (str(payload) if payload else '')
            if content:
                chunks.append(content)

        # Delegation is stateless: a shared saved session would leak prompts
        # across users, API keys, and parent conversations. Authenticated
        # callers always inherit web safety policy even if the model supplies
        # a weaker ``interface`` tool argument. Internal CLI callers retain
        # the compatibility task interface.
        if authority.user_id is not None or authority.api_key_id is not None:
            session_interface = 'web'
        else:
            session_interface = (
                interface
                if interface in ('web', 'ws', 'compat')
                else 'task'
            )
        session = Session(agent_id=str(agent.id), user_id=authority.user_id)
        await session(
            task,
            agent,
            session_interface,
            True,
            capture,
            {},
            save_history=False,
            tool_context=child_context,
            record_history=True,
            persist_history=False,
            compact_history=False,
        )
        return ''.join(chunks).strip() or f"Agent '{name}' returned no output."
    except Exception as e:
        return f"Error calling agent: {str(e)}"
    finally:
        _CALL_AGENT_DEPTH.reset(token)

@tool(category='system')
async def create_new_team(name: str, description: str, agent_names: list[str], leader_name: str | None = None):
    """Use this tool to create new teams with existing agents.

    Args:
        name (str): The name of the team
        description (str): A description of the team's purpose and goals
        agent_names (List[str]): List of existing agent names to be added to the team
        leader_name (Optional[str]): Name of an existing agent to be set as the team leader (optional)

    Returns:
        str: A message indicating whether the team was created successfully or not.
    """

    try:
        from cognitrix.teams.base import TeamManager  # noqa: WPS433

        team_manager = TeamManager()
        new_team = team_manager.create_team(name, description)
        new_team.description = description

        from cognitrix.agents import Agent  # noqa: WPS433

        for agent_name in agent_names:
            agent = await Agent.load_agent(agent_name)  # type: ignore[attr-defined]
            if agent:
                await new_team.add_agent(agent)
            else:
                print(f"Warning: Agent '{agent_name}' not found and couldn't be added to the team.")

        if leader_name:
            leader = await Agent.load_agent(leader_name)  # type: ignore[attr-defined]
            if leader and leader.id in new_team.assigned_agents:
                new_team.leader = leader
            else:
                print(f"Warning: Leader '{leader_name}' not found or not in the team. No leader set.")

        await new_team.save()

        return ['team', new_team, f"Team '{name}' created successfully with {len(new_team.assigned_agents)} agents."]
    except Exception as e:
        return f"Error creating team: {str(e)}"

# REMOVED - Replaced by skills:
# - internet_search -> internet-search skill
# - web_scraper -> web-scraper skill
# - wikipedia -> wikipedia skill


# create_tool removed: it ran attacker-supplied source via exec() with no sandbox
# (unauthenticated RCE). Follows the ede84af precedent that removed python_repl/calculator.


@tool(category='system')
def bash(command: str, timeout: int | None = 180, working_dir: str | None = str(Path.cwd())) -> str:
    """Execute a single whitelisted terminal command.

    Only one command runs per call — command chaining and shell operators
    (`;`, `&&`, `|`, `>`, `$()`, backticks, `..`) are rejected, and only
    whitelisted base commands are allowed (ls, cat, grep, find, git, python,
    pip, node, npm, mkdir, mv, cp, touch, ...). To run a command inside a
    subdirectory (e.g. run tests for a package that lives in a subfolder), pass
    that folder as working_dir rather than using `cd` — e.g. bash("python -m
    pytest tests", working_dir="myproject"). `python -c`/`node -e` inline code is
    not allowed; put code in a file and run the file instead.

    Args:
        command (str): The single command to execute (no chaining).
        timeout (int, optional): Maximum execution time in seconds.
        working_dir (str, optional): Directory to run the command in. Use this
            instead of `cd` to operate in a subfolder. Defaults to current dir.

    Returns:
        str: Command output or error message.
    """
    context = current_execution_context()
    try:
        require_host_process_authority(context.host_process_mode)
    except HostProcessAccessError as e:
        return f'Error: {e}'

    # Validate and resolve the working directory
    if working_dir:
        try:
            work_dir = Path(working_dir).resolve()
            if not work_dir.exists() or not work_dir.is_dir():
                return f"Error: Invalid working directory - {working_dir}"
        except Exception as e:
            return f"Error: Working directory validation failed - {str(e)}"
    else:
        work_dir = Path.cwd()

    # Normalise timeout (the LLM may pass it as a string)
    try:
        timeout_s = int(timeout) if timeout is not None else DEFAULT_TIMEOUT
    except (ValueError, TypeError):
        return f"Error: Invalid timeout value '{timeout}'. Must be an integer."

    # Sandbox mode: run through a real shell (pipes, &&, arbitrary commands),
    # bypassing the whitelist. ONLY for throwaway sandboxes (benchmark/CI
    # containers) where the environment itself is the isolation boundary — never
    # enable on a host you care about. Off by default.
    if os.getenv('COGNITRIX_SANDBOX_SHELL', '').strip().lower() in ('1', 'true', 'yes'):
        return _run_sandbox_shell(
            command,
            cwd=str(work_dir),
            timeout=timeout_s,
            host_process_mode=context.host_process_mode,
        )

    # Default: the shared safety boundary — whitelist + argv + shell=False.
    try:
        return run_whitelisted(
            command,
            cwd=str(work_dir),
            timeout=timeout_s,
            host_process_mode=context.host_process_mode,
        )
    except CommandNotAllowed as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("bash tool failed")
        return f"Error executing command: {e}"


def _run_sandbox_shell(
    command: str,
    cwd: str,
    timeout: int,
    *,
    host_process_mode: HostProcessMode,
) -> str:
    """Run a command through a real shell (no whitelist). Sandbox-only; gated by
    COGNITRIX_SANDBOX_SHELL."""
    import subprocess
    require_host_process_authority(host_process_mode)
    try:
        proc = subprocess.run(
            command, shell=True, cwd=cwd, timeout=timeout,
            capture_output=True, text=True,
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"
    out = (proc.stdout or '').strip()
    err = (proc.stderr or '').strip()
    if proc.returncode != 0:
        detail = err or out or f"exit code {proc.returncode}"
        return f"Command failed (exit {proc.returncode}): {detail}"
    return out or (f"[stderr: {err}]" if err else "Command executed successfully (no output)")
