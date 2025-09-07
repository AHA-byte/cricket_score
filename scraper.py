from __future__ import annotations

import re
import time
import json
import os
import shutil
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup, Tag


_SCHEDULES_URL = "https://hamariweb.com/cricket/schedules.aspx"
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

_STATIC_FLAGS_MAPPING = "/workspace/static/flags/mapping.json"
_FLAGS_RAW_DIR = "/workspace/static/flags/raw"
_FLAGS_BY_NAME_DIR = "/workspace/static/flags/by-name"
_FLAGS_BASE_URL = "https://hamariweb.com//cricket/flags/"

# Simple in-memory cache with TTL to reduce upstream load
_CACHE: Dict[str, Dict[str, Any]] = {
    "html": {"value": None, "fetched_at": 0.0},
}
_CACHE_TTL_SECONDS = 60.0

# Cache for flags mapping
_FLAGS_MAP: Dict[str, Any] = {}


def _ensure_dirs() -> None:
    os.makedirs(os.path.dirname(_STATIC_FLAGS_MAPPING), exist_ok=True)
    os.makedirs(_FLAGS_RAW_DIR, exist_ok=True)
    os.makedirs(_FLAGS_BY_NAME_DIR, exist_ok=True)


def _slugify(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "unknown"


def _save_flags_mapping(data: Dict[str, Any]) -> None:
    tmp = _STATIC_FLAGS_MAPPING + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _STATIC_FLAGS_MAPPING)


def _load_flags_mapping() -> Dict[str, Any]:
    global _FLAGS_MAP
    if _FLAGS_MAP:
        return _FLAGS_MAP
    try:
        with open(_STATIC_FLAGS_MAPPING, "r", encoding="utf-8") as f:
            _FLAGS_MAP = json.load(f)
    except FileNotFoundError:
        _FLAGS_MAP = {"id_to_path": {}, "id_to_name": {}}
    return _FLAGS_MAP


def _download_flag_gif(flag_id: str) -> Optional[str]:
    _ensure_dirs()
    dst = os.path.join(_FLAGS_RAW_DIR, f"{flag_id}.gif")
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        return dst
    try:
        url = f"{_FLAGS_BASE_URL}{flag_id}.gif"
        r = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=20)
        r.raise_for_status()
        if len(r.content) < 100:
            return None
        with open(dst, "wb") as f:
            f.write(r.content)
        return dst
    except Exception:
        return None


def _ensure_flag_local(flag_id: str, team_name: str) -> Optional[str]:
    _ensure_dirs()
    flags = _load_flags_mapping()
    id_to_path: Dict[str, str] = flags.setdefault("id_to_path", {})
    id_to_name: Dict[str, str] = flags.setdefault("id_to_name", {})

    # If already mapped and file exists, return it
    existing = id_to_path.get(flag_id)
    if existing and os.path.exists(os.path.join("/workspace", existing.lstrip("/"))):
        # Update name if newly learned
        if team_name and id_to_name.get(flag_id) != team_name:
            id_to_name[flag_id] = team_name
            _save_flags_mapping(flags)
        return existing

    # Ensure raw GIF exists
    raw_path = _download_flag_gif(flag_id)
    if not raw_path:
        return None

    # Create by-name copy
    base = _slugify(team_name or f"flag-{flag_id}")
    out_path = os.path.join(_FLAGS_BY_NAME_DIR, f"{base}.gif")
    # If name occupied by another id, disambiguate
    suffix = 2
    while os.path.exists(out_path):
        # If this file is already mapped to this id, reuse
        rel = f"/static/flags/by-name/{os.path.basename(out_path)}"
        if id_to_path.get(flag_id) == rel:
            break
        out_path = os.path.join(_FLAGS_BY_NAME_DIR, f"{base}-{suffix}.gif")
        suffix += 1
    shutil.copyfile(raw_path, out_path)
    rel_path = f"/static/flags/by-name/{os.path.basename(out_path)}"

    # Update mapping cache and persist
    id_to_path[flag_id] = rel_path
    if team_name:
        id_to_name[flag_id] = team_name
    _save_flags_mapping(flags)
    # Refresh in-memory cache
    _FLAGS_MAP = flags
    return rel_path


