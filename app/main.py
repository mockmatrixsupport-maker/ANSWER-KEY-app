#!/usr/bin/env python3
"""
SSC Answer Key Checker - Web Service
Paste an answer-key URL (any Part A/B/C/D), it fetches all 4 parts,
parses option bgcolor (green/yellow = correct, red = wrong selection,
yellow-alone = skipped), and returns a score summary as JSON / HTML.

Logic ported 1:1 from the reference desktop script (anskey.py):
- green OR yellow bgcolor on an option => that option is the correct answer
- if a question's ONLY colored cell is yellow (no red) => user did NOT
  select anything for that question => SKIPPED, excluded from attempted count
- if there's a RED cell AND a YELLOW cell in the same question => user
  selected the red option (wrong), correct answer is the yellow one
- if GREEN present => user selected correctly (green is both "selected"
  and "correct" in this scheme)
"""

import re
import asyncio
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

app = FastAPI(title="SSC Answer Key Checker")

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ── Part URL derivation (same as anskey.py) ─────────────────────────
PART_PAGES = [
    ("ViewCandResponse.aspx", "enckey", "Part A"),
    ("ViewCandResponse2.aspx", "EncKey", "Part B"),
    ("ViewCandResponse3.aspx", "EncKey", "Part C"),
    ("ViewCandResponse4.aspx", "EncKey", "Part D"),
]


def derive_all_part_urls(any_url: str):
    parsed = urlparse(any_url)
    path = parsed.path

    enc_key = None
    matched = False
    for page_file, _param, _name in PART_PAGES:
        if re.search(re.escape(page_file), path, re.IGNORECASE):
            qs = parsed.query
            m = re.search(r'(?:enckey|EncKey)=([^&]+)', qs, re.IGNORECASE)
            if m:
                enc_key = m.group(1)
            matched = True
            break

    if not matched or enc_key is None:
        return None

    base_dir = path.rsplit('/', 1)[0]
    urls = []
    for page_file, param_name, name in PART_PAGES:
        new_path = f"{base_dir}/{page_file}"
        new_query = f"{param_name}={enc_key}"
        new_url = urlunparse((parsed.scheme, parsed.netloc, new_path, '', new_query, ''))
        urls.append((name, new_url))
    return urls


# ── JS extraction logic (parsing only, no PDF rendering -> much cheaper) ──
EXTRACT_JS = r"""
(function () {
  var allTables = document.querySelectorAll('table[border="1"]');
  var results = [];

  allTables.forEach(function(table) {
    var allRows = Array.from(table.querySelectorAll('tr'));

    var optionRows = [];
    allRows.forEach(function(row) {
      var firstTd = row.querySelector('td');
      if (!firstTd) return;
      var w = (firstTd.getAttribute('width') || '').trim();
      var vAlign = (firstTd.getAttribute('valign') || '').toLowerCase();
      if (w === '2%' && vAlign !== 'top') optionRows.push(row);
    });

    if (optionRows.length !== 4) return;

    var colors = [];
    optionRows.forEach(function(row) {
      var firstTd = row.querySelector('td');
      var color = (firstTd.getAttribute('bgcolor') || '').toLowerCase();
      colors.push(color);
    });

    results.push({colors: colors});
  });

  return results;
})();
"""


def classify_question(colors):
    """
    colors: list of 4 strings, each '' / 'green' / 'yellow' / 'red'
    Returns one of: 'correct', 'wrong', 'skipped', 'unknown'
    """
    has_green = 'green' in colors
    has_red = 'red' in colors
    has_yellow = 'yellow' in colors

    if has_green:
        return 'correct'
    if has_red and has_yellow:
        return 'wrong'
    if has_yellow and not has_red:
        return 'skipped'
    return 'unknown'


async def fetch_part(browser, name, url):
    context = await browser.new_context(ignore_https_errors=True)
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_selector('table[border="1"]', timeout=15000)
        except PWTimeout:
            await context.close()
            return {"name": name, "url": url, "error": "No question tables found (bad URL?)"}

        raw = await page.evaluate(EXTRACT_JS)
        await context.close()

        correct = wrong = skipped = unknown = 0
        for q in raw:
            cls = classify_question(q["colors"])
            if cls == 'correct':
                correct += 1
            elif cls == 'wrong':
                wrong += 1
            elif cls == 'skipped':
                skipped += 1
            else:
                unknown += 1

        total = len(raw)
        attempted = correct + wrong
        return {
            "name": name,
            "url": url,
            "total_questions": total,
            "correct": correct,
            "wrong": wrong,
            "skipped": skipped,
            "unknown": unknown,
            "attempted": attempted,
        }
    except PWTimeout:
        await context.close()
        return {"name": name, "url": url, "error": "Timeout loading page"}
    except Exception as e:
        await context.close()
        return {"name": name, "url": url, "error": str(e)}


