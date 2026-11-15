import copy
import html
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from scripts import sync


def profile(counts=(3, 15, 133), challenge=True):
    shelves = ''.join(
        f'<a class="actionLinkLite userShowPageShelfListItem" href="/review/list/{sync.ID}?shelf={shelf}">{shelf}&lrm; ({count:,})</a>'
        for shelf, count in zip(sync.SHELVES, counts)
    )
    progress = '''<div id="hp_year_challenge"><span class="yearHeader">2026</span>
        <a class="challengeBooksRead">Brian Heneghan has read 69 books of their goal of 50!</a></div>''' if challenge else ''
    return f'<link rel="canonical" href="{sync.PROFILE}">{shelves}{progress}'


def widget(shelf, ids, title="Reader's \\ journey & discovery"):
    # Use the actual public widget structure and JS string escaping.
    entries = []
    for book_id in ids:
        entries.append(f'''<div class="gr_custom_each_container_test">
          <div class="gr_custom_book_container_test"><img src="https://i.gr-assets.com/images/S/compressed.photo.goodreads.com/books/123l/{book_id}._SY75_.jpg"></div>
          <div class="gr_custom_rating_test"><img src="https://s.gr-assets.com/images/layout/gr_flat_red_star_active.png"><img src="https://s.gr-assets.com/images/layout/gr_flat_red_star_inactive.png"></div>
          <div class="gr_custom_title_test"><a href="https://www.goodreads.com/review/show/{book_id}">{html.escape(title)}</a></div>
          <div class="gr_custom_author_test">by <a href="/author/show/1">An Author</a></div>
        </div>''')
    body = f'<div class="gr_custom_container_test"><h2 class="gr_custom_header_test"><a href="https://www.goodreads.com/review/list/{sync.ID}-brian-heneghan?shelf={shelf}&amp;utm_source=custom_widget">Books</a></h2>' + ''.join(entries) + '</div>'
    escaped = body.replace('\\', '\\\\').replace("'", "\\'").replace('"', '\\"').replace('/', '\\/').replace('\n', '\\n')
    return "var widget_code = '" + escaped + "'; document.write(widget_code);"


class PublicSourceTests(unittest.TestCase):
    def test_profile_counts_and_challenge(self):
        result = sync.parse_profile(profile((0, 15, 1133)), datetime(2026, 11, 14, tzinfo=timezone.utc))
        self.assertEqual(result['currently_reading'], 0)
        self.assertEqual(result['read'], 1133)
        self.assertEqual(result['reading_challenge_2026'], 69)
        self.assertEqual(result['reading_goal_2026'], 50)

    def test_profile_rejects_missing_totals_and_wrong_identity(self):
        for content in (profile().replace(sync.ID, '999'), '<html>Sign in</html>', profile().replace('userShowPageShelfListItem', 'other')):
            with self.subTest(content=content[:50]), self.assertRaises(ValueError):
                sync.parse_profile(content, datetime.now(timezone.utc))

    def test_widget_books_entities_escapes_and_ratings(self):
        result = sync.parse_widget(widget('read', [42]), 'read')
        self.assertEqual(result[0]['id'], '42')
        self.assertEqual(result[0]['title'], "Reader's \\ journey & discovery")
        self.assertEqual(result[0]['author'], 'An Author')
        self.assertEqual(result[0]['rating'], 1)
        self.assertEqual(result[0]['date_read'], '')

    def test_widget_rejects_wrong_shelf_user_and_invalid_responses(self):
        cases = [widget('to-read', [42]), widget('read', [42]).replace(sync.ID, '999'),
                 '<html>Sign in</html>', widget('read', [42]).replace(r'\/show\/', r'\/missing\/')]
        for content in cases:
            with self.subTest(content=content[:50]), self.assertRaises(ValueError):
                sync.parse_widget(content, 'read')

    @patch.object(sync.time, 'sleep')
    def test_two_widget_orders_produce_complete_shelf(self, sleep):
        with patch.object(sync, 'fetch', side_effect=[widget('read', range(34, 134)), widget('read', range(1, 101))]) as fetch:
            books = sync.load_widget_shelf('read', 133)
        self.assertEqual(len(books), 133)
        self.assertEqual(len({book['id'] for book in books}), 133)
        urls = [call.args[0] for call in fetch.call_args_list]
        self.assertNotEqual(urlsplit(urls[0]).path, urlsplit(urls[1]).path)
        self.assertEqual([parse_qs(urlsplit(url).query)['order'][0] for url in urls], ['d', 'a'])

    @patch.object(sync.time, 'sleep')
    def test_truncated_and_duplicate_widgets_are_rejected(self, sleep):
        for responses in ([widget('read', range(1, 101))] * 2, [widget('read', [1, 1])]):
            with patch.object(sync, 'fetch', side_effect=responses), self.assertRaises(ValueError):
                sync.load_widget_shelf('read', 133)

    def test_explicit_empty_widget_is_valid(self):
        with patch.object(sync, 'fetch', return_value=widget('read', [])):
            self.assertEqual(sync.load_widget_shelf('read', 0), [])

    def test_html_cannot_be_mistaken_for_empty_rss(self):
        with patch.object(sync, 'fetch', return_value='<html><channel/></html>'), self.assertRaises(ValueError):
            sync.load_shelf('read')

    def test_rss_fields_and_empty_channel(self):
        feed = '''<rss><channel><item><book_id>42</book_id><title>A book</title>
          <author_name>An Author</author_name><user_rating>5</user_rating>
          <user_read_at>Sat, 14 Nov 2026 10:00:00 +0000</user_read_at></item></channel></rss>'''
        with patch.object(sync, 'fetch', return_value=feed):
            book = sync.load_shelf('read')[0]
        self.assertEqual(book['date_read'], '2026-11-14')
        self.assertEqual(book['rating'], 5)
        with patch.object(sync, 'fetch', return_value='<rss><channel/></rss>'):
            self.assertEqual(sync.load_shelf('read'), [])

    @patch.object(sync.time, 'sleep')
    def test_repeated_rss_pages_are_rejected(self, sleep):
        feed = '<rss><channel>' + ''.join(f'<item><book_id>{i}</book_id></item>' for i in range(100)) + '</channel></rss>'
        with patch.object(sync, 'fetch', return_value=feed), self.assertRaises(ValueError):
            sync.load_shelf('read')

    @patch.object(sync.time, 'sleep')
    def test_fetch_retries_transient_errors_but_not_404(self, sleep):
        for status, attempts in ((503, 3), (404, 1)):
            error = HTTPError('https://www.goodreads.com/', status, 'Error', {}, None)
            with patch.object(sync, 'urlopen', side_effect=error) as request, self.assertRaises(HTTPError):
                sync.fetch('https://www.goodreads.com/')
            self.assertEqual(request.call_count, attempts)
            error.close()


