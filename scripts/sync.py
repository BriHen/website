"""Sync public Goodreads RSS, with validated public bookshelf widgets as fallback.

No login credentials or third-party proxies. Failed shelves retain their cache.
Widget responses do not supply finish dates.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'books.json'
ID = '191979846'
PROFILE = f'https://www.goodreads.com/user/show/{ID}-brian-heneghan'
SHELVES = ('currently-reading', 'to-read', 'read')
COUNT_KEYS = {'currently-reading': 'currently_reading', 'to-read': 'to_read', 'read': 'read'}
MAX_BYTES = 8_000_000


def fetch(url):
    request = Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; personal-reading-dashboard/2.0)',
        'Accept': 'application/rss+xml, application/xml, text/html, */*;q=0.8',
    })
    for attempt in range(3):
        try:
            with urlopen(request, timeout=25) as response:
                if '/user/sign_in' in response.url:
                    raise ValueError('Goodreads requires sign-in for this source')
                payload = response.read(MAX_BYTES + 1)
                if len(payload) > MAX_BYTES:
                    raise ValueError('Response exceeds size limit; refusing truncated data')
                return payload.decode('utf-8')
        except HTTPError as exc:
            exc.close()
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** (attempt + 1))


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def text(self):
        return ''.join(child.text() if isinstance(child, Node) else child for child in self.children)

    def has_class(self, name):
        return name in self.attrs.get('class', '').split()


class Document(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, text):
        self.stack[-1].children.append(text)


def parse_profile(html, now):
    nodes = list(Document(html).root.walk())
    canonical = [n.attrs.get('href', '') for n in nodes if n.tag == 'link' and n.attrs.get('rel') == 'canonical']
    if not any(re.search(rf'/user/show/{ID}(?:-|$)', url) for url in canonical):
        raise ValueError('Profile response does not identify the expected Goodreads user')
    snapshot = {'date': now.date().isoformat(), 'updated_at': now.isoformat()}
    for node in nodes:
        if node.tag == 'a' and node.has_class('userShowPageShelfListItem'):
            shelf = parse_qs(urlsplit(node.attrs.get('href', '')).query).get('shelf', [''])[0]
            count = re.search(r'\(([\d,]+)\)', node.text())
            if shelf in COUNT_KEYS and count:
                snapshot[COUNT_KEYS[shelf]] = int(count[1].replace(',', ''))
    if not all(key in snapshot for key in COUNT_KEYS.values()):
        raise ValueError('Public profile is missing expected shelf totals')
    challenge = next((n for n in nodes if n.attrs.get('id') == 'hp_year_challenge'), None)
    if challenge:
        year = next((n.text().strip() for n in challenge.walk() if n.has_class('yearHeader')), '')
        progress = next((n.text() for n in challenge.walk() if n.has_class('challengeBooksRead')), '')
        match = re.search(r'has read ([\d,]+) books? of their goal of ([\d,]+)', progress)
        if re.fullmatch(r'\d{4}', year) and match:
            snapshot['reading_challenge_' + year] = int(match[1].replace(',', ''))
            snapshot['reading_goal_' + year] = int(match[2].replace(',', ''))
            snapshot['challenge_date'] = now.date().isoformat()
            snapshot['challenge_updated_at'] = now.isoformat()
    return snapshot


def field(item, name):
    return (item.findtext(name) or '').strip()


def iso_date(value):
    try:
        return parsedate_to_datetime(value).date().isoformat() if value else ''
    except (ValueError, TypeError, IndexError):
        return ''


def load_shelf(shelf):
    books, seen = [], set()
    for page in range(1, 51):
        url = f'https://www.goodreads.com/review/list_rss/{ID}?' + urlencode({'shelf': shelf, 'per_page': 100, 'page': page})
        feed = ET.fromstring(fetch(url))
        if feed.tag != 'rss' or feed.find('channel') is None:
            raise ValueError('Goodreads returned an invalid RSS feed')
        batch = feed.findall('./channel/item')
        if not batch:
            return books
        for item in batch:
            book_id, link = field(item, 'book_id'), field(item, 'link')
            key = book_id or link
            if not key or key in seen:
                raise ValueError('RSS pagination repeated a book or returned an invalid item')
            seen.add(key)
            books.append({
                'id': book_id, 'title': field(item, 'title'),
                'author': field(item, 'author_name'), 'rating': int(field(item, 'user_rating') or 0),
                'date_read': iso_date(field(item, 'user_read_at')),
                'date_added': iso_date(field(item, 'user_date_added')),
                'cover': field(item, 'book_medium_image_url') or field(item, 'book_image_url'),
                'url': f'https://www.goodreads.com/book/show/{book_id}' if book_id else link,
            })
        if len(batch) < 100:
            return books
        time.sleep(1.5)
    raise RuntimeError('RSS pagination limit reached; refusing incomplete data')


def parse_widget(script, shelf):
    # Decode only the widget string literal. Never execute remote JavaScript.
    match = re.search(r"\bvar\s+widget_code\s*=\s*'((?:\\.|[^'\\])*)'", script)
    if not match:
        raise ValueError('Goodreads returned no bookshelf widget')
    escapes = {'n': '\n', 'r': '\r', 't': '\t', 'b': '\b', 'f': '\f', '/': '/', "'": "'", '"': '"', '\\': '\\'}

    def unescape(match):
        value = match[1]
        if value.startswith(('u', 'x')) and len(value) > 1:
            return chr(int(value[1:], 16))
        if value not in escapes:
            raise ValueError('Unexpected widget string escape')
        return escapes[value]

    html = re.sub(r'\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)', unescape, match[1])
    nodes = list(Document(html).root.walk())
    headers = [n for n in nodes if n.tag == 'h2' and n.attrs.get('class', '').startswith('gr_custom_header_')]
    expected = any(parse_qs(urlsplit(n.attrs.get('href', '')).query).get('shelf') == [shelf]
                   and re.search(rf'/review/list/{ID}(?:-|$)', urlsplit(n.attrs.get('href', '')).path)
                   for header in headers for n in header.walk() if n.tag == 'a')
    if not expected:
        raise ValueError('Widget belongs to a different user or shelf')
    books = []
    for node in nodes:
        if not node.attrs.get('class', '').startswith('gr_custom_each_container_'):
            continue
        parts = list(node.walk())
        title_node = next((n for n in parts if n.attrs.get('class', '').startswith('gr_custom_title_')), None)
        author_node = next((n for n in parts if n.attrs.get('class', '').startswith('gr_custom_author_')), None)
        link = next((n for n in title_node.walk() if n.tag == 'a'), None) if title_node else None
        review = re.search(r'/review/show/(\d+)', link.attrs.get('href', '')) if link else None
        if not review or not link.text().strip():
            raise ValueError('Widget contains an incomplete book entry')
        image = next((n for n in parts if n.tag == 'img' and '/books/' in n.attrs.get('src', '')), None)
        cover = image.attrs['src'] if image else ''
        book_id = re.search(r'/books/[^/]+/(\d+)', cover)
        author = next((n.text().strip() for n in author_node.walk() if n.tag == 'a'), '') if author_node else ''
        books.append({
            'id': book_id[1] if book_id else 'review:' + review[1],
            'review_id': review[1], 'title': link.text().strip(), 'author': author,
            'rating': sum(n.tag == 'img' and n.attrs.get('src', '').endswith('/gr_flat_red_star_active.png') for n in parts),
            'date_read': '', 'date_added': '', 'cover': cover,
            'url': f'https://www.goodreads.com/book/show/{book_id[1]}' if book_id else f'https://www.goodreads.com/review/show/{review[1]}',
        })
    return books


def validate_count(books, expected):
    if len(books) != expected:
        raise ValueError(f'Incomplete shelf: downloaded {len(books)} books; public profile reports {expected}')


def load_widget_shelf(shelf, expected):
    # Widgets cap each response at 100 and do not reliably paginate. Opposite
    # sort orders cover up to 200 books; profile totals detect missing books.
    books = {}
    for order in ('d', 'a'):
        label = f'Brian books: {shelf} {order}'
        url = f'https://www.goodreads.com/review/custom_widget/{ID}.' + quote(label) + '?' + urlencode({
            'shelf': shelf, 'num_books': 100, 'sort': 'date_added', 'order': order,
            'show_author': 1, 'show_rating': 1, 'show_title': 1, 'show_cover': 1,
            'show_review': 0, 'widget_id': f'{shelf}_{order}',
        })
        batch = parse_widget(fetch(url), shelf)
        if len({book['review_id'] for book in batch}) != len(batch):
            raise ValueError('Widget contains duplicate book entries')
        books.update((book['review_id'], book) for book in batch)
        if len(books) >= expected:
            break
        time.sleep(1.5)
    result = list(books.values())
    validate_count(result, expected)
    return result


def main():
    existing = json.loads(DATA.read_text(encoding='utf-8')) if DATA.exists() else {}
    result = dict(existing)
    for key in ('shelves', 'shelf_updated', 'shelf_sources'):
        result.setdefault(key, {})
    result['sync_error_details'] = {}
    now = datetime.now(timezone.utc)
    snapshot, failures = None, []
    try:
        snapshot = parse_profile(fetch(PROFILE), now)
        result['profile_snapshot'] = snapshot
        print('Public profile totals refreshed')
    except Exception as exc:
        result['sync_error_details']['profile'] = str(exc)
        print(f'WARNING profile: {exc}; retaining dated snapshot', file=sys.stderr)
    year = str(now.year)
    if snapshot and 'reading_challenge_' + year not in snapshot:
        result['sync_error_details']['reading_challenge'] = f'Public profile has no readable {year} Reading Challenge total'
        previous = existing.get('profile_snapshot', {})
        for key in ('reading_challenge_' + year, 'reading_goal_' + year):
            if key in previous:
                snapshot[key] = previous[key]
        if 'reading_challenge_' + year in previous:
            snapshot['challenge_date'] = previous.get('challenge_date', previous.get('date'))
            snapshot['challenge_updated_at'] = previous.get('challenge_updated_at', previous.get('updated_at'))
    for shelf in SHELVES:
        expected = snapshot[COUNT_KEYS[shelf]] if snapshot else None
        try:
            try:
                books = load_shelf(shelf)
                if expected is not None:
                    validate_count(books, expected)
                source = 'Goodreads public shelf RSS'
            except Exception as rss_error:
                print(f'{shelf}: RSS unavailable ({rss_error}); trying public widget')
                if expected is None:
                    raise ValueError('Cannot validate widget completeness without current profile totals') from rss_error
                books = load_widget_shelf(shelf, expected)
                source = 'Goodreads public bookshelf widget'
                cached = {book['id']: book for book in existing.get('shelves', {}).get(shelf, [])}
                for book in books:
                    for key in ('date_read', 'date_added'):
                        book[key] = cached.get(book['id'], {}).get(key, '')
            result['shelves'][shelf] = books
            result['shelf_updated'][shelf] = now.isoformat()
            result['shelf_sources'][shelf] = source
            print(f'{shelf}: {len(books)} books ({source})')
        except Exception as exc:
            failures.append(shelf)
            result['sync_error_details'][shelf] = str(exc)
            print(f'ERROR {shelf}: {exc}; retaining cached books', file=sys.stderr)
    result.update(source='Goodreads public profile and bookshelves', profile_url=PROFILE,
                  sync_errors=failures, last_attempt=now.isoformat())
    temporary = DATA.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(DATA)
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        lines = ['## Goodreads sync', '', '| Shelf | Cached books | Latest attempt |', '| --- | ---: | --- |']
        for shelf in SHELVES:
            lines.append(f"| {shelf} | {len(result['shelves'].get(shelf, []))} | {'Failed; cache retained' if shelf in failures else 'Updated'} |")
        lines.extend(['', 'Profile counts: ' + ('updated' if snapshot else 'failed; dated snapshot retained')])
        for key, error in result['sync_error_details'].items():
            lines.append(f'- {key}: {error.replace(chr(10), " ")}')
        with open(summary, 'a', encoding='utf-8') as output:
            output.write('\n'.join(lines) + '\n')
    if failures or not snapshot or 'reading_challenge' in result['sync_error_details']:
        print('Sync incomplete; inspect the job summary. Existing data was retained for failed sources.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