async def process_url(any_url: str):
    parts = derive_all_part_urls(any_url)
    if parts is None:
        return {"error": "Could not detect Part A/B/C/D pattern in URL. Make sure it's a ViewCandResponse(2/3/4).aspx link with enckey."}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
        )
        try:
            tasks = [fetch_part(browser, name, url) for name, url in parts]
            results = await asyncio.gather(*tasks)
        finally:
            await browser.close()

    grand_total = sum(r.get("total_questions", 0) for r in results)
    grand_correct = sum(r.get("correct", 0) for r in results)
    grand_wrong = sum(r.get("wrong", 0) for r in results)
    grand_skipped = sum(r.get("skipped", 0) for r in results)
    grand_attempted = grand_correct + grand_wrong

    return {
        "parts": results,
        "summary": {
            "total_questions": grand_total,
            "attempted": grand_attempted,
            "correct": grand_correct,
            "wrong": grand_wrong,
            "skipped": grand_skipped,
        }
    }


# ── Routes ───────────────────────────────────────────────────────────
PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SSC Answer Key Checker</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; background:#0f172a; color:#e2e8f0; margin:0; padding:0; }}
  .wrap {{ max-width: 720px; margin: 0 auto; padding: 32px 20px; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  p.sub {{ color:#94a3b8; margin-top:0; font-size:14px; }}
  form {{ margin-top: 24px; display:flex; gap:8px; flex-wrap:wrap; }}
  input[type=text] {{ flex:1; min-width:240px; padding:12px 14px; border-radius:8px; border:1px solid #334155; background:#1e293b; color:#e2e8f0; font-size:14px; }}
  button {{ padding:12px 20px; border-radius:8px; border:none; background:#22c55e; color:#0f172a; font-weight:700; cursor:pointer; font-size:14px; }}
  button:hover {{ background:#16a34a; }}
  .card {{ background:#1e293b; border-radius:10px; padding:16px; margin-top:16px; border:1px solid #334155; }}
  .grid {{ display:grid; grid-template-columns: repeat(2,1fr); gap:10px; margin-top:10px; }}
  .stat {{ background:#0f172a; padding:10px; border-radius:8px; text-align:center; }}
  .stat .num {{ font-size:22px; font-weight:800; }}
  .stat .lbl {{ font-size:11px; color:#94a3b8; text-transform:uppercase; }}
  .correct {{ color:#22c55e; }} .wrong {{ color:#ef4444; }} .skipped {{ color:#eab308; }}
  table {{ width:100%; border-collapse: collapse; margin-top:10px; font-size:13px;}}
  td, th {{ padding:6px 8px; border-bottom:1px solid #334155; text-align:left;}}
  .err {{ color:#ef4444; margin-top:16px; }}
  .loading {{ color:#94a3b8; margin-top:16px; display:none; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>SSC Answer Key Checker</h1>
  <p class="sub">Paste the URL of ANY part (A/B/C/D) of your answer key. All 4 parts are fetched automatically and scored.</p>
  <form id="f" method="post" action="/check">
    <input type="text" name="url" placeholder="https://...ViewCandResponse2.aspx?EncKey=..." required>
    <button type="submit">Check Score</button>
  </form>
  <div class="loading" id="loading">Fetching and scoring... this can take 15–30 seconds.</div>
  <div id="result"></div>
</div>
<script>
const form = document.getElementById('f');
const result = document.getElementById('result');
const loading = document.getElementById('loading');
form.addEventListener('submit', async (e) => {{
  e.preventDefault();
  result.innerHTML = '';
  loading.style.display = 'block';
  const url = form.url.value;
  try {{
    const res = await fetch('/api/check', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{url}})
    }});
    const data = await res.json();
    loading.style.display = 'none';
    if (data.error) {{
      result.innerHTML = '<div class="err">' + data.error + '</div>';
      return;
    }}
    const s = data.summary;
    let html = '<div class="card"><div class="grid">' +
      '<div class="stat"><div class="num">' + s.total_questions + '</div><div class="lbl">Total</div></div>' +
      '<div class="stat"><div class="num correct">' + s.correct + '</div><div class="lbl">Correct</div></div>' +
      '<div class="stat"><div class="num wrong">' + s.wrong + '</div><div class="lbl">Wrong</div></div>' +
      '<div class="stat"><div class="num skipped">' + s.skipped + '</div><div class="lbl">Skipped</div></div>' +
      '</div></div>';

    html += '<div class="card"><table><tr><th>Part</th><th>Total</th><th>Correct</th><th>Wrong</th><th>Skipped</th></tr>';
    data.parts.forEach(p => {{
      if (p.error) {{
        html += '<tr><td>' + p.name + '</td><td colspan=4 style="color:#ef4444">' + p.error + '</td></tr>';
      }} else {{
        html += '<tr><td>' + p.name + '</td><td>' + p.total_questions + '</td><td class="correct">' + p.correct + '</td><td class="wrong">' + p.wrong + '</td><td class="skipped">' + p.skipped + '</td></tr>';
      }}
    }});
    html += '</table></div>';
    result.innerHTML = html;
  }} catch (err) {{
    loading.style.display = 'none';
    result.innerHTML = '<div class="err">Request failed: ' + err + '</div>';
  }}
}});
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def home():
    return PAGE_TEMPLATE


@app.post("/api/check", response_class=JSONResponse)
async def api_check(request: Request):
    body = await request.json()
    url = (body or {}).get("url", "").strip()
    if not url.startswith("http"):
        return JSONResponse({"error": "Invalid URL. Must start with http/https."})
    data = await process_url(url)
    return JSONResponse(data)


@app.post("/check")
async def check_form(url: str = Form(...)):
    data = await process_url(url)
    return JSONResponse(data)


@app.get("/health")
async def health():
    return {"status": "ok"}
