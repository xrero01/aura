"""Aura server: phone app (PWA) + extra cameras -> AI -> voice in your earbuds.

Run:  uvicorn main:app --host 0.0.0.0 --port 8000
Tunnel for phone HTTPS:  ngrok http 8000   (or cloudflared)

Connections:
  /ws            main wearer app (audio + camera + replies)
  /ws?role=cam&name=helmet   extra camera (second phone, sends frames only)
  RTSP cameras: from the app, enter rtsp:// URL -> server ingests it (needs opencv).
"""

import asyncio
import base64
import json
import logging
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response

import lessons
import memory
import pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("aura")

app = FastAPI(title="Aura")
CLIENT = Path(__file__).parent.parent / "client" / "index.html"

SCENE_MEMORY_INTERVAL_S = 30   # how often to describe & remember what cameras see
COACH_INTERVAL_S = 20          # how often the lesson coach considers speaking
FRAME_MAX_AGE_S = 20           # ignore camera frames older than this


# ---------------------------------------------------------------- PWA assets

MANIFEST = {
    "name": "Aura", "short_name": "Aura", "start_url": "/", "display": "fullscreen",
    "background_color": "#0b0f14", "theme_color": "#0b0f14",
    "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}],
}
ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    '<rect width="100" height="100" rx="22" fill="#0b0f14"/>'
    '<circle cx="50" cy="50" r="26" fill="none" stroke="#3ddc84" stroke-width="6"/>'
    '<circle cx="50" cy="50" r="9" fill="#3ddc84"/></svg>'
)
SW_JS = "self.addEventListener('fetch', () => {});"


@app.get("/")
async def index():
    return FileResponse(CLIENT)


@app.get("/manifest.json")
async def manifest():
    return JSONResponse(MANIFEST)


@app.get("/icon.svg")
async def icon():
    return Response(ICON_SVG, media_type="image/svg+xml")


@app.get("/sw.js")
async def sw():
    return Response(SW_JS, media_type="application/javascript")


@app.get("/lessons.json")
async def lesson_names():
    return JSONResponse(lessons.names())


# ---------------------------------------------------------------- hub state

class Hub:
    """Single-user hub: one main wearer, any number of extra cameras."""

    def __init__(self) -> None:
        self.main_ws: WebSocket | None = None
        self.frames: dict[str, tuple[bytes, float]] = {}   # source -> (jpeg, ts)
        self.proactive = True   # full-assistant mode: speaks up on its own by default
        # ambient=False is "session mode": nothing is stored or answered unless the
        # user addresses Aura or a lesson is running. Legal-safe default for places
        # where recording others is restricted (see RESEARCH-AND-ROADMAP.md part 3).
        self.ambient = True
        # answer_all=True: no wake word needed — every utterance gets an answer.
        # The phone UI enables this by default ("Just talk" mode).
        self.answer_all = False
        self.lesson = ""
        self.coach_history: list[str] = []
        self.last_scene_ts = 0.0
        self.coach_task: asyncio.Task | None = None
        self.rtsp_task: asyncio.Task | None = None

    def put_frame(self, source: str, jpeg: bytes) -> None:
        self.frames[source] = (jpeg, time.time())

    def recent_frames(self) -> list[tuple[str, bytes]]:
        now = time.time()
        return [(src, jpg) for src, (jpg, ts) in self.frames.items()
                if now - ts < FRAME_MAX_AGE_S]


hub = Hub()


async def notify(payload: dict) -> None:
    if hub.main_ws is not None:
        try:
            await hub.main_ws.send_text(json.dumps(payload))
        except Exception:  # noqa: BLE001
            pass


async def say(text: str) -> None:
    """Send text + spoken audio to the wearer's earbuds."""
    await notify({"type": "reply_text", "text": text})
    memory.store("said_by_aura", text)
    try:
        audio = await pipeline.speak(text)
        await notify({"type": "reply_audio",
                      "audio_b64": base64.b64encode(audio).decode(),
                      "mime": pipeline.AUDIO_MIME})
    except Exception as e:  # noqa: BLE001
        log.warning("TTS failed: %s", e)


# ---------------------------------------------------------------- websocket

@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    role = websocket.query_params.get("role", "main")
    name = websocket.query_params.get("name", "phone")
    if role == "main":
        hub.main_ws = websocket
    log.info("connected: role=%s name=%s", role, name)
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(int(msg.get("code") or 1000))
            if msg.get("text"):
                await handle_json(websocket, role, name, json.loads(msg["text"]))
            elif msg.get("bytes") and role == "main":
                await handle_audio_chunk(msg["bytes"])
    except WebSocketDisconnect:
        log.info("disconnected: role=%s name=%s", role, name)
        if role == "main":
            hub.main_ws = None
            stop_lesson()
        else:
            hub.frames.pop(name, None)


async def handle_json(websocket: WebSocket, role: str, name: str, data: dict) -> None:
    kind = data.get("type")
    if kind == "frame":
        source = name if role != "main" else "wearer"
        hub.put_frame(source, base64.b64decode(data["jpeg_b64"]))
        if time.time() - hub.last_scene_ts > SCENE_MEMORY_INTERVAL_S:
            hub.last_scene_ts = time.time()
            asyncio.create_task(remember_scene())
    elif kind == "config" and role == "main":
        hub.proactive = bool(data.get("proactive", hub.proactive))
        hub.ambient = bool(data.get("ambient", hub.ambient))
        hub.answer_all = bool(data.get("answer_all", hub.answer_all))
        if "lesson" in data:
            await set_lesson(str(data["lesson"]).strip())
        if data.get("rtsp"):
            start_rtsp(str(data["rtsp"]).strip())


