"""Read web pages into the knowledge base.

``read_site`` fetches one page, or that page and the pages it links to on the
same site (breadth first, staying under the starting folder), and returns
each page's text. Every request, and every redirect hop, is checked against
private and loopback addresses first, so a knowledge source can't be used to
reach the server's own network. Crawling follows the site's robots.txt and
waits a moment between pages.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

logger = logging.getLogger('cognitrix.log')

MAX_PAGES = 50
"""The most pages one web source can read."""
MAX_PAGE_BYTES = 5 * 1024 * 1024
TIMEOUT = 20.0
CRAWL_DELAY = 0.5
USER_AGENT = 'Mozilla/5.0 (compatible; CognitrixKnowledge/1.0; +https://github.com/theonlyamos/cognitrix)'
ALLOW_ENV = 'COGNITRIX_WEBFETCH_ALLOW_PRIVATE'

# Links that are never pages worth reading.
_SKIP_SUFFIXES = (
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico', '.bmp', '.tif', '.tiff', '.avif',
    '.mp3', '.mp4', '.wav', '.ogg', '.webm', '.mov', '.avi', '.zip', '.tar', '.gz', '.tgz', '.rar', '.7z',
    '.exe', '.msi', '.dmg', '.pkg', '.deb', '.rpm', '.apk', '.iso', '.woff', '.woff2', '.ttf', '.otf',
    '.css', '.js', '.mjs', '.map', '.xml', '.rss', '.atom', '.json', '.docx', '.xlsx', '.pptx',
)


_INDENT = '\ue000'  # stands in for a code indent while whitespace is collapsed
_BLOCKS = ['p', 'div', 'li', 'pre', 'tr', 'table', 'ul', 'ol', 'dl', 'dt', 'dd', 'blockquote', 'section',
           'article', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'figcaption', 'caption', 'hr']


class WebError(ValueError):
    """A web source that can't be read; the message is safe to show."""


@dataclass
class Page:
    url: str
    title: str
    text: str


@dataclass
class SiteResult:
    pages: list[Page] = field(default_factory=list)
    skipped: int = 0
    """Linked pages that couldn't be read (errors, not allowed, not text)."""


def _allow_private() -> bool:
    return os.getenv(ALLOW_ENV, '').strip().lower() in ('1', 'true', 'yes')


def normalize_url(url: str) -> str:
    """The address as typed, with a scheme, without the #fragment.

    Raises WebError for anything that isn't an http(s) address."""
    url = str(url or '').strip()
    if not url:
        raise WebError('Paste the address of a web page')
    if '://' not in url:
        url = 'https://' + url
    url, _fragment = urldefrag(url)
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise WebError('Use a web address that starts with http:// or https://')
    try:
        parsed.port  # noqa: B018 - raises on a port that isn't a number
    except ValueError as exc:
        raise WebError('That address has an invalid port') from exc
    if parsed.username or parsed.password:
        raise WebError("Addresses with a sign-in name or password in them can't be added")
    if len(url) > 2000:
        raise WebError('That address is too long')
    return url


async def _check(url: str) -> None:
    from cognitrix.utils.webhooks import check_public_url

    reason = await asyncio.to_thread(check_public_url, url, label='The address',
                                     allow_private=_allow_private(), allow_env=ALLOW_ENV)
    if reason:
        raise WebError(reason)


async def _get(client, url: str) -> tuple[str, str, bytes]:
    """GET ``url`` following up to 5 checked redirects. Returns (final url, content type, body)."""
    current = url
    for _hop in range(6):
        await _check(current)
        async with client.stream('GET', current) as response:
            if response.is_redirect and response.headers.get('location'):
                current = urldefrag(urljoin(current, response.headers['location']))[0]
                continue
            if response.status_code >= 400:
                raise WebError(f'The site answered with an error ({response.status_code})')
            declared = response.headers.get('content-length')
            if declared and declared.isdigit() and int(declared) > MAX_PAGE_BYTES:
                raise WebError('That page is larger than 5 MB')
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_PAGE_BYTES:
                    raise WebError('That page is larger than 5 MB')
            kind = response.headers.get('content-type', '').split(';')[0].strip().lower()
            return current, kind, bytes(body)
    raise WebError('That address redirects too many times')


def _decode(body: bytes) -> str:
    from cognitrix.knowledge.extract import _decode as decode

    return decode(body)


