# brihen.com Goodreads reading dashboard

A static GitHub Pages dashboard for Goodreads user `191979846`.

## Install
1. Upload `index.html`, `books.json`, `scripts/sync.py`, and `.github/workflows/goodreads.yml` to the root of your existing GitHub Pages repo. Preserve folder paths. **Do not replace your existing `CNAME` file** (`www.brihen.com`).
2. Commit to `main`. Under **Settings → Pages**, keep deployment set to `main / (root)`.
3. In GitHub, open **Actions → Update Goodreads shelves → Run workflow**. (You may need to enable Actions and give `Read and write permissions` under **Settings → Actions → General → Workflow permissions**, if pushes are rejected.)
4. The workflow tries Goodreads' public shelf RSS feeds twice a day and commits updated `books.json`. Check the workflow logs for blocking/HTTP errors.

## Limitations
- As of November 2026 the Goodreads bookshelf API is retired and the RSS endpoints may be unavailable or change. The GitHub-hosted runner's ability to fetch your shelves **has not been verified**. The script deliberately doesn't use login credentials, proxies, CAPTCHA avoidance, or bypass techniques.
- A public profile shows a **2026 reading challenge snapshot of 68/50 as of Nov 7, 2026**. This is stored as a dated snapshot, not an automatically updating count. Once a full RSS `read` shelf successfully downloads, the dashboard instead counts books with Goodreads finish dates in 2026; Goodreads may exclude books without finish dates, so this can differ from its challenge count.
- Counts for shelves without successful RSS are dated profile snapshots. If feeds fail, **book tables remain empty until a successful sync**. Previously synchronized data will not be cleared by a failed fetch.
- Respect Goodreads terms and avoid aggressive automated polling. If unattended RSS fetching is blocked, use Goodreads' own export as a one-time fallback rather than assuming automatic sync works.
