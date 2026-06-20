# SSC Answer Key Checker

Paste any one Part (A/B/C/D) answer-key URL → it auto-derives all 4 part URLs,
loads each in headless Chromium, reads the `bgcolor` on each option cell, and
scores the attempt.

## Scoring logic (matches the reference script)
For each question's 4 options, look at `bgcolor`:
- **green** present → user selected correctly → counts as **Correct**
- **red + yellow** both present → user selected red (wrong), yellow is the
  real correct answer → counts as **Wrong**
- **yellow only** (no red) → user selected nothing → **Skipped** (not counted
  in attempted)
- none of the above → **Unknown** (shouldn't normally happen)

This is purely the scoring path — no PDF is generated, so it's much lighter
than the original PDF-generator script (no `page.pdf()`, no image-load waits,
no PDF merging). That keeps Railway CPU/RAM time per request low.

---

## Run locally on your PC (same as a normal terminal script)

```bash
# 1. Clone / unzip this project, then cd into it
cd sscapp

# 2. Create a virtual environment (optional but recommended)
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Install Chromium for Playwright (one-time, ~150MB)
playwright install chromium
playwright install-deps chromium   # Linux only, installs OS libs

# 5. Run the server
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 6. Open in browser
http://localhost:8000
```

Paste your answer-key URL into the page and click "Check Score".

---

## Deploy to Railway (low-cost)

1. Push this folder to a GitHub repo (or use `railway up` directly from CLI).
2. On Railway: **New Project → Deploy from GitHub repo** (or `railway init`
   then `railway up` from this folder).
3. Railway detects the `Dockerfile` automatically (it also reads
   `railway.json` for the health check path `/health`).
4. No environment variables are required. Railway injects `$PORT`
   automatically; the Dockerfile's `CMD` already uses it.
5. Once deployed, open the generated Railway URL — same form/page as local.

### Why this stays cheap on Railway
- Uses the official `mcr.microsoft.com/playwright/python` base image, so the
  build does **not** spend time/credits installing Chromium + OS deps from
  scratch — they're already baked in.
- No PDF rendering, no `time.sleep()` waits for image loading — each request
  just opens the page, reads the table HTML via one JS `evaluate()` call,
  and closes the browser context immediately. Typical request finishes in a
  few seconds of CPU time.
- Browser is launched fresh per `/api/check` call and fully closed afterward
  (`await browser.close()`), so no idle browser process sits around consuming
  RAM between requests — important on Railway's metered usage-based billing.
- Consider Railway's **Hobby plan sleep/idle** settings so the service scales
  to zero when nobody's using it, instead of always running.

---

## Project structure
```
sscapp/
├── Dockerfile          # Playwright-prebuilt image, minimal layers
├── railway.json        # Railway build/deploy config
├── requirements.txt    # FastAPI, Uvicorn, Playwright
├── .gitignore
├── README.md
└── app/
    └── main.py          # FastAPI app: form UI + scoring logic + Playwright scraping
```

## API
`POST /api/check` with JSON body `{"url": "<any part URL>"}` returns:
```json
{
  "parts": [
    {"name": "Part A", "url": "...", "total_questions": 25, "correct": 20, "wrong": 3, "skipped": 2, "unknown": 0, "attempted": 23},
    ...
  ],
  "summary": {"total_questions": 100, "attempted": 92, "correct": 78, "wrong": 14, "skipped": 8}
}
```