def _html_page(url: str, body: bytes) -> tuple[str, str, list[str]]:
    """(title, text, links) of an HTML page. Menus, headers and footers are
    dropped so passages hold the page's own content."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(_decode(body), 'html.parser')
    title = ''
    if soup.title and soup.title.string:
        title = ' '.join(soup.title.string.split())
    links: list[str] = []
    for anchor in soup.find_all('a', href=True):
        href = urldefrag(urljoin(url, anchor['href']))[0]
        if href.startswith(('http://', 'https://')):
            links.append(href)
    for tag in soup(['script', 'style', 'noscript', 'template', 'svg', 'nav', 'header', 'footer', 'form', 'aside']):
        tag.decompose()
    main = soup.find('main') or soup.find('article') or soup.body or soup
    # Line breaks only between blocks: inline pieces (links, code highlighting
    # spans) stay on their line. Headings start a new paragraph so passages
    # break at sections.
    for block in main.find_all(_BLOCKS):
        if re.fullmatch(r'h[1-6]', block.name):
            block.insert_before('\n\n')
        block.insert_after('\n')
    for br in main.find_all('br'):
        br.replace_with('\n')
    # Code keeps its indentation; everywhere else runs of spaces collapse.
    for pre in main.find_all('pre'):
        code = pre.get_text()
        pre.clear()
        pre.append(re.sub(r'(?m)^[ \t]+', lambda m: _INDENT * len(m.group(0).expandtabs(4)), code))
    text = main.get_text()
    lines = [' '.join(line.split()).replace(_INDENT, ' ') for line in text.splitlines()]
    text = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
    if not title:
        h1 = soup.find('h1')
        title = ' '.join(h1.get_text(' ').split()) if h1 else ''
    return title, text, links


def page_from(url: str, kind: str, body: bytes) -> tuple[Page, list[str]]:
    """Turn a fetched response into a page and the links it holds."""
    from cognitrix.knowledge import extract

    path = urlparse(url).path.lower()
    if kind == 'application/pdf' or path.endswith('.pdf'):
        try:
            text = extract.to_text('page.pdf', body)
        except extract.ExtractError as exc:
            raise WebError(str(exc)) from exc
        return Page(url=url, title=path.rsplit('/', 1)[-1] or url, text=text), []
    if kind in ('text/html', 'application/xhtml+xml') or (not kind and b'<html' in body[:2000].lower()):
        title, text, links = _html_page(url, body)
        return Page(url=url, title=title or url, text=text), links
    if kind.startswith('text/') or kind in ('application/json', 'application/x-yaml', 'application/yaml'):
        return Page(url=url, title=path.rsplit('/', 1)[-1] or url, text=_decode(body).strip()), []
    raise WebError(f"That address isn't a web page or text ({kind or 'unknown type'})")


def _in_scope(start: str, candidate: str) -> bool:
    """Same host, and under the starting page's folder (so a docs section
    doesn't wander into the whole site)."""
    a, b = urlparse(start), urlparse(candidate)
    if b.scheme not in ('http', 'https') or (a.hostname or '').lower() != (b.hostname or '').lower():
        return False
    if a.port != b.port:
        return False
    folder = a.path if a.path.endswith('/') else a.path.rsplit('/', 1)[0] + '/'
    if not (b.path or '/').startswith(folder):
        return False
    return not (b.path or '').lower().endswith(_SKIP_SUFFIXES)


def _same_page(url: str) -> str:
    """Key used to avoid reading one page twice (trailing slash, query order aside)."""
    parsed = urlparse(url)
    path = parsed.path.rstrip('/') or '/'
    return f'{(parsed.hostname or "").lower()}:{parsed.port}{path}?{parsed.query}'


async def _robots(client, start: str) -> RobotFileParser | None:
    parsed = urlparse(start)
    robots_url = f'{parsed.scheme}://{parsed.netloc}/robots.txt'
    parser = RobotFileParser()
    try:
        _final, _kind, body = await _get(client, robots_url)
    except Exception:
        return None  # No robots.txt (or unreachable): nothing is disallowed.
    parser.parse(_decode(body).splitlines())
    return parser


def _client():
    import httpx

    return httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False,
                             headers={'User-Agent': USER_AGENT, 'Accept': 'text/html,application/xhtml+xml,text/plain,application/pdf;q=0.9,*/*;q=0.5'})


async def read_site(url: str, max_pages: int = 1, *, progress=None) -> SiteResult:
    """Read ``url`` and, when ``max_pages`` > 1, the pages it links to under
    the same folder of the same site, up to ``max_pages`` pages in all.

    Raises WebError when the first page can't be read. Later pages that fail
    are counted in ``skipped``."""
    url = normalize_url(url)
    max_pages = max(1, min(int(max_pages or 1), MAX_PAGES))
    result = SiteResult()
    async with _client() as client:
        robots = await _robots(client, url) if max_pages > 1 else None
        queue: deque[str] = deque([url])
        seen = {_same_page(url)}
        start = url
        first = True
        while queue and len(result.pages) < max_pages:
            current = queue.popleft()
            if not first:
                if robots is not None and not robots.can_fetch(USER_AGENT, current):
                    result.skipped += 1
                    continue
                await asyncio.sleep(CRAWL_DELAY)
            try:
                final, kind, body = await _get(client, current)
                page, links = page_from(final, kind, body)
            except Exception as exc:
                if first:
                    if isinstance(exc, WebError):
                        raise
                    logger.info('Could not read %s: %s', current, exc)
                    raise WebError("That page couldn't be reached") from exc
                result.skipped += 1
                continue
            if first:
                # A redirect (http to https, a moved page) sets the real starting point.
                start = final
                seen.add(_same_page(final))
                first = False
            if page.text.strip():
                result.pages.append(page)
                if progress is not None:
                    await progress(len(result.pages))
            for link in links:
                key = _same_page(link)
                if key not in seen and _in_scope(start, link):
                    seen.add(key)
                    queue.append(link)
    if not result.pages:
        raise WebError('No text was found on that page (it may build its content with JavaScript)')
    return result
