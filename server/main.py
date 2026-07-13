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
import os
import re
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
MAX_FRAME_BYTES = 3_000_000    # reject camera frames bigger than 3 MB (DoS guard)
MIN_AUDIO_GAP_S = 0.7          # drop audio chunks that arrive faster than this (anti-spam)
REPLY_ECHO_TTL_S = 75          # how long a reply stays "repeatable" without re-triggering Aura
ECHO_OVERLAP = 0.7             # if this share of the heard words are in a recent reply -> it's a repeat
REPLY_GRACE_S = 35             # after Aura speaks, use the smart repeat-check for this long

import unicodedata as _ud


def _normalize(text: str) -> str:
    """Normalize text for repeat-detection: drop diacritics and punctuation and unify
    Arabic alef/ya/ta-marbuta forms. Diacritics are detected by Unicode category
    (nonspacing marks), so base letters are always preserved. Pure-ASCII source."""
    t = "".join(c for c in text.lower() if _ud.category(c) != "Mn")
    t = (t.replace("أ", "ا").replace("إ", "ا")
         .replace("آ", "ا").replace("ى", "ي")
         .replace("ة", "ه").replace("ـ", ""))
    t = re.sub(r"[^\w؀-ۿ\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()

# Arabic diacritics (tashkeel) to strip so "repeat" matching is robust.
_DEAD_UNUSED = None  # removed (superseded by _normalize): re.compile(r"[ؗ-ًؚ-ْٰۖ-ۭ]")


# Correct tashkeel range (marks only, never base letters); ASCII \u escapes to be safe.
_DEAD_UNUSED = None  # removed (superseded by _normalize): re.compile("[ؐ-ًؚ-ٰٟۖ-ۜ۟-۪ۨ-ۭ]")


def _norm(text: str) -> str:
    """Normalize for repeat-detection: drop diacritics/punctuation, unify letter forms."""
    # strip only Arabic diacritic marks (never base letters) — explicit \u ranges
    t = re.sub("[ؐ-ًؚ-ٰٟۖ-ۜ۟-۪ۨ-ۭ]",
               "", text.lower())
    t = (t.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
         .replace("ى", "ي").replace("ة", "ه").replace("ـ", ""))
    t = re.sub(r"[^\w؀-ۿ\s]", " ", t)   # keep letters/digits/Arabic, drop punctuation
    return re.sub(r"\s+", " ", t).strip()

# Optional shared passcode. When AURA_PASSCODE is set, a connection must present the
# same value as ?key=... or it is refused. Unset = open (backward compatible).
AURA_PASSCODE = os.environ.get("AURA_PASSCODE", "").strip()

# Optional production error monitoring — set SENTRY_DSN to activate (crashes reported).
SENTRY_DSN = os.environ.get("SENTRY_DSN", "").strip()
if SENTRY_DSN:
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=0.0, send_default_pii=False)
        log.info("Sentry error monitoring enabled")
    except Exception as e:  # noqa: BLE001
        log.warning("Sentry init failed: %s", e)


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


@app.get("/health")
async def health():
    # lightweight readiness probe for uptime monitors / load balancers
    return JSONResponse({"status": "ok", "connected": hub.main_ws is not None})


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
        self.last_audio_ts = 0.0
        self.busy = False
        self.coach_task: asyncio.Task | None = None
        self.rtsp_task: asyncio.Task | None = None
        # what Aura recently told the wearer to say — so when the wearer repeats
        # those words aloud, we recognize the echo and don't answer again.
        self.recent_replies: list[tuple[set, float]] = []
        self.last_reply_text = ""     # raw text of the most recent reply (for the smart check)
        self.last_reply_ts = 0.0

    def put_frame(self, source: str, jpeg: bytes) -> None:
        self.frames[source] = (jpeg, time.time())

    def recent_frames(self) -> list[tuple[str, bytes]]:
        now = time.time()
        return [(src, jpg) for src, (jpg, ts) in self.frames.items()
                if now - ts < FRAME_MAX_AGE_S]

    def remember_reply(self, text: str) -> None:
        self.last_reply_text = text
        self.last_reply_ts = time.time()
        words = set(_normalize(text).split())
        if words:
            self.recent_replies.append((words, time.time()))
            self.recent_replies = self.recent_replies[-6:]

    def is_repeat(self, text: str) -> bool:
        """True if the wearer is just repeating what Aura recently said."""
        now = time.time()
        heard = set(_normalize(text).split())
        if not heard:
            return False
        for words, ts in self.recent_replies:
            if now - ts > REPLY_ECHO_TTL_S:
                continue
            if len(heard & words) / len(heard) >= ECHO_OVERLAP:
                return True
        return False


hub = Hub()


async def notify(payload: dict, ws=None) -> None:
    # Send to a specific connection when given (so a reply goes back to the exact
    # request that asked); otherwise to the current main connection (background events).
    target = ws or hub.main_ws
    if target is not None:
        try:
            await target.send_text(json.dumps(payload))
        except Exception:  # noqa: BLE001
            pass


