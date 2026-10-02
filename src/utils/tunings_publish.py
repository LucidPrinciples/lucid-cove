"""Daily lucidtuner.com /tunings auto-publish.

Muse writes drop-guided-YYYY-MM-DD-youtube-id.txt. This job reads that
sidecar (playlist RSS as fallback), writes the day page + archive index
into the lucidtuner.com Nextcloud Sites folder, then merges to main
without an Attention card.

site_deploy stays APPROVE for every other site. This path is locked to
lucidtuner.com and only the tunings archive files.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote
from xml.etree import ElementTree as ET

import httpx

from src.env import env
from src.utils.time_utils import today_app, ts_log

log = logging.getLogger("tunings_publish")

TUNER_DOMAIN = "lucidtuner.com"
DROP_URL = "https://drop.lucidprinciples.com/drops/{y}/{m}/{d}.json"
PLAYLIST_FEED = (
    "https://www.youtube.com/feeds/videos.xml?playlist_id={pid}"
)
DEFAULT_PLAYLIST_ID = "PLOK8y2vDqDKg"
SITES_REL = "AgentSkills/Sites"
ARCHIVE_REL = "AgentSkills/Working/tunings-archive"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
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


def _principle_slug(principle: str) -> str:
    return "".join(ch for ch in (principle or "").lower() if ch.isalnum())


def parse_youtube_id(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    first = text.splitlines()[0].strip()
    if ID_RE.match(first):
        return first
    m = re.search(r"(?:v=|/embed/|/shorts/|youtu\.be/)([A-Za-z0-9_-]{11})", first)
    if m:
        return m.group(1)
    return ""


def playlist_id() -> str:
    return (env("TUNINGS_PLAYLIST_ID") or DEFAULT_PLAYLIST_ID).strip()


def _admin_nc_creds() -> tuple[str, str, str]:
    url = (env("NEXTCLOUD_URL") or "").rstrip("/")
    user = env("NEXTCLOUD_ADMIN_USER") or env("NC_ADMIN_USER") or env("NEXTCLOUD_USER") or ""
    password = (
        env("NEXTCLOUD_ADMIN_PASSWORD")
        or env("NC_ADMIN_PASSWORD")
        or env("NEXTCLOUD_PASSWORD")
        or ""
    )
    return url, user, password


def _dav_file(nc_url: str, user: str, rel: str) -> str:
    return (
        f"{nc_url}/remote.php/dav/files/{quote(user, safe='')}/"
        f"{rel.lstrip('/')}"
    )


async def _nc_get_text(client: httpx.AsyncClient, url: str, user: str, password: str) -> str:
    r = await client.get(url, auth=(user, password))
    if r.status_code == 404:
        return ""
    r.raise_for_status()
    return r.text or ""


async def _nc_put_text(
    client: httpx.AsyncClient, url: str, user: str, password: str, body: str, content_type: str
) -> None:
    r = await client.put(
        url,
        auth=(user, password),
        content=body.encode("utf-8"),
        headers={"Content-Type": content_type},
    )
    if r.status_code not in (200, 201, 204):
        raise RuntimeError(f"WebDAV PUT {url} failed: HTTP {r.status_code}")


async def _nc_mkcol(client: httpx.AsyncClient, url: str, user: str, password: str) -> None:
    r = await client.request("MKCOL", url, auth=(user, password))
    if r.status_code not in (201, 405):
        raise RuntimeError(f"WebDAV MKCOL {url} failed: HTTP {r.status_code}")


def sidecar_paths(day: str) -> list[Path]:
    name = f"drop-guided-{day}-youtube-id.txt"
    return [
        Path("/app/data/scratch/tunings-ids") / name,
        Path("/app/data/scratch") / name,
    ]


def read_local_sidecar(day: str) -> str:
    for path in sidecar_paths(day):
        try:
            if path.is_file():
                found = parse_youtube_id(path.read_text())
                if found:
                    return found
        except OSError:
            continue
    return ""


async def read_nc_sidecar(client: httpx.AsyncClient, nc_url: str, user: str, password: str, day: str) -> str:
    name = f"drop-guided-{day}-youtube-id.txt"
    rels = [
        f"{ARCHIVE_REL}/{name}",
        f"AgentSkills/Working/tunings-ids/{name}",
    ]
    for rel in rels:
        try:
            text = await _nc_get_text(client, _dav_file(nc_url, user, rel), user, password)
        except Exception:
            continue
        found = parse_youtube_id(text)
        if found:
            return found
    return ""


def parse_playlist_feed(xml_text: str, echo: int | None = None) -> str:
    """Return the newest Daily Tuning video id, optionally matching echo #."""
    if not (xml_text or "").strip():
        return ""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }
    entries = root.findall("atom:entry", ns) or list(root)
    for entry in entries:
        vid_el = entry.find("yt:videoId", ns)
        vid = (vid_el.text or "").strip() if vid_el is not None else ""
        if not ID_RE.match(vid):
            continue
        title_el = entry.find("atom:title", ns)
        title = (title_el.text or "") if title_el is not None else ""
        if echo is not None:
            if re.search(rf"#\s*{echo}\b", title) or re.search(rf"Tuning Day {echo}\b", title):
                return vid
        else:
            if "Daily Tuning" in title:
                return vid
    # Last resort: first 11-char id in the feed
    m = re.search(r"yt:video:([A-Za-z0-9_-]{11})", xml_text)
    return m.group(1) if m else ""