def _local_gif_for_src(src: Optional[str]) -> Optional[str]:
    if not src:
        return None
    m = re.search(r"(?:cricflag|flags)/([0-9]+)\.(?:png|gif)", src)
    if not m:
        return None
    flags = _load_flags_mapping()
    local = flags.get("id_to_path", {}).get(m.group(1))
    return local


def _is_cache_fresh() -> bool:
    fetched_at = _CACHE["html"]["fetched_at"]
    return (time.time() - fetched_at) < _CACHE_TTL_SECONDS and _CACHE["html"]["value"] is not None


def fetch_schedules_html() -> str:
    if _is_cache_fresh():
        return _CACHE["html"]["value"]  # type: ignore[return-value]

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "keep-alive",
    }

    resp = requests.get(_SCHEDULES_URL, headers=headers, timeout=20)
    resp.raise_for_status()

    html_text = resp.text
    _CACHE["html"]["value"] = html_text
    _CACHE["html"]["fetched_at"] = time.time()
    return html_text


_HEADING_PATTERNS = [
    re.compile(r"upcoming", re.I),
    re.compile(r"schedule", re.I),
    re.compile(r"fixtures?", re.I),
    re.compile(r"matches", re.I),
]


def _text_matches_any(text: str, patterns: Iterable[re.Pattern[str]]) -> bool:
    return any(p.search(text) for p in patterns)


def _find_content_root(soup: BeautifulSoup) -> Tag:
    for id_candidate in ["main", "content", "mainContent", "ContentPlaceHolder1"]:
        el = soup.find(id=id_candidate)
        if el:
            return el
    main = soup.find("main")
    return main or soup.body or soup


def _norm_src(src: Optional[str]) -> Optional[str]:
    if not src:
        return None
    # Prefer local gif when mapping exists
    local = _local_gif_for_src(src)
    if local:
        return local
    # handle lazyloaded flags that use data-src
    if src.startswith("data:"):
        return None
    if src.startswith("//"):
        return f"https:{src}"
    if src.startswith("/"):
        return f"https://hamariweb.com{src}"
    return src


def _parse_match_updates(root: Tag) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for mu in root.select(".match_update"):
        # title/status
        p_tag = mu.find("p")
        raw_title = p_tag.get_text(" ", strip=True) if p_tag else ""
        status_tag = p_tag.find("small") if p_tag else None
        status_text = status_tag.get_text(strip=True) if status_tag else None
        if status_tag and p_tag:
            title = raw_title.replace(status_text or "", "").strip()
        else:
            title = raw_title

        # teams and images
        team_nodes = mu.select(".teamname")
        teams: List[str] = []
        team_images: List[Optional[str]] = []
        for tn in team_nodes:
            # Prefer textual span that is not a score
            name_span = None
            for sp in tn.find_all("span"):
                cls = sp.get("class") or []
                if "score" not in cls:
                    name_span = sp
                    break
            name_text = name_span.get_text(strip=True) if name_span else tn.get_text(" ", strip=True)
            if name_text:
                teams.append(name_text)
            img_tag = tn.find("img")
            img_src_raw = img_tag.get("src") if img_tag else None
            # Try to enrich mapping if we have id and name
            enriched = None
            if img_src_raw and name_text:
                m = re.search(r"(?:cricflag|flags)/([0-9]+)\.(?:png|gif)", img_src_raw)
                if m:
                    enriched = _ensure_flag_local(m.group(1), name_text)
            img_src = enriched or _norm_src(img_src_raw)
            team_images.append(img_src)
        if len(teams) > 2:
            teams = teams[:2]
            team_images = team_images[:2]

        # link
        link_tag = mu.find("a", href=True)
        href: Optional[str] = link_tag["href"] if link_tag else None
        href = _norm_src(href)

        # timing/result
        res = mu.find(class_="match_result")
        time_or_venue = res.get_text(" ", strip=True) if res else None

        # Skip empty entries
        if not ((title and title.strip()) or (teams and len(teams) > 0) or (time_or_venue and time_or_venue.strip())):
            continue

        items.append({
            "title": title,
            "teams": teams,
            "team_images": team_images,
            "status": status_text,
            "time_or_venue": time_or_venue,
            "link": href,
        })
    return items