# ---------------------------------------------------------------- audio path

async def handle_audio_chunk(audio: bytes) -> None:
    try:
        text = await pipeline.transcribe(audio)
    except Exception as e:  # noqa: BLE001
        log.warning("STT failed: %s", e)
        return
    if not text or len(text) < 2:
        return

    # "Just talk" mode: everything counts as addressed — no wake word needed.
    addressed = hub.answer_all or pipeline.has_wake_word(text)

    # Session mode (ambient off): unless Aura was addressed or a lesson is running,
    # this utterance is neither stored nor answered — it is simply dropped.
    if not hub.ambient and not addressed and not hub.lesson:
        return

    log.info("heard: %s", text)
    await notify({"type": "transcript", "text": text})
    try:
        emb = await pipeline.embed(text)
    except Exception:  # noqa: BLE001
        emb = None
    memory.store("heard", text, emb)
    if not addressed:
        if hub.lesson:
            return  # in lesson mode the coach loop decides when to speak
        if not hub.proactive:
            return
        # Gate on the NEWEST utterance only — judging a whole minute of context
        # makes the gate re-fire on already-answered questions.
        if not await pipeline.should_speak(text):
            return

    context = memory.recent_transcript(minutes=3)
    memories = memory.recall(emb, k=5) if emb else []
    reply = await pipeline.think(context, hub.recent_frames(), memories,
                                 detailed=pipeline.wants_detail(text))
    if reply:
        log.info("aura: %s", reply)
        await say(reply)


# ---------------------------------------------------------------- lesson coach

async def set_lesson(lesson: str) -> None:
    ended, history = hub.lesson, list(hub.coach_history)
    stop_lesson()
    if ended and history and not lesson:
        # Lesson finished: speak a progress summary and remember it.
        try:
            summary = await pipeline.summarize_lesson(ended, history)
            if summary:
                try:
                    emb = await pipeline.embed(f"{ended} lesson summary: {summary}")
                except Exception:  # noqa: BLE001
                    emb = None
                memory.store("lesson_summary", f"({ended}) {summary}", emb)
                await say(summary)
        except Exception as e:  # noqa: BLE001
            log.warning("lesson summary failed: %s", e)
    hub.lesson = lesson
    if lesson:
        hub.coach_history = []
        hub.coach_task = asyncio.create_task(coach_loop())
        log.info("lesson started: %s", lesson)


def stop_lesson() -> None:
    if hub.coach_task:
        hub.coach_task.cancel()
        hub.coach_task = None
    hub.lesson = ""


async def coach_loop() -> None:
    await say(f"Okay, starting your {hub.lesson} lesson. I'm watching — let's begin.")
    try:
        while hub.lesson and hub.main_ws is not None:
            await asyncio.sleep(COACH_INTERVAL_S)
            frames = hub.recent_frames()
            if not frames:
                continue
            try:
                tip = await pipeline.coach(hub.lesson, memory.recent_transcript(minutes=2),
                                           frames, hub.coach_history,
                                           template=lessons.template_for(hub.lesson))
            except Exception as e:  # noqa: BLE001
                log.warning("coach failed: %s", e)
                continue
            if tip and tip.upper().strip() != "WAIT":
                hub.coach_history.append(tip)
                log.info("coach: %s", tip)
                await say(tip)
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------- extra cameras

def start_rtsp(url: str) -> None:
    if hub.rtsp_task:
        hub.rtsp_task.cancel()
    hub.rtsp_task = asyncio.create_task(rtsp_loop(url))


async def rtsp_loop(url: str) -> None:
    """Ingest a WiFi/IP/action camera (RTSP) as an extra frame source."""
    try:
        import cv2  # optional dep: pip install opencv-python-headless
    except ImportError:
        await notify({"type": "error",
                      "text": "RTSP needs opencv: pip install opencv-python-headless"})
        return
    cap = await asyncio.to_thread(cv2.VideoCapture, url)
    if not cap.isOpened():
        await notify({"type": "error", "text": f"Could not open camera: {url}"})
        return
    log.info("rtsp connected: %s", url)
    try:
        while True:
            ok, frame = await asyncio.to_thread(cap.read)
            if ok:
                ok2, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
                if ok2:
                    hub.put_frame("rtsp-cam", buf.tobytes())
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        pass
    finally:
        cap.release()


# ---------------------------------------------------------------- scene memory

async def remember_scene() -> None:
    """Describe what the cameras see and store it as 'seen' memories."""
    for label, jpeg in hub.recent_frames():
        try:
            desc = await pipeline.describe_frame(jpeg)
            if desc:
                emb = await pipeline.embed(desc)
                memory.store("seen", f"({label}) {desc}", emb)
                log.info("seen [%s]: %s", label, desc)
        except Exception as e:  # noqa: BLE001
            log.warning("scene memory failed: %s", e)