async def fetch_playlist_id(echo: int | None = None) -> str:
    pid = playlist_id()
    url = PLAYLIST_FEED.format(pid=pid)
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, headers={"User-Agent": "LucidTunerArchive/1.0"})
        r.raise_for_status()
        return parse_playlist_feed(r.text, echo=echo)


async def fetch_drop(day: str) -> dict:
    y, m, d = day.split("-")
    url = DROP_URL.format(y=y, m=m, d=d)
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, headers={"User-Agent": "LucidTunerArchive/1.0"})
        r.raise_for_status()
        return r.json()


def entry_from_drop(day: str, drop: dict, youtube_id: str) -> dict:
    freq = ((drop.get("frequency") or {}).get("name") or "").strip()
    principle = ((drop.get("tuning_key") or {}).get("source_song") or "").strip()
    signal = (drop.get("signal_type") or "").strip()
    key = ((drop.get("tuning_key") or {}).get("text") or "").strip()
    echo = drop.get("tuning_day")
    weekday = datetime.strptime(day, "%Y-%m-%d").strftime("%A")
    return {
        "date": day,
        "echo": echo,
        "weekday": weekday,
        "frequency": freq,
        "principle": principle,
        "signal": signal,
        "color_var": FREQ_COLORS.get(freq, "--peace"),
        "youtube_id": youtube_id,
        "principle_slug": _principle_slug(principle),
        "tuning_key": key,
        "shorts": [],
    }


def upsert_catalog(data: dict, entry: dict) -> dict:
    entries = list(data.get("entries") or [])
    day = entry["date"]
    entries = [e for e in entries if e.get("date") != day]
    entries.insert(0, entry)
    entries.sort(key=lambda e: e.get("date") or "", reverse=True)
    out = dict(data)
    out["entries"] = entries
    return out


def patch_sitemap(xml_text: str, day: str) -> str:
    site = "https://lucidtuner.com"
    day_loc = f"{site}/tunings/{day}/"
    index_loc = f"{site}/tunings/"
    day_url = (
        f"  <url><loc>{day_loc}</loc><lastmod>{day}</lastmod>"
        f"<changefreq>monthly</changefreq><priority>0.7</priority></url>"
    )
    index_url = (
        f"  <url><loc>{index_loc}</loc><lastmod>{day}</lastmod>"
        f"<changefreq>daily</changefreq><priority>0.9</priority></url>"
    )
    text = xml_text or ""
    if not text.strip():
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{index_url}\n{day_url}\n</urlset>\n"
        )
    text = re.sub(
        r"  <url><loc>https://lucidtuner.com/tunings/</loc>.*?</url>",
        index_url,
        text,
        count=1,
        flags=re.DOTALL,
    )
    if day_loc not in text:
        text = text.replace(index_url, index_url + "\n" + day_url, 1)
    return text


def allowed_domain(domain: str) -> bool:
    return (domain or "").strip().lower() == TUNER_DOMAIN


async def _live_has_day(day: str, youtube_id: str) -> bool:
    url = f"https://lucidtuner.com/tunings/{day}/"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url)
        if r.status_code != 200:
            return False
        body = r.text or ""
        return youtube_id in body if youtube_id else "DAILY TUNING" in body
    except Exception:
        return False


async def publish_today(day: str | None = None) -> dict:
    """Write today's archive page and merge lucidtuner.com without a card.

    Returns a status dict. Never raises into the scheduler loop.
    """
    day = day or today_app()
    try:
        return await _publish_today(day)
    except Exception as e:
        log.exception("tunings auto-publish failed")
        print(f"{ts_log()} [tunings-publish] FAILED {day}: {e}")
        try:
            from src.tools.comms_tools import notify_operator
            await notify_operator(
                f"Daily Tuner archive auto-publish failed for {day}: {e}",
                urgency="high",
            )
        except Exception:
            pass
        return {"ok": False, "day": day, "error": str(e)}


