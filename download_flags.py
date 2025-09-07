import os
import re
import json
import shutil
from typing import Dict, Optional

import requests
from bs4 import BeautifulSoup

BASE = "/workspace"
STATIC_DIR = os.path.join(BASE, "static")
FLAGS_RAW_DIR = os.path.join(STATIC_DIR, "flags", "raw")
FLAGS_BY_NAME_DIR = os.path.join(STATIC_DIR, "flags", "by-name")
MAPPING_PATH = os.path.join(STATIC_DIR, "flags", "mapping.json")

FLAGS_BASE_URL = "https://hamariweb.com//cricket/flags/"
SCHEDULES_URL = "https://hamariweb.com/cricket/schedules.aspx"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36",
}


def ensure_dirs() -> None:
    os.makedirs(FLAGS_RAW_DIR, exist_ok=True)
    os.makedirs(FLAGS_BY_NAME_DIR, exist_ok=True)


def slugify(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "unknown"


def download_gif(flag_id: int) -> Optional[str]:
    dst = os.path.join(FLAGS_RAW_DIR, f"{flag_id}.gif")
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        return dst
    url = f"{FLAGS_BASE_URL}{flag_id}.gif"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        # Some ids may not exist; treat non-gif or tiny files as invalid
        if len(r.content) < 100:
            return None
        with open(dst, "wb") as f:
            f.write(r.content)
        return dst
    except Exception:
        return None


def build_id_to_name_map() -> Dict[str, str]:
    # Scrape schedules page to associate cricflag/<id>.png or flags/<id>.gif to visible team names
    r = requests.get(SCHEDULES_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    mapping: Dict[str, str] = {}

    def record(img_src: Optional[str], name: Optional[str]) -> None:
        if not img_src or not name:
            return
        m = re.search(r"(?:cricflag|flags)/([0-9]+)\.(?:png|gif)", img_src)
        if not m:
            return
        key = m.group(1)
        if key not in mapping:
            mapping[key] = name.strip()

    # From table entries (more reliable)
    for a in soup.select("a.team_name"):
        img = a.find("img")
        img_src = (img.get("data-src") or img.get("src")) if img else None
        span = a.find("span")
        name = (span.get_text(strip=True) if span else a.get_text(" ", strip=True)) or None
        if not name and img and img.get("alt"):
            name = img["alt"]
        record(img_src, name)

    # From card entries
    for tn in soup.select(".match_update .teamname"):
        img = tn.find("img")
        img_src = img.get("src") if img else None
        # name after img might be available in text
        txt = tn.get_text(" ", strip=True)
        # remove score numbers if present
        name = re.split(r"\s{2,}|\s+\d+/?\d*", txt)[0].strip() if txt else None
        if not name and img and img.get("alt"):
            name = img["alt"]
        record(img_src, name)

    return mapping


def write_by_name_gifs(id_to_name: Dict[str, str]) -> Dict[str, str]:
    # Returns id->relative local path mapping
    used_names: Dict[str, int] = {}
    id_to_path: Dict[str, str] = {}
    for sid, name in id_to_name.items():
        src = os.path.join(FLAGS_RAW_DIR, f"{sid}.gif")
        if not os.path.exists(src):
            continue
        base = slugify(name)
        count = used_names.get(base, 0)
        out_name = base if count == 0 else f"{base}-{count+1}"
        used_names[base] = count + 1
        dst = os.path.join(FLAGS_BY_NAME_DIR, f"{out_name}.gif")
        shutil.copyfile(src, dst)
        rel = f"/static/flags/by-name/{out_name}.gif"
        id_to_path[sid] = rel
    return id_to_path


def _load_existing_mapping() -> Dict[str, Dict[str, str]]:
    try:
        with open(MAPPING_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {
                "id_to_name": data.get("id_to_name", {}),
                "id_to_path": data.get("id_to_path", {}),
            }
    except FileNotFoundError:
        return {"id_to_name": {}, "id_to_path": {}}


def main() -> None:
    ensure_dirs()

    # Step 0: load existing mapping to merge idempotently
    existing = _load_existing_mapping()

    # Step 1: download raw gifs for a broader range of ids
    # Some flags have ids above 129 (e.g., Oman 182)
    available = 0
    for i in range(1, 301):
        path = download_gif(i)
        if path:
            available += 1
    print(f"Downloaded/kept {available} gifs to {FLAGS_RAW_DIR}")

    # Step 2: map ids to names from schedules page
    id_to_name_new = build_id_to_name_map()
    print(f"Newly scraped {len(id_to_name_new)} id->name entries")

    # Merge names (prefer newly scraped names if available)
    id_to_name = dict(existing.get("id_to_name", {}))
    id_to_name.update(id_to_name_new)

    # Step 3: create by-name copies for any ids that have raw gifs
    id_to_path_new = write_by_name_gifs(id_to_name)

    # Merge paths:
    # - Keep existing paths if file exists
    # - Use newly created paths otherwise
    merged_paths: Dict[str, str] = {}
    for sid, p in existing.get("id_to_path", {}).items():
        # validate file exists
        rel = p
        fs_path = os.path.join(BASE, rel.lstrip("/")) if rel.startswith("/") else os.path.join(BASE, rel)
        if os.path.exists(fs_path):
            merged_paths[sid] = rel
    for sid, rel in id_to_path_new.items():
        if sid not in merged_paths:
            merged_paths[sid] = rel

    mapping = {
        "id_to_name": id_to_name,
        "id_to_path": merged_paths,
    }
    os.makedirs(os.path.dirname(MAPPING_PATH), exist_ok=True)
    with open(MAPPING_PATH, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    print(f"Wrote merged mapping to {MAPPING_PATH}")


if __name__ == "__main__":
    main()