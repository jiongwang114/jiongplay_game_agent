"""
One-shot initialization script: fetch Steam data → SQLite + ChromaDB.

Usage:
    python -m data_layer.scripts.init_top_2000_games

What it does:
    1. Collect app IDs from Steam featured / genre / SteamSpy endpoints
    2. Fetch full app details for each ID (with rate limiting)
    3. Upsert into SQLite
    4. Build the Chroma vector index

Estimated runtime: 30–60 minutes for ~2 000 games (Steam API rate limit:
~200 requests per 5 minutes → ~1.5 s sleep between calls).
"""

import os
import sys
import time
import json
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

# Ensure project root is on sys.path so that `data_layer` imports work
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from data_layer.sqlite_db import GameDB
from data_layer.vector_store import VectorStore

# ---------------------------------------------------------------------------
#  Constants
# ---------------------------------------------------------------------------

STEAM_API_BASE = "https://store.steampowered.com/api"
STEAMSPY_URL = "https://steamspy.com/api.php?request=all"
REQUEST_DELAY = 1.5          # seconds between Steam API calls
TARGET_COUNT = 2000          # approximate number of games to collect
CC = "cn"                    # country code for pricing
LANG = "zh-cn"               # language for descriptions


# ---------------------------------------------------------------------------
#  App‑ID collectors
# ---------------------------------------------------------------------------

def collect_featured() -> set[int]:
    """Fetch app IDs from Steam featured categories."""
    ids: set[int] = set()
    try:
        resp = requests.get(f"{STEAM_API_BASE}/featuredcategories", timeout=30)
        data = resp.json()
        for category in data.values():
            if isinstance(category, dict) and "items" in category:
                for item in category["items"]:
                    if "id" in item:
                        ids.add(item["id"])
        print(f"  [featured]  collected {len(ids)} IDs")
    except Exception as exc:
        print(f"  [featured]  WARNING — {exc}")
    return ids


def collect_steamspy() -> set[int]:
    """Fetch the top‑played / top‑owned list from SteamSpy (free, no key needed)."""
    ids: set[int] = set()
    try:
        resp = requests.get(STEAMSPY_URL, timeout=60)
        data = resp.json()
        # SteamSpy returns {appid: {…}, …} — keys are app IDs
        for key in data:
            try:
                ids.add(int(key))
            except ValueError:
                pass
        print(f"  [steamspy]  collected {len(ids)} IDs")
    except Exception as exc:
        print(f"  [steamspy]  WARNING — {exc}")
    return ids


def collect_all_appids() -> list[int]:
    """Combine multiple sources and return a deduplicated, shuffled list."""
    print("Collecting app IDs from multiple sources …")
    all_ids = collect_featured() | collect_steamspy()

    # Limit to TARGET_COUNT but preserve variety
    id_list = list(all_ids)[:TARGET_COUNT]
    print(f"Total unique IDs: {len(all_ids)} — using {len(id_list)} for this run.\n")
    return id_list


# ---------------------------------------------------------------------------
#  App‑detail fetcher
# ---------------------------------------------------------------------------

def fetch_app_detail(appid: int) -> Optional[dict]:
    """
    GET appdetails for *appid*, returning a flat dict ready for DB insert,
    or None if the request fails / data is missing.
    """
    url = f"{STEAM_API_BASE}/appdetails?appids={appid}&cc={CC}&l={LANG}"
    try:
        resp = requests.get(url, timeout=20)
        if resp.status_code != 200:
            return None
        data = resp.json()
        # Steam wraps the result in {appid_str: {success: bool, data: {…}}}
        app_data = data.get(str(appid), {})
        if not app_data.get("success"):
            return None
        detail = app_data["data"]

        # --- parse price --------------------------------------------------
        price_raw = detail.get("price_overview", {})
        price_cny = 0.0
        if price_raw and price_raw.get("final", 0) > 0:
            price_cny = round(price_raw["final"] / 100.0, 2)

        # --- parse review score -------------------------------------------
        # Some apps don't have review data
        review_score = 0.0
        review_count = 0
        if "metacritic" in detail:
            # Metacritic score (0–100) → normalize to 0.0–1.0
            review_score = detail["metacritic"].get("score", 0) / 100.0

        # --- parse tags ---------------------------------------------------
        genre_list = [g.get("description", "") for g in detail.get("genres", [])]
        tags_str = ",".join(genre_list) if genre_list else ""

        # --- multiplayer detection ----------------------------------------
        categories = detail.get("categories", [])
        is_multi = any(
            c.get("id") in (1, 49)  # 1=Multi-player, 49=gifting (not multiplayer)
            # Correct known multiplayer category ids:
            or c.get("description", "") in ("Multi-player", "在线游戏", "多人")
            for c in categories
        )
        # More reliable multiplayer check
        is_multi = any(
            c.get("id") == 1 or "multi" in c.get("description", "").lower()
            for c in categories
        )

        # --- release date ------------------------------------------------
        release_raw = detail.get("release_date", {})
        release_date = release_raw.get("date", "") if not release_raw.get("coming_soon") else ""

        # --- description --------------------------------------------------
        short_desc = detail.get("short_description", "")

        # --- header image -------------------------------------------------
        header_img = detail.get("header_image", "")

        return {
            "steam_appid": appid,
            "name": detail.get("name", f"App {appid}"),
            "description": short_desc,
            "price_cny": price_cny,
            "review_score": review_score,
            "review_count": review_count,
            "release_date": release_date,
            "tags": tags_str,
            "is_multiplayer": is_multi,
            "header_image": header_img,
            "store_url": f"https://store.steampowered.com/app/{appid}",
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  Steam Game DB Initializer")
    print("=" * 60)

    # ---- 1. Collect app IDs ----------------------------------------------
    appids = collect_all_appids()
    if not appids:
        print("ERROR: No app IDs collected. Check your network / Steam API availability.")
        sys.exit(1)

    # ---- 2. Initialize DB ------------------------------------------------
    db = GameDB()
    db.init_tables()
    print(f"SQLite ready at {db.engine.url}\n")

    # ---- 3. Fetch & store ------------------------------------------------
    success_count = 0
    fail_count = 0

    print("Fetching game details (this will take a while) …\n")
    for i, appid in enumerate(appids):
        detail = fetch_app_detail(appid)
        if detail:
            db.upsert_game(detail)
            success_count += 1
        else:
            fail_count += 1

        # Progress + rate‑limit
        if (i + 1) % 50 == 0 or i == len(appids) - 1:
            print(f"  [{i+1:>4}/{len(appids)}]  ✓ {success_count}  ✗ {fail_count}")
        time.sleep(REQUEST_DELAY)

    print(f"\nDone fetching.  Stored: {success_count}  Skipped: {fail_count}\n")

    # ---- 4. Build vector index -------------------------------------------
    print("Building vector index …")
    all_games = db.get_all_for_vectorize()
    print(f"  {len(all_games)} games to embed")

    vs = VectorStore()
    # Add in batches to avoid memory spikes
    BATCH = 200
    for i in range(0, len(all_games), BATCH):
        batch = all_games[i : i + BATCH]
        vs.add_games(batch)
        print(f"  embedded {min(i + BATCH, len(all_games))}/{len(all_games)}")

    print("\n" + "=" * 60)
    print("  Initialization complete! 🎮")
    print("=" * 60)


if __name__ == "__main__":
    main()