async def _publish_today(day: str) -> dict:
    from src.utils import tunings_pages as pages

    drop = await fetch_drop(day)
    echo = drop.get("tuning_day")
    nc_url, user, password = _admin_nc_creds()
    if not nc_url or not user or not password:
        raise RuntimeError("Nextcloud admin credentials are not set")

    youtube_id = read_local_sidecar(day)
    async with httpx.AsyncClient(timeout=60) as client:
        site_yaml_url = _dav_file(nc_url, user, f"{SITES_REL}/{TUNER_DOMAIN}/site.yaml")
        site_yaml = await _nc_get_text(client, site_yaml_url, user, password)
        if not site_yaml.strip():
            print(f"{ts_log()} [tunings-publish] skip: this Cove does not host {TUNER_DOMAIN}")
            return {"ok": True, "skipped": "no_tuner_site"}
        if not youtube_id:
            youtube_id = await read_nc_sidecar(client, nc_url, user, password, day)
        if not youtube_id:
            try:
                youtube_id = await fetch_playlist_id(echo=echo if isinstance(echo, int) else None)
            except Exception as e:
                log.warning("playlist fallback failed: %s", e)
        if not youtube_id:
            print(f"{ts_log()} [tunings-publish] {day}: no YouTube id yet (sidecar + playlist)")
            return {"ok": True, "skipped": "waiting_for_id", "day": day}

        if await _live_has_day(day, youtube_id):
            print(f"{ts_log()} [tunings-publish] {day}: already live ({youtube_id})")
            return {"ok": True, "skipped": "already_live", "day": day, "youtube_id": youtube_id}

        catalog_url = _dav_file(nc_url, user, f"{ARCHIVE_REL}/catalog.json")
        raw_catalog = await _nc_get_text(client, catalog_url, user, password)
        data = json.loads(raw_catalog) if raw_catalog.strip() else {
            "series": "Lucid Tuner Orchestrated Tuning",
            "index_url": "https://lucidtuner.com/tunings/",
            "cta": "https://lucidtuner.com/try",
            "entries": [],
        }
        entry = entry_from_drop(day, drop, youtube_id)
        if not entry["frequency"] or not entry["principle"] or not entry["tuning_key"]:
            raise RuntimeError(f"drop {day} missing frequency/principle/key")
        data = upsert_catalog(data, entry)
        catalog_json = json.dumps(data, indent=2) + "\n"

        index_html = pages.write_index(data["entries"], dest_dir=None)
        day_html = pages.write_day(entry, dest_dir=None)

        sitemap_url = _dav_file(nc_url, user, f"{SITES_REL}/{TUNER_DOMAIN}/sitemap.xml")
        sitemap = patch_sitemap(await _nc_get_text(client, sitemap_url, user, password), day)

        day_dir = _dav_file(nc_url, user, f"{SITES_REL}/{TUNER_DOMAIN}/tunings/{day}")
        await _nc_mkcol(client, day_dir, user, password)
        await _nc_put_text(
            client, catalog_url, user, password, catalog_json, "application/json"
        )
        await _nc_put_text(
            client,
            _dav_file(nc_url, user, f"{SITES_REL}/{TUNER_DOMAIN}/tunings/index.html"),
            user,
            password,
            index_html,
            "text/html; charset=utf-8",
        )
        await _nc_put_text(
            client,
            f"{day_dir}/index.html",
            user,
            password,
            day_html,
            "text/html; charset=utf-8",
        )
        await _nc_put_text(client, sitemap_url, user, password, sitemap, "application/xml")

    deployed = await auto_deploy_tuner(
        f"Daily tuning archive {day} ({entry['frequency']} · {entry['principle']})"
    )
    print(
        f"{ts_log()} [tunings-publish] {day}: published {youtube_id} "
        f"{entry['frequency']} · {entry['principle']} deploy={deployed.get('status')}"
    )
    return {
        "ok": True,
        "day": day,
        "youtube_id": youtube_id,
        "frequency": entry["frequency"],
        "principle": entry["principle"],
        "deploy": deployed,
    }


async def auto_deploy_tuner(description: str) -> dict:
    """Mirror lucidtuner.com Sites → GitHub and merge to main with no card."""
    if not allowed_domain(TUNER_DOMAIN):
        return {"status": "refused", "error": "domain lock"}
    nc_url, user, password = _admin_nc_creds()
    from src.config import get_feature_flags, get_primary_agent_id
    from src.dashboard.routes.sites import _deploy_site_core
    from src.utils.github import github_delete_branch, github_merge_branch

    pat = (get_feature_flags() or {}).get("github_pat") or ""
    if not pat:
        raise RuntimeError("GitHub PAT not configured")
    agent_id = get_primary_agent_id()
    result = await _deploy_site_core(
        nc_url, user, password, TUNER_DOMAIN, description, agent_id,
        raise_approval=False,
    )
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "deploy failed")
    if result.get("no_changes"):
        return {"status": "no_changes"}
    repo = result.get("repo") or ""
    branch = result.get("branch") or ""
    if not repo or not branch:
        raise RuntimeError("deploy did not return repo/branch")
    merge = await github_merge_branch(
        repo, "main", branch, f"Auto-publish: {description}", pat
    )
    if not merge.get("merged"):
        raise RuntimeError(merge.get("error") or "merge failed")
    try:
        await github_delete_branch(repo, branch, pat)
    except Exception:
        pass
    return {"status": "merged", "repo": repo, "branch": branch}


def in_morning_window(now: Optional[datetime] = None) -> bool:
    """07:30–08:45 app-local — sidecar may land after 6:25."""
    from src.utils.time_utils import now_app

    stamp = now or now_app()
    minutes = stamp.hour * 60 + stamp.minute
    return (7 * 60 + 30) <= minutes <= (8 * 60 + 45)


async def scheduled_publish() -> dict:
    if not in_morning_window():
        return {"ok": True, "skipped": "outside_window"}
    return await publish_today()
