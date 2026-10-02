#!/usr/bin/env python3
"""Rebuild lucidtuner.com/tunings from catalog.json.

Run from this folder:
    python3 build_tunings.py

Writes index.html and YYYY-MM-DD/index.html. Does not deploy.
Paste a YouTube id into catalog.json first; morning 6:15 does not run this.

tuning_key is always overwritten from that day's drop JSON
(https://drop.lucidprinciples.com/drops/YYYY/MM/DD.json → tuning_key.text).
Never use Canon KEY_LYRIC as a stand-in. The drop is what the still and VO use.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATALOG = HERE / "catalog.json"
SITE = "https://lucidtuner.com"
DROP_URL = "https://drop.lucidprinciples.com/drops/{y}/{m}/{d}.json"

FREQ_COLORS = {
    "Peace": "--peace",
    "Clarity": "--clarity",
    "Momentum": "--momentum",
    "Trust": "--trust",
    "Joy": "--joy",
    "Connection": "--connection",
    "Presence": "--presence",
    "Resilience": "--resilience",
    "Courage": "--courage",
    "Gratitude": "--gratitude",
    "Release": "--release",
    "Integration": "--integration",
    "Boundary": "--boundary",
}

NAV = """<nav class="site-nav">
  <a href="/" class="nav-logo">
    <svg viewBox="0 0 128 128" width="30" height="30" aria-label="Lucid Tuner"><rect width="128" height="128" rx="26" fill="#0a0a0f"/><path d="M 94,48 Q 110,64 94,80" fill="none" stroke="#a0ebff" stroke-width="2.5" stroke-linecap="round" opacity="0.55"/><path d="M 93,55 Q 103,64 93,73" fill="none" stroke="#5ce1e6" stroke-width="3" stroke-linecap="round"/><path d="M 34,48 Q 18,64 34,80" fill="none" stroke="#a0ebff" stroke-width="2.5" stroke-linecap="round" opacity="0.55"/><path d="M 35,55 Q 25,64 35,73" fill="none" stroke="#5ce1e6" stroke-width="3" stroke-linecap="round"/><path d="M90,64 L73.2,67.8 L82.4,82.4 L67.8,73.2 L64,90 L60.2,73.2 L45.6,82.4 L54.8,67.8 L38,64 L54.8,60.2 L45.6,45.6 L60.2,54.8 L64,38 L67.8,54.8 L82.4,45.6 L73.2,60.2 Z" fill="#5ce1e6"/><circle cx="64" cy="64" r="3.5" fill="#eafdff"/></svg>
    <span>LUCID TUNER</span>
  </a>
  <ul class="nav-links" id="nav-links">
    <li><a href="/try">TRY IT</a></li>
    <li><a href="/playlists">PLAYLISTS</a></li>
    <li><a href="/tunings">TUNINGS</a></li>
    <li><a href="/start">GET STARTED</a></li>
    <li><a href="/affiliates">EARN</a></li>
    <li><a href="/faq">FAQ</a></li>
    <li><a href="https://app.lucidtuner.com" class="nav-cta" data-umami-event="cta-sign-in">SIGN IN</a></li>
  </ul>
  <button class="nav-mobile-toggle" onclick="document.getElementById('nav-links').classList.toggle('open')">&#9776;</button>
</nav>"""

FOOTER = """<footer>
  <ul class="footer-links">
    <li><a href="/try">TRY IT</a></li>
    <li><a href="/playlists">PLAYLISTS</a></li>
    <li><a href="/tunings">TUNINGS</a></li>
    <li><a href="/start">GET STARTED</a></li>
    <li><a href="/affiliates">EARN</a></li>
    <li><a href="/faq">FAQ</a></li>
    <li><a href="/legal/terms.html">TERMS</a></li>
    <li><a href="/legal/privacy.html">PRIVACY</a></li>
  </ul>
  <p>&copy; 2026 <a href="https://lucidprinciples.com">Lucid Principles</a>. All rights reserved.</p>
