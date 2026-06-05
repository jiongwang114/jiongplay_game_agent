"""Steam store real‑time price check tool."""

import os
from typing import Optional

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from data_layer.sqlite_db import GameDB
from data_layer.schema import Game

STEAM_API_BASE = "https://store.steampowered.com/api"


class SteamStoreTool:
    """
    Look up the current Steam price / discount for a game.

    First tries a fuzzy match in the local DB, then fetches live pricing
    from the Steam store API.
    """

    def __init__(self, db: GameDB):
        self._db = db

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    def _fuzzy_find(self, name: str) -> Optional[Game]:
        """Find the best local match for *name* using SQL LIKE."""
        session: Session = self._db.session
        return session.query(Game).filter(
            Game.name.like(f"%{name}%")
        ).first()

    # ------------------------------------------------------------------
    #  Main
    # ------------------------------------------------------------------

    async def run(self, game_name: str) -> dict:
        """
        Return live pricing for *game_name*.

        Returns:
            {"name": …, "price": …, "discount": …, "store_url": …}
            or {"error": "未找到该游戏"}
        """
        # 1. Local match
        game = self._fuzzy_find(game_name)
        appid = game.steam_appid if game else None

        if not appid:
            return {"error": f"未找到游戏「{game_name}」，请确认名称是否正确"}

        # 2. Live price from Steam API
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{STEAM_API_BASE}/appdetails",
                    params={"appids": appid, "cc": "cn"},
                )
                resp.raise_for_status()
                data = resp.json()

            app_data = data.get(str(appid), {})
            if not app_data.get("success"):
                # Fall back to cached data
                return {
                    "name": game.name,
                    "price": game.price_cny,
                    "discount": None,
                    "store_url": game.store_url,
                    "note": "实时价格暂不可用，显示为本地缓存价格",
                }

            detail = app_data["data"]
            price_info = detail.get("price_overview", {})

            return {
                "name": detail.get("name", game.name),
                "price": round(price_info.get("final", 0) / 100.0, 2),
                "original_price": round(price_info.get("initial", 0) / 100.0, 2),
                "discount": price_info.get("discount_percent", 0),
                "store_url": f"https://store.steampowered.com/app/{appid}",
            }

        except Exception:
            # Fall back to cached data on any error
            return {
                "name": game.name,
                "price": game.price_cny,
                "discount": None,
                "store_url": game.store_url,
                "note": "无法获取实时价格，显示为本地缓存价格",
            }
