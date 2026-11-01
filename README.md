# Desi Choice - Amazon India deals (site + feed)

Free, public, GitHub Actions + GitHub Pages. `index.html` is the page (Top offers, Trending, Daily essentials, 17 category tabs); the same JSON files feed the bots. No guides, only deals.
Repo name `desichoice.github.io` under the account `desichoice` = site at https://desichoice.github.io (Settings -> Pages -> Deploy from branch `main` / root).

| File | What |
|---|---|
| `guides/hot.json` | `ess` (Daily Essentials), `hot` (Top Offers ticker), `trend` (seasonal: Diwali, Winter, Wedding ...), `themes` |
| `guides/offers.json` | `cats` = Top Offers per category (17 categories, up to 40 each) |

Item: `asin, name, pct, rating, t, img, link, now, was, deal, cat, label, icon, reviews, tier` (rupees as plain numbers).

## Setup (once)
1. Create a **public** repo, upload these files (keep the folder structure, incl. `.github/workflows/scan.yml`).
2. Settings -> Actions -> General -> Workflow permissions = **Read and write**.
3. Optional: Settings -> Secrets and variables -> Actions -> **Variables** -> `AMAZON_IN_TAG` = your associate id (e.g. `yourid-21`).
4. Actions -> India Deal Scan -> Run workflow -> mode **selftest**. Read the log: every department should show cards > 0.
   A line with 0 cards = wrong Amazon alias -> fix `SCOPES` in `tools/scan_in.py` (the sweep already falls back to a keyword search).
5. Run mode **feed**, then mode **offers**. After that the 8 daily cron runs keep everything fresh (each run only does what is due).

## Use in Xbot
`DESI_FEED_URL = https://raw.githubusercontent.com/<user>/<repo>/main/guides/hot.json`

## Notes
- Festival windows (Diwali, Holi, Rakhi ...) move each year: edit `THEMES` in `tools/scan_in.py` once a year.
- If Amazon shows a captcha twice in a row the run stops cleanly and keeps the old feed (never writes an empty one).
- Local test of the logic (no browser): `python -m unittest discover -s tests -v`