</footer>"""

SHARED_CSS = """
  *{margin:0;padding:0;box-sizing:border-box}
  :root{
    --peace:#5ce1e6;--clarity:#a0ebff;--momentum:#ff6b5c;--trust:#b8c6db;
    --joy:#ffd700;--connection:#e0b0ff;--presence:#7b7394;--resilience:#d2691e;
    --courage:#ff8c00;--gratitude:#e8b830;--release:#9370db;--integration:#20b2aa;
    --boundary:#4682b4;
    --dark:#0a0a0f;--dark-mid:#111118;--dark-surface:#181820;--dark-elevated:#1e1e2a;
    --text-primary:#e8e8ec;--text-secondary:#b8b8c0;--text-muted:#6a6a78;
    --accent:#5ce1e6;
  }
  body{background:var(--dark);color:var(--text-primary);font-family:'Inter',system-ui,sans-serif;line-height:1.7;-webkit-font-smoothing:antialiased}
  a{color:inherit;text-decoration:none}
  .site-nav{position:fixed;top:0;left:0;right:0;z-index:100;padding:18px 32px;display:flex;align-items:center;justify-content:space-between;background:rgba(10,10,15,0.9);backdrop-filter:blur(12px);border-bottom:1px solid rgba(92,225,230,0.04)}
  .nav-logo{display:flex;align-items:center;gap:10px}
  .nav-logo svg{width:30px;height:30px}
  .nav-logo span{font-size:11px;letter-spacing:3px;color:var(--peace);font-weight:500}
  .nav-links{display:flex;gap:28px;list-style:none;align-items:center}
  .nav-links a{font-size:11px;letter-spacing:2px;color:var(--text-secondary);transition:color 0.2s}
  .nav-links a:hover{color:var(--peace)}
  .nav-cta{padding:8px 20px;border:1px solid var(--peace);color:var(--peace);font-size:10px;letter-spacing:2px;font-weight:500;transition:all 0.3s}
  .nav-cta:hover{background:var(--peace);color:var(--dark)}
  .nav-mobile-toggle{display:none;background:none;border:none;color:var(--text-secondary);font-size:20px;cursor:pointer}
  footer{text-align:center;padding:40px 32px;border-top:1px solid rgba(255,255,255,0.03)}
  .footer-links{display:flex;justify-content:center;gap:24px;margin-bottom:16px;list-style:none;flex-wrap:wrap}
  .footer-links a{font-size:10px;letter-spacing:2px;color:var(--text-muted);transition:color 0.2s}
  .footer-links a:hover{color:var(--peace)}
  footer p{font-size:10px;letter-spacing:2px;color:var(--text-muted)}
  footer a{color:var(--boundary)}
  .cta-bar{text-align:center;padding:40px 24px;margin-top:48px;background:var(--dark-mid);border:1px solid rgba(92,225,230,0.08);border-radius:8px}
  .cta-bar p{font-size:14px;color:var(--text-secondary);margin-bottom:20px}
  .cta-bar a,.cta-bar a.btn{display:inline-block;padding:12px 32px;background:var(--peace);color:var(--dark);font-size:11px;letter-spacing:2px;font-weight:600}
  .cta-bar a:hover{background:#7ceaee}
  @media(max-width:700px){
    .site-nav{padding:14px 20px}
    .nav-links{display:none}
    .nav-mobile-toggle{display:block}
    .nav-links.open{display:flex;flex-direction:column;position:absolute;top:56px;left:0;right:0;background:var(--dark);padding:20px 24px;gap:16px;border-bottom:1px solid rgba(92,225,230,0.08)}
  }
"""

INDEX_CSS = SHARED_CSS + """
  .page-wrap{max-width:900px;margin:0 auto;padding:120px 24px 80px}
  .page-title{font-size:clamp(24px,4vw,36px);font-weight:300;letter-spacing:2px;text-align:center;margin-bottom:12px}
  .page-title em{color:var(--peace);font-style:normal;font-weight:400}
  .page-sub{text-align:center;font-size:15px;color:var(--text-secondary);max-width:600px;margin:0 auto 48px}
  .tuning-table{width:100%;border-collapse:collapse;margin-top:32px}
  .tuning-table th{font-size:10px;letter-spacing:2px;color:var(--text-muted);text-align:left;padding:12px 16px;border-bottom:1px solid rgba(92,225,230,0.08);font-weight:500}
  .tuning-table td{padding:16px;border-bottom:1px solid rgba(92,225,230,0.04);font-size:14px;color:var(--text-secondary)}
  .tuning-table td a{color:var(--peace);border-bottom:1px solid rgba(92,225,230,0.2);padding-bottom:1px}
  .tuning-table .freq-cell{font-weight:500}
  .tuning-table .today-row{background:rgba(92,225,230,0.03)}
  .tuning-table .date-col{font-size:12px;letter-spacing:1px;color:var(--text-muted);white-space:nowrap}
  @media(max-width:700px){
    .tuning-table th:nth-child(4),.tuning-table td:nth-child(4){display:none}
    .tuning-table td{padding:12px 8px}
  }
"""

DAY_CSS = SHARED_CSS + """
  .page-wrap{max-width:780px;margin:0 auto;padding:120px 24px 80px}
  .crumb{font-size:10px;letter-spacing:2px;color:var(--text-muted);margin-bottom:28px}
  .crumb a{color:var(--peace)}
  .day-label{font-size:11px;letter-spacing:3px;color:var(--text-muted);margin-bottom:12px}
  h1{font-size:clamp(26px,4vw,40px);font-weight:300;letter-spacing:2px;line-height:1.3;margin-bottom:8px}
  h1 em{font-style:normal;font-weight:400}
  .day-meta{font-size:14px;color:var(--text-secondary);margin-bottom:32px}
  .player{position:relative;width:100%;padding-bottom:56.25%;background:#000;border:1px solid rgba(92,225,230,0.08);border-radius:8px;overflow:hidden;margin-bottom:28px}
  .player iframe{position:absolute;inset:0;width:100%;height:100%;border:0}
  .key-block{padding:24px 28px;background:var(--dark-mid);border-left:2px solid var(--peace);border-radius:0 6px 6px 0;margin-bottom:28px}
  .key-block .label{font-size:9px;letter-spacing:3px;color:var(--text-muted);margin-bottom:8px}
  .key-block p{font-size:16px;font-weight:300;color:var(--text-primary);line-height:1.6}
  .links{display:flex;flex-wrap:wrap;gap:16px;margin-bottom:40px}
  .links a{font-size:11px;letter-spacing:2px;color:var(--peace);border-bottom:1px solid rgba(92,225,230,0.25);padding-bottom:2px}
  .cta-bar{margin-top:0}
"""


def display_date(iso: str) -> str:
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%B %-d, %Y")


def short_date(iso: str) -> str:
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%b %-d, %Y")


def esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def fetch_drop(iso: str) -> dict:
    y, m, d = iso.split("-")
    url = DROP_URL.format(y=y, m=m, d=d)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "LucidTunerArchive/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"drop JSON failed for {iso} ({url}): {exc}") from exc


def apply_drop_fields(e: dict) -> None:
    """Page key must match the day's drop, not Canon KEY_LYRIC for the principle."""
    drop = fetch_drop(e["date"])
    text = ((drop.get("tuning_key") or {}).get("text") or "").strip()
    if not text:
        raise RuntimeError(f"drop {e['date']} has no tuning_key.text")
    e["tuning_key"] = text
    freq = ((drop.get("frequency") or {}).get("name") or "").strip()
    principle = ((drop.get("tuning_key") or {}).get("source_song") or "").strip()
    signal = (drop.get("signal_type") or "").strip()
    if freq:
        e["frequency"] = freq
    if principle:
        e["principle"] = principle
    if signal:
        e["signal"] = signal
    echo = drop.get("tuning_day")
    if echo is not None:
        e["echo"] = echo


def load() -> list[dict]:
    data = json.loads(CATALOG.read_text())
    entries = sorted(data["entries"], key=lambda e: e["date"], reverse=True)
    for e in entries:
        if not e.get("youtube_id"):
            raise RuntimeError(f"catalog entry {e['date']} has no youtube_id")
        apply_drop_fields(e)
        e.setdefault("color_var", FREQ_COLORS.get(e["frequency"], "--peace"))
    data["entries"] = entries
    CATALOG.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {CATALOG} (keys from drop JSON)")
    return entries


def write_index(entries: list[dict], dest_dir: Path | None = None) -> str:
    rows = []
    items = []
    for i, e in enumerate(entries):
        cls = ' class="today-row"' if i == 0 else ""
        color = e["color_var"]
        rows.append(
            f"""      <tr{cls}>
        <td class="date-col">{esc(short_date(e['date']))}</td>
        <td class="freq-cell" style="color:var({color})">{esc(e['frequency'])}</td>
        <td>{esc(e['principle'])}</td>
        <td>{esc(e.get('signal', ''))}</td>
        <td><a href="/tunings/{e['date']}/">Watch →</a></td>
      </tr>"""
        )
        items.append(
            f'      {{"@type":"ListItem","position":{i+1},"url":"{SITE}/tunings/{e["date"]}/","name":"{esc(e["frequency"])} · {esc(e["principle"])} — {esc(display_date(e["date"]))}"}}'
        )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Past Tunings — Lucid Tuner</title>
<meta name="description" content="Every daily tuning, archived. Date, frequency, principle, and the full video. A free daily practice.">
<meta name="theme-color" content="#0a0a0f">
<link rel="icon" type="image/x-icon" href="/favicon.ico">
<link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<script>
(function(){{
  var params=new URLSearchParams(window.location.search);
  var code=params.get('a')||params.get('aff_id')||params.get('ref');
  if(code)localStorage.setItem('lucid_referral_code',code);
}})();
</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>{INDEX_CSS}</style>
<link rel="canonical" href="{SITE}/tunings/">
<meta property="og:site_name" content="Lucid Tuner">
<meta property="og:type" content="website">
<meta property="og:url" content="{SITE}/tunings/">
<meta property="og:title" content="Past Tunings — Lucid Tuner">
<meta property="og:description" content="Every daily tuning, archived. Date, frequency, principle, and the full video.">
<meta property="og:image" content="{SITE}/og/home.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:site" content="@LucidPrinciple">
<meta name="twitter:title" content="Past Tunings — Lucid Tuner">
<meta name="twitter:description" content="Every daily tuning, archived. Date, frequency, principle, and the full video.">
<script type="application/ld+json">
{{
  "@context":"https://schema.org",
  "@graph":[{{
    "@type":"WebPage",
    "url":"{SITE}/tunings/",
    "name":"Past Tunings — Lucid Tuner"
  }},{{
    "@type":"ItemList",
    "itemListElement":[
{(',' + chr(10)).join(items)}
    ]
  }}]
}}
</script>
</head>
<body>
{NAV}

<main class="page-wrap">
  <h1 class="page-title">Past <em>Tunings</em></h1>
  <p class="page-sub">Every daily tuning, archived. Click a date for the full video, frequency, and principle.</p>

  <table class="tuning-table">
    <thead>
      <tr>
        <th>DATE</th>
        <th>FREQUENCY</th>
        <th>PRINCIPLE</th>
        <th>SIGNAL</th>
        <th>VIDEO</th>
      </tr>
    </thead>
    <tbody>
{chr(10).join(rows)}
    </tbody>
  </table>

  <div class="cta-bar">
    <p>New tunings drop every morning. Start your own practice free.</p>
    <a href="/try">TRY A FREE TUNING</a>
  </div>
</main>

{FOOTER}
</body>
</html>
"""
    if dest_dir is not None:
        (dest_dir / "index.html").write_text(html)
        print(f"wrote {dest_dir / 'index.html'}")
    return html


def write_day(e: dict, dest_dir: Path | None = None) -> str:
    d = e["date"]
    yt = e["youtube_id"]
    color = e["color_var"]
    disp = display_date(d)
    title = f"{e['frequency']} · {e['principle']} — {disp} | Lucid Tuner"
    desc = f"Daily tuning for {disp}. Frequency {e['frequency']}, principle {e['principle']}. A free Lucid Tuner practice."
    url = f"{SITE}/tunings/{d}/"
    slug = e.get("principle_slug", "")
    principle_url = f"https://lucidprinciples.com/canon/{slug}/" if slug else "https://lucidprinciples.com/canon/"
    key = esc(e.get("tuning_key") or "")
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<meta name="theme-color" content="#0a0a0f">
<link rel="icon" type="image/x-icon" href="/favicon.ico">
<link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<script>
(function(){{
  var params=new URLSearchParams(window.location.search);
  var code=params.get('a')||params.get('aff_id')||params.get('ref');
  if(code)localStorage.setItem('lucid_referral_code',code);
}})();
</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>{DAY_CSS}</style>
<link rel="canonical" href="{url}">
<meta property="og:site_name" content="Lucid Tuner">
<meta property="og:type" content="video.other">
<meta property="og:url" content="{url}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:image" content="https://i.ytimg.com/vi/{yt}/maxresdefault.jpg">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:site" content="@LucidPrinciple">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(desc)}">
<meta name="twitter:image" content="https://i.ytimg.com/vi/{yt}/maxresdefault.jpg">
<script type="application/ld+json">
{{
  "@context":"https://schema.org",
  "@type":"VideoObject",
  "name":"{esc(e['frequency'])} · {esc(e['principle'])} — {esc(disp)}",
  "description":"{esc(desc)}",
  "thumbnailUrl":"https://i.ytimg.com/vi/{yt}/maxresdefault.jpg",
  "uploadDate":"{d}",
  "contentUrl":"https://www.youtube.com/watch?v={yt}",
  "embedUrl":"https://www.youtube.com/embed/{yt}",
  "publisher":{{"@type":"Organization","name":"Lucid Principles","url":"https://lucidprinciples.com/"}}
}}
</script>
</head>
<body>
{NAV}

<main class="page-wrap">
  <p class="crumb"><a href="/tunings">Past Tunings</a> · {esc(disp)}</p>
  <p class="day-label">DAILY TUNING #{e.get('echo','')}</p>
  <h1><em style="color:var({color})">{esc(e['frequency'])}</em> · {esc(e['principle'])}</h1>
  <p class="day-meta">{esc(e.get('weekday',''))} · {esc(e.get('signal',''))} Signal</p>

  <div class="player">
    <iframe src="https://www.youtube.com/embed/{yt}" title="{esc(e['frequency'])} · {esc(e['principle'])}" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>
  </div>

  <div class="key-block">
    <div class="label">TUNING KEY</div>
    <p>{key}</p>
  </div>

  <div class="links">
    <a href="{principle_url}">Principle on Lucid Principles</a>
    <a href="https://www.youtube.com/watch?v={yt}">Watch on YouTube</a>
    <a href="/tunings">All tunings</a>
  </div>

  <div class="cta-bar">
    <p>This is the practice. Start free — five minutes a day.</p>
    <a class="btn" href="/try">TRY A FREE TUNING</a>
  </div>
</main>

{FOOTER}
</body>
</html>
"""
    if dest_dir is not None:
        dest = dest_dir / d
        dest.mkdir(exist_ok=True)
        (dest / "index.html").write_text(html)
        print(f"wrote {dest / 'index.html'}")
    return html


def main() -> None:
    entries = load()
    write_index(entries, dest_dir=HERE)
    for e in entries:
        write_day(e, dest_dir=HERE)
    print("sitemap lines:")
    print(f'  <url><loc>{SITE}/tunings/</loc><lastmod>{entries[0]["date"]}</lastmod><changefreq>daily</changefreq><priority>0.9</priority></url>')
    for e in entries:
        print(f'  <url><loc>{SITE}/tunings/{e["date"]}/</loc><lastmod>{e["date"]}</lastmod><changefreq>monthly</changefreq><priority>0.7</priority></url>')


if __name__ == "__main__":
    main()
