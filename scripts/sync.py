"""Best-effort Goodreads PUBLIC RSS sync. No login, no password, no third-party proxy.
Data from a failed shelf is retained; Goodreads can change or disable RSS anytime.
"""
import json, os, re, sys, time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'books.json'
ID = '191979846'
SHELVES = ('currently-reading', 'to-read', 'read')


def fetch(url):
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; personal-reading-dashboard/1.0)', 'Accept': 'application/rss+xml, application/xml, text/xml;q=0.9'})
    with urlopen(req, timeout=25) as r:
        payload = r.read(8_000_000)
        if b'<rss' not in payload[:2000] and b'<?xml' not in payload[:200]:
            raise ValueError('Goodreads returned HTML or a non-RSS response')
        return ET.fromstring(payload)


def field(item, name):
    el = item.find(name)
    return (el.text or '').strip() if el is not None else ''


def iso_date(value):
    if not value: return ''
    try: return parsedate_to_datetime(value).date().isoformat()
    except (ValueError, TypeError, IndexError): return ''


def load_shelf(shelf):
    books, seen = [], set()
    for page in range(1, 51):
        url = 'https://www.goodreads.com/review/list_rss/' + ID + '?' + urlencode({'shelf': shelf, 'per_page': 100, 'page': page})
        feed = fetch(url)
        batch = feed.findall('./channel/item')
        if not batch: break
        additions = 0
        for item in batch:
            book_id = field(item, 'book_id')
            link = field(item, 'link')
            key = book_id or link
            if not key or key in seen: continue
            seen.add(key)
            additions += 1
            books.append({
                'id': book_id,
                'title': field(item, 'title'),
                'author': field(item, 'author_name'),
                'rating': int(field(item, 'user_rating') or 0),
                'date_read': iso_date(field(item, 'user_read_at')),
                'date_added': iso_date(field(item, 'user_date_added')),
                'cover': field(item, 'book_medium_image_url') or field(item, 'book_image_url'),
                'url': ('https://www.goodreads.com/book/show/' + book_id) if book_id else link,
            })
        if len(batch) < 100 or additions == 0: break
        time.sleep(1.5)
    else:
        raise RuntimeError('Pagination limit reached; declining to overwrite potentially incomplete shelf')
    return books


def main():
    existing = json.loads(DATA.read_text(encoding='utf-8')) if DATA.exists() else {}
    result = dict(existing)
    result.setdefault('shelves', {})
    result.setdefault('shelf_updated', {})
    updated, failures = [], []
    for shelf in SHELVES:
        try:
            books = load_shelf(shelf)
            # Empty shelves are valid only when explicitly confirmed by a valid RSS response.
            result['shelves'][shelf] = books
            result['shelf_updated'][shelf] = datetime.now(timezone.utc).isoformat()
            updated.append(shelf)
            print(f'{shelf}: {len(books)} books')
        except Exception as exc:
            failures.append(shelf)
            print(f'WARNING {shelf}: {exc}; retaining cached books', file=sys.stderr)
    result['source'] = 'Goodreads public shelf RSS'
    result['profile_url'] = 'https://www.goodreads.com/user/show/191979846-brian-heneghan'
    result['sync_errors'] = failures
    result['last_attempt'] = datetime.now(timezone.utc).isoformat()
    # The reading challenge total is available on the public Goodreads profile,
    # but don't claim to have pulled it unless an actual extraction took place.
    DATA.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    if failures:
        print('Some shelves could not sync; see warnings above.', file=sys.stderr)
    if not updated:
        sys.exit(1)

if __name__ == '__main__': main()
