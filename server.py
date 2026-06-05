"""
FastAPI application entry point.

Start with:
    uvicorn server:app --host 0.0.0.0 --port 8000 --reload
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Ensure the project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from core.agent import AgentRunner
from data_layer.auth import AuthManager

# ---------------------------------------------------------------------------
#  App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Steam 游戏推荐 Agent", version="0.1.0")

# Mount the frontend static directory
frontend_dir = PROJECT_ROOT / "frontend"
frontend_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

# Singleton agent (shared across requests)
_agent = AgentRunner()

# Ensure all tables exist
_agent.db.init_tables()

# Auth manager
_auth = AuthManager(_agent.db)


# ---------------------------------------------------------------------------
#  Request / response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    session_id: str
    message: str
    settings: dict = {}   # {budget: int|null, genres: [str], platforms: [str]}


class SyncSteamRequest(BaseModel):
    session_id: str
    steam_id: str = ""          # Steam 64-bit ID or vanity‑URL name
    include_played_free: bool = True


class AuthRequest(BaseModel):
    username: str
    password: str


class AuthResponse(BaseModel):
    success: bool
    token: str = ""
    user: dict = {}
    error: str = ""


# ---------------------------------------------------------------------------
#  Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    """Serve the SPA entry point."""
    index_path = frontend_dir / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse("<h1>Frontend not found</h1>", status_code=404)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


@app.post("/chat")
async def chat(req: ChatRequest):
    """
    Main chat endpoint — returns a Server‑Sent Events (SSE) stream.

    Request body: {"session_id": "...", "message": "..."}
    Response:      text/event-stream with "data: {token}\n\n" chunks
    """

    async def event_stream():
        try:
            async for token in _agent.run_stream(req.session_id, req.message, req.settings):
                # SSE format: each chunk is "data: <text>\n\n"
                # Escape newlines inside token so the SSE parser doesn't break
                safe = token.replace("\n", "\\n")
                yield f"data: {safe}\n\n"
                # Explicitly yield control so uvicorn flushes each chunk
                # rather than buffering multiple SSE events into one TCP packet.
                import asyncio
                await asyncio.sleep(0)
            yield "data: [DONE]\n\n"
        except Exception as e:
            # Catch any unhandled error so the SSE stream doesn't crash silently
            yield f"data: \\n❌ 服务端错误：{e}\\n\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
#  Authentication endpoints
# ---------------------------------------------------------------------------

@app.post("/api/auth/register", response_model=AuthResponse)
async def auth_register(req: AuthRequest):
    """Register a new user account."""
    user, error = _auth.register(req.username, req.password)
    if error:
        return AuthResponse(success=False, error=error)
    return AuthResponse(
        success=True,
        token=user.token,
        user=user.to_dict(),
    )


@app.post("/api/auth/login", response_model=AuthResponse)
async def auth_login(req: AuthRequest):
    """Log in and receive an auth token."""
    user, error = _auth.login(req.username, req.password)
    if error:
        return AuthResponse(success=False, error=error)
    return AuthResponse(
        success=True,
        token=user.token,
        user=user.to_dict(),
    )


@app.post("/api/auth/logout")
async def auth_logout(token: str = ""):
    """Clear the auth token (logout)."""
    ok = _auth.logout(token)
    return {"success": ok}


@app.get("/api/auth/me", response_model=AuthResponse)
async def auth_me(token: str = ""):
    """Validate token and return current user info."""
    if not token:
        return AuthResponse(success=False, error="未提供认证 token")
    user = _auth.get_user_by_token(token)
    if not user:
        return AuthResponse(success=False, error="token 无效或已过期")
    return AuthResponse(success=True, token=token, user=user.to_dict())


# ---------------------------------------------------------------------------
#  Sidebar support endpoints
# ---------------------------------------------------------------------------

# Steam Web API helpers
STEAM_API_KEY = os.getenv("STEAM_API_KEY", "")
STEAM_API_BASE = "https://api.steampowered.com"


async def _resolve_steam_id(steam_id: str) -> str | None:
    """Resolve a vanity‑URL name → 64‑bit Steam ID.  Pass‑through if already numeric."""
    import httpx
    if not steam_id.strip():
        return None
    # Already a 64‑bit numeric ID?
    if steam_id.strip().isdigit() and len(steam_id.strip()) == 17:
        return steam_id.strip()

    # Try resolving as a vanity name
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{STEAM_API_BASE}/ISteamUser/ResolveVanityURL/v1/",
                params={"key": STEAM_API_KEY, "vanityurl": steam_id.strip()},
            )
            data = resp.json().get("response", {})
            if data.get("success") == 1:
                return data.get("steamid")
    except Exception:
        pass
    return None


async def _fetch_steam_profile(steam_id: str) -> dict:
    """Fetch a player's public profile, owned games, and recently played games."""
    import httpx

    result = {"steam_id": steam_id, "success": False, "persona_name": "",
              "avatar_url": "", "game_count": 0, "total_playtime_min": 0,
              "games": [], "recent_games": [], "top_genres": [],
              "recent_playtime_analysis": "", "message": ""}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # 1. Player summary
            summary_resp = await client.get(
                f"{STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v2/",
                params={"key": STEAM_API_KEY, "steamids": steam_id},
            )
            players = summary_resp.json().get("response", {}).get("players", [])
            if not players:
                result["message"] = "未找到该 Steam 用户的公开信息"
                return result
            player = players[0]
            result["persona_name"] = player.get("personaname", "Unknown")
            result["avatar_url"] = player.get("avatarfull", "")

            # 2. Owned games
            games_resp = await client.get(
                f"{STEAM_API_BASE}/IPlayerService/GetOwnedGames/v1/",
                params={"key": STEAM_API_KEY, "steamid": steam_id,
                        "include_appinfo": True, "include_played_free_games": True},
            )
            games_data = games_resp.json().get("response", {})
            owned = games_data.get("games", [])
            result["game_count"] = games_data.get("game_count", len(owned))
            result["games"] = sorted(owned, key=lambda g: g.get("playtime_forever", 0), reverse=True)[:50]

            # 3. Recently played (last 2 weeks)
            recent_resp = await client.get(
                f"{STEAM_API_BASE}/IPlayerService/GetRecentlyPlayedGames/v1/",
                params={"key": STEAM_API_KEY, "steamid": steam_id, "count": 10},
            )
            recent_data = recent_resp.json().get("response", {})
            recent = recent_data.get("games", [])
            result["recent_games"] = recent

            # 4. Analyse
            total_playtime = sum(g.get("playtime_forever", 0) for g in owned)
            result["total_playtime_min"] = total_playtime

            # Match owned games against our local DB to extract genre tags
            local_genres: dict[str, int] = {}
            owned_names = {g.get("name", "").lower(): g for g in owned if g.get("name")}
            try:
                all_local = _agent.db.filter_games(limit=500)
                for local_g in all_local:
                    if local_g.name.lower() in owned_names:
                        for tag in local_g.tags.split(","):
                            tag = tag.strip()
                            if tag:
                                local_genres[tag] = local_genres.get(tag, 0) + 1
            except Exception:
                pass

            top_genres = sorted(local_genres.items(), key=lambda x: x[1], reverse=True)[:5]
            result["top_genres"] = [g[0] for g in top_genres]

            # Build analysis text
            parts = []
            if recent:
                recent_names = [g.get("name", "") for g in recent[:3] if g.get("name")]
                if recent_names:
                    parts.append(f"最近在玩：{'、'.join(recent_names)}")
                    recent_hours = sum(g.get("playtime_2weeks", 0) for g in recent) // 60
                    if recent_hours > 0:
                        parts.append(f"近两周游戏时间约 {recent_hours} 小时")
            if top_genres:
                parts.append(f"偏好类型：{'、'.join([g[0] for g in top_genres[:3]])}")
            if result["game_count"] > 0:
                parts.append(f"游戏库共 {result['game_count']} 款")
            result["recent_playtime_analysis"] = "；".join(parts) if parts else "分析完成"

            result["success"] = True
            result["message"] = f"数据同步完成 (库中 {result['game_count']} 款游戏)"
            return result

    except Exception as e:
        result["message"] = f"Steam API 请求失败：{e}"
        return result


