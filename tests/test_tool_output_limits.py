import sys
from types import SimpleNamespace

import pytest

from cognitrix.config import settings
from cognitrix.tools import misc
from cognitrix.tools.misc import (
    DEFAULT_PDF_PAGES,
    MAX_FILE_LINE_CHARS,
    MAX_GLOB_PATTERN_CHARS,
    MAX_READ_FILE_BYTES,
    MAX_PDF_PAGES_PER_READ,
    MAX_SEARCH_PATTERN_CHARS,
    MAX_TOOL_OUTPUT_CHARS,
    Glob,
    Grep,
    Read,
)


@pytest.mark.asyncio
async def test_read_caps_total_output_and_reports_resume_line(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    target = tmp_path / 'large.txt'
    target.write_text(
        ''.join(f'line-{index}: ' + ('x' * 1000) + '\n' for index in range(100))
    )

    result = await Read.run(file_path='large.txt', show_line_numbers=False)

    assert len(result.content) <= MAX_TOOL_OUTPUT_CHARS
    assert '[Output truncated' in result.content
    assert 'start_line=' in result.content


@pytest.mark.asyncio
async def test_read_truncates_one_oversized_line_with_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    target = tmp_path / 'one-line.txt'
    target.write_text('z' * (MAX_FILE_LINE_CHARS * 3))

    result = await Read.run(file_path='one-line.txt')

    assert len(result.content) <= MAX_TOOL_OUTPUT_CHARS
    assert '[line truncated;' in result.content


@pytest.mark.asyncio
async def test_read_rejects_files_above_the_hard_input_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    target = tmp_path / 'too-large.txt'
    with target.open('wb') as stream:
        stream.truncate(MAX_READ_FILE_BYTES + 1)

    result = await Read.run(file_path='too-large.txt')

    assert result.content.startswith('Error')
    assert str(MAX_READ_FILE_BYTES) in result.content


@pytest.mark.asyncio
async def test_grep_truncates_oversized_matching_line(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    target = tmp_path / 'match.txt'
    target.write_text('needle-' + ('s' * (MAX_TOOL_OUTPUT_CHARS * 2)))

    result = await Grep.run(pattern='needle', path='.')

    assert len(result.content) <= MAX_TOOL_OUTPUT_CHARS
    assert '[line truncated;' in result.content
    assert 's' * (MAX_FILE_LINE_CHARS + 1) not in result.content


@pytest.mark.asyncio
async def test_grep_does_not_mark_a_complete_final_line_as_truncated(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    (tmp_path / 'final-line.txt').write_text('needle')

    result = await Grep.run(pattern='needle', path='.')

    assert '[line truncated;' not in result.content


@pytest.mark.asyncio
async def test_grep_rejects_an_oversized_pattern(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    (tmp_path / 'small.txt').write_text('content')

    result = await Grep.run(
        pattern='x' * (MAX_SEARCH_PATTERN_CHARS + 1), path='.'
    )

    assert result.content.startswith('Error')
    assert str(MAX_SEARCH_PATTERN_CHARS) in result.content


@pytest.mark.asyncio
async def test_grep_skips_files_above_the_per_file_scan_limit(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    monkeypatch.setattr(misc, 'MAX_SEARCH_FILE_BYTES', 5)
    (tmp_path / 'oversized.txt').write_text('needle')

    result = await Grep.run(pattern='needle', path='.')

    assert result.content.startswith('No matches found')


@pytest.mark.asyncio
async def test_grep_stops_before_exceeding_the_total_scan_byte_budget(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    monkeypatch.setattr(misc, 'MAX_SEARCH_TOTAL_BYTES', 6)
    (tmp_path / 'a-first.txt').write_text('abcdef')
    (tmp_path / 'b-second.txt').write_text('needle')
    monkeypatch.setattr(
        misc.os,
        'walk',
        lambda _path: iter([
            (str(tmp_path), [], ['a-first.txt', 'b-second.txt'])
        ]),
    )

    result = await Grep.run(pattern='needle', path='.')

    assert result.content.startswith('No matches found')


@pytest.mark.asyncio
async def test_grep_caps_enumerated_files_before_searching(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    monkeypatch.setattr(misc, 'MAX_SEARCH_FILES', 1)
    (tmp_path / 'a-first.txt').write_text('haystack')
    (tmp_path / 'b-second.txt').write_text('needle')
    monkeypatch.setattr(
        misc.os,
        'walk',
        lambda _path: iter([
            (str(tmp_path), [], ['a-first.txt', 'b-second.txt'])
        ]),
    )

    result = await Grep.run(pattern='needle', path='.')

    assert result.content.startswith('No matches found')


@pytest.mark.asyncio
async def test_grep_bounds_each_line_before_running_the_regex(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    (tmp_path / 'long-line.txt').write_text(
        ('x' * MAX_FILE_LINE_CHARS) + 'needle'
    )

    result = await Grep.run(pattern='needle', path='.')

    assert result.content.startswith('No matches found')


@pytest.mark.asyncio
async def test_grep_applies_per_match_timeout_with_regex_engine(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    (tmp_path / 'input.txt').write_text('content')
    captured = {}

    class TimeoutPattern:
        def search(self, value, *, timeout=None):
            captured['value'] = value
            captured['timeout'] = timeout
            if timeout is not None:
                raise TimeoutError('regex timed out')
            return None

    engine = SimpleNamespace(
        IGNORECASE=1,
        compile=lambda _pattern, _flags=0: TimeoutPattern(),
        escape=lambda value: value,
        error=ValueError,
    )
    monkeypatch.setattr(misc, 'regex_engine', engine, raising=False)
    monkeypatch.setattr(misc, 'MAX_REGEX_MATCH_SECONDS', 0.01, raising=False)

    result = await Grep.run(pattern='content', path='.')

    assert 'regular expression timed out' in result.content
    assert captured['timeout'] == 0.01


@pytest.mark.asyncio
async def test_glob_rejects_an_oversized_pattern(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())

    result = await Glob.run(
        pattern='x' * (MAX_GLOB_PATTERN_CHARS + 1), path='.'
    )

    assert result.content.startswith('Error')
    assert str(MAX_GLOB_PATTERN_CHARS) in result.content


@pytest.mark.asyncio
async def test_glob_caps_enumerated_nonmatching_files_and_reports_limit(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    monkeypatch.setattr(misc, 'MAX_SEARCH_FILES', 2)
    for name in ('one.txt', 'two.txt', 'three.txt'):
        (tmp_path / name).write_text('content')
    monkeypatch.setattr(
        misc.os,
        'walk',
        lambda _path: iter([
            (str(tmp_path), [], ['one.txt', 'two.txt', 'three.txt'])
        ]),
    )

    result = await Glob.run(pattern='*.py', path='.')

    assert result.content.startswith('No files found')
    assert '2-file enumeration limit' in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize('tool', [Grep, Glob])
async def test_search_deadline_bounds_empty_directory_trees(
    tool, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    current = tmp_path
    for index in range(8):
        current = current / f'empty-{index}'
        current.mkdir()
    monkeypatch.setattr(misc, 'MAX_SEARCH_SECONDS', 0.0, raising=False)

    if tool is Grep:
        result = await tool.run(pattern='needle', path='.')
    else:
        result = await tool.run(pattern='*.txt', path='.')

    assert 'search deadline' in result.content.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize('tool', [Grep, Glob])
async def test_search_directory_budget_bounds_empty_directory_trees(
    tool, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    current = tmp_path
    for index in range(8):
        current = current / f'empty-{index}'
        current.mkdir()
    monkeypatch.setattr(misc, 'MAX_SEARCH_DIRECTORIES', 2, raising=False)

    if tool is Grep:
        result = await tool.run(pattern='needle', path='.')
    else:
        result = await tool.run(pattern='*.txt', path='.')

    assert 'directory traversal limit' in result.content.lower()


class _FakePage:
    def __init__(self, number):
        self.number = number

    def get_text(self, _kind):
        return f'page-{self.number} ' + ('p' * MAX_TOOL_OUTPUT_CHARS)


class _FakeDocument:
    def __init__(self, pages):
        self.pages = [_FakePage(index + 1) for index in range(pages)]
        self.closed = False

    def __len__(self):
        return len(self.pages)

    def __getitem__(self, index):
        return self.pages[index]

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_pdf_defaults_to_bounded_pages_and_hard_output_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    target = tmp_path / 'large.pdf'
    target.write_bytes(b'%PDF-fake')
    document = _FakeDocument(MAX_PDF_PAGES_PER_READ + 5)
    monkeypatch.setitem(sys.modules, 'fitz', SimpleNamespace(open=lambda _path: document))

    result = await Read.run(file_path='large.pdf')

    assert len(result.content) <= MAX_TOOL_OUTPUT_CHARS
    assert f'showing first {DEFAULT_PDF_PAGES}' in result.content
    assert f'page-{DEFAULT_PDF_PAGES}' in result.content
    assert f'page-{DEFAULT_PDF_PAGES + 1}' not in result.content
    assert document.closed is True


@pytest.mark.asyncio
async def test_pdf_explicit_range_is_limited_and_reports_pagination(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    target = tmp_path / 'range.pdf'
    target.write_bytes(b'%PDF-fake')
    document = _FakeDocument(MAX_PDF_PAGES_PER_READ + 5)
    monkeypatch.setitem(sys.modules, 'fitz', SimpleNamespace(open=lambda _path: document))

    result = await Read.run(file_path='range.pdf', page_range='1-999')

    assert len(result.content) <= MAX_TOOL_OUTPUT_CHARS
    assert f'limited to {MAX_PDF_PAGES_PER_READ} pages' in result.content
    assert 'request another page_range' in result.content