async def say(text: str, ws=None) -> None:
    """Send text + spoken audio to the wearer's earbuds."""
    hub.remember_reply(text)   # so the wearer repeating this aloud won't re-trigger Aura
    await notify({"type": "reply_text", "text": text}, ws)
    memory.store("said_by_aura", text)
    try:
        audio = await pipeline.speak(text)
        await notify({"type": "reply_audio",
                      "audio_b64": base64.b64encode(audio).decode(),
                      "mime": pipeline.AUDIO_MIME}, ws)
    except Exception as e:  # noqa: BLE001
        log.warning("TTS failed: %s", e)


# ---------------------------------------------------------------- websocket

@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    # --- authentication: reject connections without the shared passcode ---
    if AURA_PASSCODE and websocket.query_params.get("key", "") != AURA_PASSCODE:
        log.info("rejected connection: bad/missing key")
        await websocket.close(code=4401)   # 4401 = client should prompt for the passcode
        return
    role = websocket.query_params.get("role", "main")
    name = (websocket.query_params.get("name", "phone") or "phone")[:40]
    if role == "main":
        hub.main_ws = websocket
    log.info("connected: role=%s name=%s", role, name)
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(int(msg.get("code") or 1000))
            if msg.get("text"):
                try:
                    data = json.loads(msg["text"])
                except (ValueError, TypeError):
                    continue   # ignore malformed JSON instead of dropping the connection
                if isinstance(data, dict):
                    await handle_json(websocket, role, name, data)
            elif msg.get("bytes") and role == "main":
                await handle_audio_chunk(msg["bytes"], websocket)
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
        b64 = data.get("jpeg_b64") or ""
        if not isinstance(b64, str) or len(b64) > MAX_FRAME_BYTES * 2:
            return
        try:
            raw = base64.b64decode(b64, validate=True)
        except Exception:  # noqa: BLE001
            return
        if not raw or len(raw) > MAX_FRAME_BYTES:
            return
        source = name if role != "main" else "wearer"
        hub.put_frame(source, raw)
        if time.time() - hub.last_scene_ts > SCENE_MEMORY_INTERVAL_S:
            hub.last_scene_ts = time.time()
            asyncio.create_task(remember_scene())
    elif kind == "config" and role == "main":
        hub.proactive = bool(data.get("proactive", hub.proactive))
        hub.ambient = bool(data.get("ambient", hub.ambient))
        hub.answer_all = bool(data.get("answer_all", hub.answer_all))
        if "lesson" in data:
            await set_lesson(str(data["lesson"]).strip()[:80])
        if data.get("rtsp"):
            url = str(data["rtsp"]).strip()
            if url.lower().startswith("rtsp://"):   # only RTSP — blocks SSRF via file://, http://, etc.
                start_rtsp(url)
            else:
                await notify({"type": "error", "text": "Only rtsp:// camera URLs are allowed."}, websocket)


# ---------------------------------------------------------------- audio path

async def handle_audio_chunk(audio: bytes, ws=None) -> None:
    now = time.time()
    # anti-spam + no-pileup: skip chunks that arrive too fast or while one is processing
    if hub.busy or (now - hub.last_audio_ts) < MIN_AUDIO_GAP_S:
        return
    hub.last_audio_ts = now
    hub.busy = True
    try:
        await _process_audio(audio, ws)
    finally:
        hub.busy = False


async def _process_audio(audio: bytes, ws=None) -> None:
    try:
        text = await pipeline.transcribe(audio)
    except Exception as e:  # noqa: BLE001
        log.warning("STT failed: %s", e)
        return
    if not text or len(text) < 2:
        return

    # Repeat guard: the wearer speaks Aura's answers aloud to the person in front of
    # them. When we hear those same words back, it's the wearer repeating — not a new
    # question — so drop it instead of answering again.
    # 1) fast word-overlap check catches verbatim/near-verbatim repeats for free.
    if hub.is_repeat(text):
        log.info("skipped repeat (word-overlap): %s", text)
        return
    # 2) smart check: just after Aura speaks, ask the model whether this is the wearer
    #    relaying the line (robust to numbers-as-words, rewording, partial repeats).
    if hub.last_reply_text and (time.time() - hub.last_reply_ts) < REPLY_GRACE_S:
        if await pipeline.is_repeat_of_reply(hub.last_reply_text, text):
            log.info("skipped repeat (smart): %s", text)
            return

    # "Just talk" mode: everything counts as addressed — no wake word needed.
    addressed = hub.answer_all or pipeline.has_wake_word(text)

    # Session mode (ambient off): unless Aura was addressed or a lesson is running,
    # this utterance is neither stored nor answered — it is simply dropped.
    if not hub.ambient and not addressed and not hub.lesson:
        return

    log.info("heard: %s", text)
    await notify({"type": "transcript", "text": text}, ws)
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

    context = memory.recent_transcript(minutes=2)
    memories = memory.recall(emb, k=5) if emb else []
    try:
        reply = await pipeline.think(text, context, hub.recent_frames(), memories,
                                     detailed=pipeline.wants_detail(text))
    except Exception as e:  # noqa: BLE001
        log.warning("think failed: %s", e)
        return
    if reply:
        log.info("aura: %s", reply)
        await say(reply, ws)


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