def _parse_schedule_table(root: Tag) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    # Locate section heading near the main schedule table
    schedule_heading = None
    for h in root.find_all(["h2", "h3", "h1"]):
        txt = h.get_text(" ", strip=True).lower()
        if "schedule" in txt:
            schedule_heading = h
            break
    table = None
    if schedule_heading:
        # find the next table in a responsive wrapper
        wrapper = schedule_heading.find_next("div", class_=re.compile(r"table-responsive"))
        table = wrapper.find("table") if wrapper else schedule_heading.find_next("table")
    else:
        table = root.select_one(".table-responsive table.table, table.table")
    if not table:
        return items

    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        # Expecting 3 tds: Teams(cell0), Match(cell1, hidden-xs), Date & Time(cell2)
        teams_names: List[str] = []
        team_images: List[Optional[str]] = []
        link: Optional[str] = None

        # Extract team cells within the first td
        team_cells = tds[0].select("a.team_name")
        if team_cells and len(team_cells) >= 1:
            for a in team_cells[:2]:
                name_span = a.find("span")
                name = name_span.get_text(strip=True) if name_span else a.get_text(" ", strip=True)
                if name:
                    teams_names.append(name)
                img = a.find("img")
                # Prefer data-src when present, else src
                raw = (img.get("data-src") or img.get("src")) if img else None
                # Enrich mapping with id+name
                enriched = None
                if raw and name:
                    m = re.search(r"(?:cricflag|flags)/([0-9]+)\.(?:png|gif)", raw)
                    if m:
                        enriched = _ensure_flag_local(m.group(1), name)
                team_images.append(enriched or _norm_src(raw))
                if not link and a.get("href"):
                    link = _norm_src(a.get("href"))
        # Series link inside first td
        if not link:
            series_link = tds[0].find("a", href=True)
            if series_link:
                link = _norm_src(series_link.get("href"))

        match_type = None
        if len(tds) >= 2:
            match_type = tds[1].get_text(" ", strip=True) or None

        date_time = None
        if len(tds) >= 3:
            # Date line with optional small time
            date_line = tds[2].get_text(" ", strip=True)
            date_time = date_line or None

        # Compose a title if not available
        title_parts = []
        if teams_names:
            title_parts.append(" vs ".join(teams_names[:2]))
        if match_type:
            title_parts.append(match_type)
        title = ", ".join(title_parts) if title_parts else (match_type or "")

        # These table rows are typically upcoming/TBD
        status = None
        if date_time and re.search(r"\bPST\b|AM|PM", date_time, re.I):
            status = "Upcoming"

        # Skip empty entries
        if not ((title and title.strip()) or (teams_names and len(teams_names) > 0) or (date_time and date_time.strip())):
            continue

        # Keep TBD rows where names may be TBA
        items.append({
            "title": title,
            "teams": teams_names,
            "team_images": team_images,
            "status": status,
            "time_or_venue": date_time,
            "link": link,
        })
    return items


def parse_schedules_html(html_text: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html_text, "lxml")
    root = _find_content_root(soup)

    # 1) Card-style match updates
    card_items = _parse_match_updates(root)

    # 2) Table-based schedule including TBD
    table_items = _parse_schedule_table(root)

    # Merge and dedupe
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def key_for(it: Dict[str, Any]) -> str:
        title = it.get("title") or ""
        teams = "-".join(it.get("teams") or [])
        time_s = it.get("time_or_venue") or ""
        return f"{title}|{teams}|{time_s}"

    for it in card_items + table_items:
        k = key_for(it)
        if k in seen:
            continue
        seen.add(k)
        merged.append(it)

    # Return all items without artificial cap
    return merged


def fetch_scorecard_html(url: str) -> str:
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "keep-alive",
    }
    resp = requests.get(url, headers=headers, timeout=25)
    resp.raise_for_status()
    return resp.text


def _text(el: Optional[Tag]) -> str:
    return el.get_text(" ", strip=True) if el else ""


def _has_th_with_text(table: Tag, keyword: str) -> bool:
    thead = table.find("thead")
    if not thead:
        return False
    return keyword.lower() in thead.get_text(" ", strip=True).lower()


