"""
Pipecat Voice Interface Server — Hub
WebSocket endpoint for real-time voice conversations on port 8300.
Jules transcription mode available at /jules.

Split modules:
  voice_common.py     — Shared state (ConnectionManager, AudioBuffer, NC helpers, transport getters)
  routes/vault.py     — Vault inbox save, audio save, transcribe-and-save
  routes/tts.py       — Text-to-speech via Piper
  routes/video.py     — Video frame extraction, preview, process-moments, streaming
  routes/stt.py       — Batch video transcription (Qwen3-ASR)
  ws.py               — WebSocket handler + pipeline functions
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware

from src.voice_common import manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Pipecat server on port 8300...")

    # Initialize pipeline
    from src.pipeline import VoicePipeline
    app.state.pipeline = VoicePipeline()
    if await app.state.pipeline.initialize():
        logger.info("Pipeline ready")
    else:
        logger.warning("Pipeline partially initialized - some features may not work")

    yield

    logger.info("Shutting down...")
    if hasattr(app.state, 'pipeline'):
        await app.state.pipeline.cleanup()
    for client_id in list(manager.active_connections.keys()):
        manager.disconnect(client_id)


# ── App + middleware ─────────────────────────────────────────────────────────

app = FastAPI(
    title="Pipecat Voice Interface",
    version="0.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Include route modules ────────────────────────────────────────────────────

from src.routes.vault import router as vault_router
from src.routes.tts import router as tts_router
from src.routes.video import router as video_router
from src.routes.stt import router as stt_router

app.include_router(vault_router)
app.include_router(tts_router)
app.include_router(video_router)
app.include_router(stt_router)


# ── WebSocket endpoint ───────────────────────────────────────────────────────

from src.ws import websocket_endpoint
app.add_api_websocket_route("/ws", websocket_endpoint)


# ── Static/meta routes ───────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "ok", "service": "pipecat-voice", "port": 8300}


@app.get("/health")
async def health():
    return {"status": "healthy", "websocket": "active"}


@app.get("/test", response_class=HTMLResponse)
async def test_page():
    with open("/app/test.html") as f:
        return f.read()


@app.get("/jules", response_class=HTMLResponse)
async def jules_page():
    with open("/app/jules.html") as f:
        return f.read()


@app.get("/julian-icon.png")
async def julian_icon():
    with open("/app/julian-icon.png", "rb") as f:
        return Response(content=f.read(), media_type="image/png")


@app.get("/julian-icon-512.png")
async def julian_icon_512():
    with open("/app/julian-icon-512.png", "rb") as f:
        return Response(content=f.read(), media_type="image/png")


@app.get("/manifest.json")
async def manifest():
    with open("/app/manifest.json") as f:
        return Response(content=f.read(), media_type="application/manifest+json")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8300)