@app.post("/api/sync-steam")
async def sync_steam(req: SyncSteamRequest):
    """
    Sync real Steam library data via the Steam Web API.

    - If *steam_id* is provided (64‑bit ID or vanity‑URL name), fetches the
      player's public profile, owned games, and recently‑played data.
    - If *steam_id* is empty, falls back to simulated data with a hint to
      provide a real Steam ID for personalised results.
    """
    import asyncio

    # ---- Real Steam API path ------------------------------------------------
    if req.steam_id.strip():
        resolved = await _resolve_steam_id(req.steam_id.strip())
        if not resolved:
            return {
                "success": False,
                "game_count": 0,
                "top_genres": [],
                "recent_playtime_analysis": "",
                "message": f"无法解析 Steam ID「{req.steam_id}」，请确认输入正确（64位数字ID或个人资料URL中的自定义名称）",
            }

        profile = await _fetch_steam_profile(resolved)
        if profile["success"]:
            _agent.set_steam_profile(req.session_id, profile)
        return profile

    # ---- Fallback: no Steam ID provided ------------------------------------
    await asyncio.sleep(0.8)

    return {
        "success": True,
        "game_count": 142,
        "top_genres": ["FPS", "开放世界RPG", "独立游戏", "策略"],
        "recent_playtime_analysis": (
            "⚠️ 这是模拟数据。如需个性化分析，请在弹窗中输入你的 Steam ID。\n"
            "→ 打开 Steam 客户端 → 点击右上角头像 → 账户详情 → 复制 Steam ID"
        ),
        "message": "模拟同步完成 (142 款游戏) — 输入 Steam ID 获取真实数据",
        "steam_id_required": True,
    }