def _parse_batting_table(table: Tag) -> Dict[str, Any]:
    batting: List[Dict[str, Any]] = []
    extras: Optional[str] = None
    total: Optional[str] = None
    info_note: Optional[str] = None

    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        label = _text(tds[0]).lower()
        if label.startswith("extras"):
            extras = " ".join(_text(td) for td in tds[1:]).strip() or None
            continue
        if label.startswith("total"):
            total = " ".join(_text(td) for td in tds[1:]).strip() or None
            continue
        # Player rows: name + dismissal in first td with <b> and <small>
        name_b = tds[0].find("b")
        dismiss_small = tds[0].find("small")
        name = _text(name_b) or _text(tds[0])
        dismissal = _text(dismiss_small) or None
        stats = [
            (_text(tds[1]) if len(tds) > 1 else None),  # R
            (_text(tds[2]) if len(tds) > 2 else None),  # B
            (_text(tds[3]) if len(tds) > 3 else None),  # 4s
            (_text(tds[4]) if len(tds) > 4 else None),  # 6s
            (_text(tds[5]) if len(tds) > 5 else None),  # SR
        ]
        if name and any(s for s in stats) and not label.startswith("did not bat"):
            batting.append({
                "name": name,
                "dismissal": dismissal,
                "runs": stats[0],
                "balls": stats[1],
                "fours": stats[2],
                "sixes": stats[3],
                "sr": stats[4],
            })
    return {"batting": batting, "extras": extras, "total": total, "note": info_note}


def _parse_bowling_table(table: Tag) -> List[Dict[str, Any]]:
    bowlers: List[Dict[str, Any]] = []
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if not tds or len(tds) < 6:
            continue
        name = _text(tds[0])
        overs = _text(tds[1])
        maidens = _text(tds[2])
        runs = _text(tds[3])
        wickets = _text(tds[4])
        econ = _text(tds[5])
        if name:
            bowlers.append({
                "name": name, "ov": overs, "m": maidens, "r": runs, "w": wickets, "econ": econ
            })
    return bowlers


def _parse_match_info(soup: BeautifulSoup) -> Dict[str, str]:
    info: Dict[str, str] = {}
    # Find section titled "Match Information" then parse the following table rows
    title_div = None
    for div in soup.select(".section_title .title, .section_title h1, .section_title h2"):
        if "match information" in _text(div).lower():
            title_div = div
            break
    table = None
    if title_div:
        wrap = title_div.find_parent().find_next("div", class_=re.compile(r"table-responsive"))
        table = wrap.find("table") if wrap else None
    if not table:
        table = soup.find("table")
    if not table:
        return info
    for tr in table.find_all("tr"):
        th = tr.find("th")
        td = tr.find("td")
        key = _text(th)
        val = _text(td)
        if key and val:
            info[key] = val
    return info


def _extract_title_and_teams(soup: BeautifulSoup) -> Tuple[Optional[str], List[str]]:
    title = _text(soup.find("title")) or None
    teams: List[str] = []
    if title and " VS " in title:
        left = title.split(" VS ", 1)[0].strip()
        right_part = title.split(" VS ", 1)[1]
        right = right_part.split(",", 1)[0].strip()
        if left and right:
            teams = [left, right]
    return title, teams


def parse_scorecard_html(html_text: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html_text, "lxml")
    title, teams = _extract_title_and_teams(soup)

    # Collect all tables then select those with headers
    tables = soup.select("table.table, table")
    batting_tables: List[Tag] = []
    bowling_tables: List[Tag] = []
    for t in tables:
        txt = t.find("thead")
        if not txt:
            continue
        head = txt.get_text(" ", strip=True).lower()
        if "batting" in head:
            batting_tables.append(t)
        elif "bowling" in head:
            bowling_tables.append(t)

    innings: List[Dict[str, Any]] = []
    for idx, bt in enumerate(batting_tables):
        inn: Dict[str, Any] = _parse_batting_table(bt)
        # Pair with nearest next bowling table if available
        bowl = bowling_tables[idx] if idx < len(bowling_tables) else None
        if bowl is not None:
            inn["bowling"] = _parse_bowling_table(bowl)
        # Try to name innings
        if teams:
            inn["team"] = teams[idx % len(teams)]
        innings.append(inn)

    info = _parse_match_info(soup)

    return {
        "ok": True,
        "title": title,
        "teams": teams,
        "info": info,
        "innings": innings,
        "source": {
            "batting_count": len(batting_tables),
            "bowling_count": len(bowling_tables),
        }
    }