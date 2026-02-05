# Copilot Instructions — Autopiter parser repo ✅

Purpose
- Short: this repo scrapes and normalizes product offers from autopiter.by using a mix of API calls + Selenium-driven pages.
- Key entry points: `pars2.py` (practical runner), `pars_gpt.py` (most complete production-ready script), `pars3.py` (API login + cookie transfer), `pars_deep.py` (advanced/experimental).

Quick-start (developer workflow) ⚡
1. Put article list (one per line) in `art.txt` (first column used if CSV-like).
2. Adjust credentials in `config.ini` or environment variables (see `pars_deep.py` for env fallback).
3. Run the preferred script:
   - Lightweight: `python pars2.py` (reads `art.txt`, saves `autopiter_results_grouped.xlsx`)
   - API-first: `python pars3.py` (uses API login + cookies → Selenium)
   - Full/robust: `python pars_gpt.py` (webdriver-manager auto-download, logging to `autopiter_parser.log`)
4. Inspect outputs: `autopiter_verified.xlsx`, `autopiter_results_grouped.xlsx`, `not_found.txt`, and logs like `autopiter_parser.log` or `parser.log`.

Important patterns & conventions 🔧
- Preferred authorization pattern: API login (requests.Session) + transfer cookies into Selenium. See `pars3.py`, `pars_gpt.py`, and `pars_deep.py`.
- Primary data extraction: look for `window.__INITIAL_STATE__` JSON and extract product/offers objects. Fallback: DOM parsing with BeautifulSoup if JSON unavailable. See `extract_initial_state`, `find_product_objects_in_json` (`pars2.py`/`pars_gpt.py`).
- Price extraction: scripts normalize numbers (comma→dot) and use heuristic regex (see `extract_price_from_text` / `parse_price_numeric`).
- Anti-bot: scripts detect captcha/block pages and prompt for manual intervention (search for `detect_block_or_captcha` / "вы очень активный"). When debugging, run with headless=False.
- Input/output files: `art.txt` (input), `products.csv/json`, `autopiter_results_grouped.xlsx`, `autopiter_verified.xlsx` (outputs). Keep filenames and columns consistent when adding features.

Project-specific conventions ⚠️
- Multiple parsing strategies coexist — do not remove older DOM heuristics when adding JSON-based extraction; prefer to add tests/samples first.
- Scripts try many selector variants (lists of selectors) — follow that pattern when adding new selectors for site changes.
- Use existing debug artifacts (`debug_*.html`, `page_sample.html`) for unit-like testing of parsers.

Dependencies & environment 💡
- Common libs: `selenium`, `requests`, `bs4` (BeautifulSoup), `openpyxl`, `webdriver_manager`, `tqdm`.
- Windows notes: `pars_gpt.py` defaults to D:\Dev paths and webdriver-manager works on Windows; if using system chromedriver, ensure Chrome/driver versions match.

Testing & debugging tips 🐞
- Reproduce layout changes using `debug_*.html` files. Load them in a small test harness calling `parse_search_page` / `parse_offers_from_dom`.
- For interactive debugging, set headless=False and use breakpoints/`input()` prompts used by existing scripts.
- If prices are missing from `__INITIAL_STATE__`, attempt to execute JS via Selenium to obtain `appraiseCatalogs.prices` (see `pars_gpt.py`).

When editing code (AI / human rules) 📋
- Preserve: existing extraction fallbacks and selector lists; keep user-visible IO stable (filenames and spreadsheet columns).
- Add: new sample `debug_*` HTML and a small test snippet that runs parser functions against it (place in repo root). This repository has no test framework; add focused scripts not broad unit test infra.
- Logging: prefer using the project's logger patterns (`logger` in `pars_gpt.py` / `setup_logging` in `pars_deep.py`) for consistency.

Where to look first (files & functions) 🔍
- `pars_gpt.py` — full-featured, production-like flow and examples of error handling.
- `pars2.py` — pragmatic runner; `parse_search_page`, `extract_initial_state`, `find_product_objects_in_json`.
- `pars3.py` — API login and cookie transfer example (`login_and_get_cookies`, `attach_cookies_to_selenium`).
- `pars_deep.py` — advanced config, concurrency, and richer data models.
- `config.ini` — simple credential/setting source for quick runs.
- Debugging artifacts: `debug_*.html`, `page_sample.html`, `autopiter_parser.log`, `parser.log`.

If anything below is unclear or you want me to include a short runnable test scaffold or CI job, tell me which target scripts or parser functions to cover and I'll update the instructions. ✨
