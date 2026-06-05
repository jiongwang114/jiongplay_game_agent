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


# ---------------------------------------------------------------------------
#  Request / response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    session_id: str
    message: str


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
            async for token in _agent.run_stream(req.session_id, req.message):
                # SSE format: each chunk is "data: <text>\n\n"
                # Escape newlines inside token so the SSE parser doesn't break
                safe = token.replace("\n", "\\n")
                yield f"data: {safe}\n\n"
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
#  Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