@app.get("/api/agent-status")
async def agent_status(session_id: str = ""):
    """
    Return the agent's **real** internal status for the sidebar panel.

    The status is updated by AgentRunner during each phase of request
    processing (analyzing → searching → generating → idle).
    """
    if session_id:
        return _agent.get_status(session_id)

    # No session — return idle defaults
    return {
        "data_source": "本地数据库",
        "preference_bias": "",
        "confidence": 50,
        "agent_thought": "等待了解你的游戏喜好...",
        "phase": "idle",
    }


# ---------------------------------------------------------------------------
#  Image upload
# ---------------------------------------------------------------------------

from fastapi import UploadFile, File, Form as FastForm
import uuid as _uuid
import shutil as _shutil

@app.post("/api/upload-image")
async def upload_image_real(
    file: UploadFile = File(...),
    session_id: str = FastForm(""),
):
    """Upload a game screenshot and return its public URL."""
    sid = session_id or "default"
    upload_dir = PROJECT_ROOT / "data" / "uploads" / sid
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Generate a unique filename
    ext = file.filename.rsplit(".", 1)[-1] if file.filename and "." in file.filename else "png"
    filename = f"{_uuid.uuid4().hex[:12]}.{ext}"
    filepath = upload_dir / filename

    with open(filepath, "wb") as f:
        _shutil.copyfileobj(file.file, f)

    url = f"/api/uploads/{sid}/{filename}"
    return {"success": True, "url": url, "filename": filename}


@app.get("/api/uploads/{session_id}/{filename}")
async def serve_upload(session_id: str, filename: str):
    """Serve an uploaded image."""
    from fastapi.responses import JSONResponse
    filepath = PROJECT_ROOT / "data" / "uploads" / session_id / filename
    if not filepath.exists():
        return JSONResponse({"error": "File not found"}, status_code=404)
    return FileResponse(filepath)


@app.get("/api/agent-stream/{session_id}")
async def agent_stream(session_id: str):
    """
    SSE endpoint that pushes agent status changes in real time.

    Replaces the old polling approach.  The frontend opens this alongside
    the chat SSE stream and receives instant status updates.
    """
    import json as _json

    async def event_stream():
        queue = _agent.subscribe_status(session_id)
        try:
            while True:
                try:
                    status = await asyncio.wait_for(queue.get(), timeout=30)
                    payload = _json.dumps(status, ensure_ascii=False)
                    yield f"event: status\ndata: {payload}\n\n"
                except asyncio.TimeoutError:
                    # Send heartbeat every 30s to keep connection alive
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _agent.unsubscribe_status(session_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/db-stats")
async def db_stats():
    """Health‑check endpoint that returns database statistics."""
    from data_layer.schema import Game
    try:
        session = _agent.db.session
        total = session.query(Game).count()
        has_price = session.query(Game).filter(Game.price_cny > 0).count()
        has_review = session.query(Game).filter(Game.review_score > 0).count()
        has_image = session.query(Game).filter(Game.header_image != "").count()
        has_tags = session.query(Game).filter(Game.tags != "").count()

        # Top tags
        all_games = session.query(Game).filter(Game.tags != "").limit(500).all()
        tag_counts: dict[str, int] = {}
        for g in all_games:
            for tag in g.tags.split(","):
                t = tag.strip()
                if t:
                    tag_counts[t] = tag_counts.get(t, 0) + 1
        top_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        return {
            "total_games": total,
            "with_price": has_price,
            "with_review": has_review,
            "with_image": has_image,
            "with_tags": has_tags,
            "top_tags": [{"tag": t, "count": c} for t, c in top_tags],
            "status": "healthy" if total > 100 else "low_data",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/api/tool-results/{session_id}")
async def tool_results(session_id: str):
    """
    Return structured game data from the most recent tool run.

    Called by the frontend after the SSE streaming ends so it can
    render rich game cards with cover images, tags, prices, etc.
    """
    games = _agent.get_tool_results(session_id)
    return {"games": games}


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
