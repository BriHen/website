# brihen.com Goodreads reading dashboard

A static GitHub Pages dashboard for Goodreads user `191979846`.

## Automatic updates

The GitHub Actions workflow `.github/workflows/goodreads.yml` runs twice daily
(at 00:17 and 12:17 UTC; GitHub can delay scheduled jobs), on relevant pushes
to `main`, and manually from **Actions > Update Goodreads shelves > Run workflow**.
It does not depend on this computer, an open browser, or a running local app.

Each run:

1. Runs the sync regression tests.
2. Reads public shelf counts and the current Reading Challenge from the profile.
3. Tries public RSS, then falls back to Goodreads' public bookshelf widgets.
4. Validates each downloaded shelf against its current profile total. Widgets
   return at most 100 books per request; ascending and descending responses are
   combined by review ID to cover this library's 133-book read shelf.
5. Saves titles, authors, ratings, covers, links, timestamps, and source details
   to `books.json`, preserving cached books for any failed shelf.
6. Commits updated data and explicitly deploys `index.html`, `books.json`, and
   `CNAME` to GitHub Pages in the same run. An incomplete sync still publishes
   retained data, then fails the job so the problem remains visible.

The latest live check on November 14, 2026 returned **133 read**, **3 currently
reading**, **15 to read**, and **69/50** for the 2026 Reading Challenge. RSS returned
HTTP 404; the public widgets successfully supplied all three shelves. This was
verified from the development machine; a successful Actions run is needed to
confirm access from a GitHub-hosted runner.

## GitHub setup (once)

1. Commit and push the repository changes to `main`, preserving `CNAME`
   (`www.brihen.com`). Enable GitHub Actions if disabled.
2. In **Settings > Pages > Build and deployment > Source**, select **GitHub
   Actions**. Preserve the existing custom domain and HTTPS settings. The
   workflow publishes Pages directly instead of relying on a bot commit to
   trigger a separate Pages build.
3. Ensure repository or organization policies allow the workflow's
   `contents: write`, `pages: write`, and `id-token: write` permissions and
   the `github-pages` environment permits deployments from `main`.
4. Run **Update Goodreads shelves** from the Actions tab. Confirm the
   **Goodreads sync** job summary lists every shelf as Updated and the
   **Publish reading dashboard** step succeeds. The deployment URL appears
   on the job's environment.

No Goodreads password, API key, personal access token, or third-party proxy is
required by the workflow.

## Troubleshooting and limits

- Read **Actions > latest run > Summary** for per-shelf counts and failure
  reasons. `books.json` also records `sync_errors`, `sync_error_details`,
  `last_attempt`, and each shelf's last successful timestamp and source.
- HTTP 404 on RSS is expected while those endpoints are unavailable; the job
  succeeds when the validated widget fallback succeeds. Transient network,
  rate-limit, and server errors receive bounded retries.
- A widget returning the wrong shelf, duplicate items, HTML instead of data,
  or fewer books than the profile total never replaces that cached shelf.
  If the library changes during a run, the next scheduled run retries.
- Two widget responses can cover at most 200 books per shelf. Beyond that,
  completeness validation fails and retains the previous list. Another
  supported source, such as Goodreads' library CSV export, would be needed
  if RSS remains unavailable; CSV export is not an unattended sync.
- Goodreads widgets omit finish dates and added dates. Previously known dates
  are retained, and unknown dates display a dash. The yearly card uses the
  profile's actual Reading Challenge total, including rereads, rather than
  counting missing dates as zero. Without a challenge total, only an RSS
  source can supply a date-based yearly fallback.
- Goodreads can change or restrict public sources. No client can guarantee
  permanent access; failures retain data and mark the job unsuccessful.
  GitHub may disable schedules after prolonged inactivity in public repos;
  re-enable the workflow from Actions if that happens. The dashboard flags
  missing or overdue updates after 48 hours.

## Local checks

Requires Python 3.12 or later; no external dependencies.

```sh
python -m unittest discover -s tests -v
python scripts/sync.py
python -m http.server 8000
```

The sync script exits nonzero if any shelf, profile totals, or the current
Reading Challenge fails to update. Successful shelves are still saved. Serve
the site over HTTP when previewing so the browser can load `books.json`.
