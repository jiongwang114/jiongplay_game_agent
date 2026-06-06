"""
FastAPI application entry point.

Start with:
    uvicorn server:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
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
from core.trace import TraceStore
from data_layer.auth import AuthManager

# ---------------------------------------------------------------------------
#  App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Steam 游戏推荐 智能体", version="0.1.0")

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
    token: str = ""             # Auth token — used to persist Steam data to user record


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
#  User data persistence endpoints (link session → account, sync settings)
# ---------------------------------------------------------------------------

class LinkUserRequest(BaseModel):
    session_id: str = ""
    token: str = ""

@app.post("/api/user/link")
async def user_link(req: LinkUserRequest):
    """
    Link a browser session to a user account, and restore all persisted data.

    Call this right after login or on page load with a valid token.
    Returns past conversations, Steam profile, preferences, and settings
    so the frontend can restore the user's state.
    """
    if not req.token or not req.session_id:
        return {"success": False, "error": "缺少 session_id 或 token"}
    user = _auth.get_user_by_token(req.token)
    if not user:
        return {"success": False, "error": "token 无效或已过期"}
    result = _agent.link_user(req.session_id, user.id)
    # Attach user info for frontend
    result["user"] = user.to_dict()
    return result


@app.post("/api/user/unlink")
async def user_unlink(session_id: str = ""):
    """Unlink a session from its user account (called on logout)."""
    if session_id:
        _agent.unlink_user(session_id)
    return {"success": True}


class SaveSettingsRequest(BaseModel):
    session_id: str = ""
    token: str = ""
    settings: dict = {}

@app.post("/api/user/settings")
async def user_save_settings(req: SaveSettingsRequest):
    """
    Save user settings (budget, genres, platforms) to the DB.
    This way settings follow the user across devices, not just localStorage.
    """
    if not req.token:
        return {"success": False, "error": "未登录"}
    user = _auth.get_user_by_token(req.token)
    if not user:
        return {"success": False, "error": "token 无效"}
    _agent.save_user_settings_to_db(user.id, req.settings)
    # Also link the session for future persistence
    if req.session_id:
        _agent.link_user(req.session_id, user.id)
    return {"success": True}


# ---------------------------------------------------------------------------
#  Sidebar support endpoints
# ---------------------------------------------------------------------------

# Steam Web API helpers
STEAM_API_KEY = os.getenv("STEAM_API_KEY", "")
STEAM_API_BASE = "https://api.steampowered.com"

# ---- Logging ----
import logging
_logger = logging.getLogger("steam_agent")
_logger.setLevel(logging.DEBUG)
if not _logger.handlers:
    _ch = logging.StreamHandler()
    _ch.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    _logger.addHandler(_ch)


async def _resolve_steam_id(steam_id: str) -> str | None:
    """Resolve a vanity‑URL name → 64‑bit Steam ID.  Pass‑through if already numeric."""
    import httpx
    if not steam_id.strip():
        return None
    # Already a 64‑bit numeric ID?
    if steam_id.strip().isdigit() and len(steam_id.strip()) == 17:
        _logger.info(f"Steam ID 已是64位格式: {steam_id.strip()}")
        return steam_id.strip()

    # Try resolving as a vanity name
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            _logger.info(f"正在解析 vanity URL: {steam_id.strip()}")
            resp = await client.get(
                f"{STEAM_API_BASE}/ISteamUser/ResolveVanityURL/v1/",
                params={"key": STEAM_API_KEY, "vanityurl": steam_id.strip()},
            )
            data = resp.json().get("response", {})
            _logger.info(f"Vanity URL 解析响应: {data}")
            if data.get("success") == 1:
                resolved = data.get("steamid")
                _logger.info(f"Vanity URL 解析成功 → Steam ID: {resolved}")
                return resolved
            else:
                _logger.warning(f"Vanity URL 解析失败: {data.get('message', '未知错误')}")
    except Exception as e:
        _logger.error(f"Vanity URL 解析网络错误: {e}")
    return None


async def _fetch_steam_profile(steam_id: str) -> dict:
    """Fetch a player's public profile, owned games, and recently played games."""
    import httpx

    result = {"steam_id": steam_id, "success": False, "persona_name": "",
              "avatar_url": "", "game_count": 0, "total_playtime_min": 0,
              "games": [], "recent_games": [], "top_genres": [],
              "recent_playtime_analysis": "", "message": "",
              # [新增] 第一期：暴露已获取但未利用的 Steam 字段
              "timecreated": 0,           # 账户创建时间（Unix 时间戳）
              "loccountrycode": "",       # 所在国家/地区代码
              "account_age_days": 0,      # 账户年龄（天）
              "top_games_by_playtime": [], # 按游玩时长排名的 Top 10 游戏 [{name, playtime_hours}]
              }

    if not STEAM_API_KEY:
        result["message"] = "Steam API Key 未配置，请在 .env 中设置 STEAM_API_KEY"
        _logger.error(result["message"])
        return result

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # 1. Player summary
            _logger.info(f"正在获取玩家摘要: steam_id={steam_id}")
            summary_resp = await client.get(
                f"{STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v2/",
                params={"key": STEAM_API_KEY, "steamids": steam_id},
            )
            summary_json = summary_resp.json()
            _logger.info(f"GetPlayerSummaries 响应状态: {summary_resp.status_code}, 内容: {str(summary_json)[:200]}")
            players = summary_json.get("response", {}).get("players", [])
            if not players:
                result["message"] = "未找到该 Steam 用户的公开信息（请确保你的 Steam 个人资料隐私设置为「公开」）"
                _logger.warning(result["message"])
                return result
            player = players[0]
            result["persona_name"] = player.get("personaname", "Unknown")
            result["avatar_url"] = player.get("avatarfull", "")
            # [新增] 提取账户创建时间并计算年龄
            timecreated = player.get("timecreated", 0)
            if timecreated:
                import datetime as _dt
                result["timecreated"] = timecreated
                result["account_age_days"] = (_dt.datetime.now().timestamp() - timecreated) // 86400
            result["loccountrycode"] = player.get("loccountrycode", "")
            _logger.info(f"玩家昵称: {result['persona_name']}, 国家: {result['loccountrycode']}, 账户年龄: {result['account_age_days']}天")

            # 2. Owned games
            _logger.info(f"正在获取游戏库: steam_id={steam_id}")
            games_resp = await client.get(
                f"{STEAM_API_BASE}/IPlayerService/GetOwnedGames/v1/",
                params={"key": STEAM_API_KEY, "steamid": steam_id,
                        "include_appinfo": True, "include_played_free_games": True},
            )
            games_json = games_resp.json()
            games_data = games_json.get("response", {})
            owned = games_data.get("games", [])
            result["game_count"] = games_data.get("game_count", len(owned))
            result["games"] = sorted(owned, key=lambda g: g.get("playtime_forever", 0), reverse=True)[:50]
            # [新增] 构建 Top 10 游玩时长排名（小时取整）
            result["top_games_by_playtime"] = [
                {"name": g.get("name", "未知"), "playtime_hours": round(g.get("playtime_forever", 0) / 60, 1)}
                for g in result["games"][:10]
            ]
            _logger.info(f"游戏库获取成功: {result['game_count']} 款游戏, Top1: {result['top_games_by_playtime'][0]['name'] if result['top_games_by_playtime'] else 'N/A'}")

            # 3. Recently played (last 2 weeks)
            _logger.info(f"正在获取最近游玩记录: steam_id={steam_id}")
            recent_resp = await client.get(
                f"{STEAM_API_BASE}/IPlayerService/GetRecentlyPlayedGames/v1/",
                params={"key": STEAM_API_KEY, "steamid": steam_id, "count": 10},
            )
            recent_data = recent_resp.json().get("response", {})
            recent = recent_data.get("games", [])
            result["recent_games"] = recent
            _logger.info(f"最近游玩: {len(recent)} 款")

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
            # [新增] 账户年龄信息
            if result["account_age_days"] and result["account_age_days"] > 0:
                years = result["account_age_days"] // 365
                parts.append(f"Steam 账号已注册约 {years} 年" if years > 0 else f"Steam 账号注册不到 1 年")
            if result["loccountrycode"]:
                parts.append(f"地区：{result['loccountrycode']}")
            # [新增] 最常玩游戏 Top 3
            top3 = result["top_games_by_playtime"][:3]
            if top3:
                top3_str = "、".join(f"{g['name']}({g['playtime_hours']}h)" for g in top3)
                parts.append(f"最常玩：{top3_str}")
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
            result["is_simulated"] = False
            result["message"] = f"数据同步完成 (库中 {result['game_count']} 款游戏)"
            _logger.info(f"Steam 真实同步完成: {result['persona_name']}, {result['game_count']} 款游戏")
            return result

    except httpx.ConnectError as e:
        result["message"] = f"无法连接到 Steam API 服务器（网络不通或被屏蔽）：{e}"
        _logger.error(result["message"])
        return result
    except httpx.TimeoutException as e:
        result["message"] = f"Steam API 请求超时，请检查网络或稍后重试：{e}"
        _logger.error(result["message"])
        return result
    except Exception as e:
        result["message"] = f"Steam API 请求失败：{e}"
        _logger.error(f"Steam API 异常: {type(e).__name__}: {e}")
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
    # ---- Real Steam API path ------------------------------------------------
    if req.steam_id.strip():
        _logger.info(f"收到真实 Steam 同步请求: steam_id={req.steam_id.strip()}, session={req.session_id}")

        # Resolve vanity URL → 64-bit ID first, so we can check uniqueness
        resolved = await _resolve_steam_id(req.steam_id.strip())
        if not resolved:
            _logger.warning(f"Steam ID 解析失败: {req.steam_id.strip()}")
            return {
                "success": False,
                "is_simulated": False,
                "game_count": 0,
                "top_genres": [],
                "recent_playtime_analysis": "",
                "message": f"无法解析 Steam ID「{req.steam_id}」，请确认输入正确（64位数字ID或个人资料URL中的自定义名称）",
            }

        # If user is logged in, link the session so Steam data persists to DB
        if req.token:
            user = _auth.get_user_by_token(req.token)
            if user:
                _agent.link_user(req.session_id, user.id)
                # [修改] 原因：确保一个 Steam ID 只能绑定一个用户，防止多用户共用同一 Steam 账号
                existing_owner = _agent.db.get_user_by_steam_id(resolved)
                if existing_owner and existing_owner.id != user.id:
                    _logger.warning(f"Steam ID {resolved} 已被用户 {existing_owner.username} 绑定")
                    return {
                        "success": False,
                        "is_simulated": False,
                        "game_count": 0,
                        "top_genres": [],
                        "recent_playtime_analysis": "",
                        "message": f"该 Steam ID 已被用户「{existing_owner.username}」绑定，一个 Steam ID 只能绑定一个账号",
                    }

        profile = await _fetch_steam_profile(resolved)
        if profile["success"]:
            _agent.set_steam_profile(req.session_id, profile)
            _logger.info(f"Steam 真实同步成功: session={req.session_id}")
        else:
            _logger.warning(f"Steam 真实同步失败: {profile.get('message')}")
        return profile

    # ---- Fallback: no Steam ID provided ------------------------------------
    _logger.info(f"模拟数据同步: session={req.session_id} (未提供 Steam ID)")
    await asyncio.sleep(0.8)

    return {
        "success": True,
        "is_simulated": True,
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


@app.get("/api/steam-check")
async def steam_check():
    """
    Verify Steam API key connectivity.

    Returns whether the Steam Web API is reachable and the key is valid.
    Call this on startup or from the settings panel to diagnose sync issues.
    """
    import httpx

    result = {
        "api_key_configured": bool(STEAM_API_KEY),
        "api_reachable": False,
        "api_key_valid": False,
        "detail": "",
    }

    if not STEAM_API_KEY:
        result["detail"] = "STEAM_API_KEY 未在 .env 中配置"
        return result

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Use GetPlayerSummaries with a known public Steam ID as a smoke test
            test_steam_id = "76561197960287930"  # Gabe Newell's Steam ID (public)
            resp = await client.get(
                f"{STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v2/",
                params={"key": STEAM_API_KEY, "steamids": test_steam_id},
            )
            data = resp.json()
            response_obj = data.get("response", {})

            if resp.status_code == 200 and "players" in response_obj:
                result["api_reachable"] = True
                result["api_key_valid"] = True
                result["detail"] = "Steam API 连通正常，Key 有效 ✓"
                _logger.info("Steam API 连通性检查: 正常")
            elif resp.status_code == 403:
                result["api_reachable"] = True
                result["api_key_valid"] = False
                result["detail"] = "Steam API Key 无效（403 Forbidden），请到 https://steamcommunity.com/dev/apikey 重新申请"
                _logger.warning("Steam API 连通性检查: Key 无效 (403)")
            else:
                result["api_reachable"] = True
                result["api_key_valid"] = False
                result["detail"] = f"Steam API 返回异常状态码 {resp.status_code}: {str(data)[:200]}"
                _logger.warning(f"Steam API 连通性检查: 状态码 {resp.status_code}")
    except httpx.ConnectError as e:
        result["detail"] = f"无法连接 Steam API 服务器（网络不通或被屏蔽）: {e}"
        _logger.error(f"Steam API 连通性检查: 连接失败 - {e}")
    except httpx.TimeoutException:
        result["detail"] = "Steam API 连接超时，请检查网络"
        _logger.error("Steam API 连通性检查: 超时")
    except Exception as e:
        result["detail"] = f"检查失败: {type(e).__name__}: {e}"
        _logger.error(f"Steam API 连通性检查: {e}")

    return result


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
#  Agent Trace API — 执行流程可视化
# ---------------------------------------------------------------------------


@app.get("/api/trace/{session_id}")
async def trace_latest(session_id: str):
    """
    Return the most recent execution trace for *session_id*.

    The trace contains a tree of spans (intent detection, tool calls,
    LLM generation, etc.) with timing and input/output summaries.
    Called by the frontend when the agent status returns to "idle"
    to render the execution flow panel.
    """
    traces = TraceStore.get_by_session(session_id, limit=1)
    if not traces:
        return {"trace": None, "message": "暂无执行记录"}
    return {"trace": traces[0].to_dict()}


@app.get("/api/trace/history")
async def trace_history(session_id: str = "", limit: int = 20):
    """
    Return a list of recent trace summaries.

    If *session_id* is provided, returns only that session's traces.
    Otherwise returns the global recent list.
    """
    if session_id:
        traces = TraceStore.get_by_session(session_id, limit=min(limit, 50))
    else:
        traces = TraceStore.get_recent(limit=min(limit, 50))
    return {
        "traces": [t.to_summary() for t in traces],
        "count": len(traces),
    }


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