class SyncIntegrationTests(unittest.TestCase):
    def run_sync(self, existing, fetch):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'books.json'
            summary = Path(directory) / 'summary.md'
            path.write_text(json.dumps(existing), encoding='utf-8')
            with patch.object(sync, 'DATA', path), patch.object(sync, 'fetch', side_effect=fetch), patch.object(sync.time, 'sleep'), patch.dict(sync.os.environ, {'GITHUB_STEP_SUMMARY': str(summary)}), patch.object(sync, 'datetime') as clock, redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                clock.now.return_value = datetime(2026, 11, 14, tzinfo=timezone.utc)
                code = sync.main()
            return code, json.loads(path.read_text(encoding='utf-8')), summary.read_text(encoding='utf-8')

    def sources(self, url):
        if '/user/show/' in url:
            return profile((1, 0, 1))
        if '/list_rss/' in url:
            raise HTTPError(url, 404, 'Not found', {}, None)
        shelf = parse_qs(urlsplit(url).query)['shelf'][0]
        return widget(shelf, [] if shelf == 'to-read' else [42])

    def test_successful_widget_fallback_updates_all_shelves_and_summary(self):
        code, result, summary = self.run_sync({}, self.sources)
        self.assertEqual(code, 0)
        self.assertEqual(result['sync_errors'], [])
        self.assertEqual([len(result['shelves'][s]) for s in sync.SHELVES], [1, 0, 1])
        self.assertEqual(result['profile_snapshot']['reading_challenge_2026'], 69)
        self.assertEqual(len(result['shelf_updated']), 3)
        self.assertIn('| read | 1 | Updated |', summary)

    def test_failure_preserves_books_dates_and_snapshot_and_exits_nonzero(self):
        existing = {'shelves': {'read': [{'id': '42', 'title': 'Cached', 'date_read': '2026-01-02'}]},
                    'shelf_updated': {'read': '2026-01-03T00:00:00+00:00'},
                    'profile_snapshot': {'date': '2026-01-03', 'read': 1}}
        original = copy.deepcopy(existing)
        def fail(url):
            raise TimeoutError('Source unavailable')
        code, result, summary = self.run_sync(existing, fail)
        self.assertEqual(code, 1)
        for key in original:
            self.assertEqual(result[key], original[key])
        self.assertEqual(set(result['sync_errors']), set(sync.SHELVES))
        self.assertIn('Failed; cache retained', summary)

    def test_partial_failure_retains_failed_shelf_and_still_saves_other_updates(self):
        existing = {'shelves': {'read': [{'id': '99', 'title': 'Cached'}]}, 'shelf_updated': {'read': '2026-01-03'}}
        def partial(url):
            if '/custom_widget/' in url and parse_qs(urlsplit(url).query)['shelf'] == ['read']:
                return widget('read', [])
            return self.sources(url)
        code, result, summary = self.run_sync(existing, partial)
        self.assertEqual(code, 1)
        self.assertEqual(result['sync_errors'], ['read'])
        self.assertEqual(result['shelves']['read'], existing['shelves']['read'])
        self.assertEqual(result['shelf_updated']['read'], '2026-01-03')
        self.assertEqual(len(result['shelves']['currently-reading']), 1)

    def test_widget_refresh_preserves_previously_known_finish_date(self):
        existing = {'shelves': {'read': [{'id': '42', 'date_read': '2026-01-02', 'date_added': '2025-10-01'}]}}
        code, result, summary = self.run_sync(existing, self.sources)
        self.assertEqual(code, 0)
        self.assertEqual(result['shelves']['read'][0]['date_read'], '2026-01-02')
        self.assertEqual(result['shelves']['read'][0]['date_added'], '2025-10-01')

    def test_unreadable_challenge_retains_previous_total_with_original_date(self):
        existing = {'profile_snapshot': {'date': '2026-01-03', 'reading_challenge_2026': 10, 'reading_goal_2026': 50}}
        def missing_challenge(url):
            return profile((1, 0, 1), challenge=False) if '/user/show/' in url else self.sources(url)
        code, result, summary = self.run_sync(existing, missing_challenge)
        self.assertEqual(code, 1)
        self.assertEqual(result['profile_snapshot']['read'], 1)
        self.assertEqual(result['profile_snapshot']['reading_challenge_2026'], 10)
        self.assertEqual(result['profile_snapshot']['challenge_date'], '2026-01-03')
        self.assertIn('reading_challenge', result['sync_error_details'])
        self.assertIn('no readable 2026 Reading Challenge total', summary)


if __name__ == '__main__':
    unittest.main()
