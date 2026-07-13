"""Real-time Gemini Live bridge — Aura 2.0 (Path A).

Bridges a phone WebSocket to the Gemini Live API so the phone streams microphone
audio (16 kHz PCM) + camera frames and plays back Gemini's spoken audio (24 kHz PCM)
in real time. Google Search grounding is enabled so factual answers are looked up,
not guessed. This runs on a SEPARATE endpoint (/live) so the classic app is untouched.

Requires:  GEMINI_API_KEY  (set on the server)  and the `google-genai` package.
Optional env: AURA_LIVE_MODEL, AURA_LIVE_VOICE, AURA_LANGUAGE.
"""

import asyncio
import base64
import json
import logging
import os

log = logging.getLogger("aura.live")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
# AI Studio native-audio Live model. Override with AURA_LIVE_MODEL if Google renames it.
LIVE_MODEL = os.environ.get("AURA_LIVE_MODEL", "gemini-live-2.5-flash-preview").strip()
LIVE_VOICE = os.environ.get("AURA_LIVE_VOICE", "Aoede").strip()   # prebuilt native voice
AURA_LANGUAGE = (os.environ.get("AURA_LANGUAGE", "Arabic").strip() or "Arabic")
LIVE_LANG_CODE = os.environ.get("AURA_LIVE_LANG_CODE", "ar-XA").strip()  # spoken-output language

SYSTEM_INSTRUCTION = f"""You are Aura, a warm, natural, real-time companion whispering into the
user's earbud while seeing through their phone camera, like a real assistant sitting with them.

- Speak ONLY in {AURA_LANGUAGE}, in natural spoken {AURA_LANGUAGE}, no matter what language you
  hear. Never mix languages.
- Sound like a real, warm human — short, casual, natural rhythm; never a robot or a textbook.
- ANSWER CORRECTLY. For any fact you are not sure of (prices, news, people, places, "is it open",
  anything current), use Google Search to check BEFORE answering. If you still don't know, say so
  briefly and naturally — never guess or invent names, numbers, prices, or dates.
- USE THE LIVE CAMERA to answer about what the user is looking at: read labels, prices, and text
  exactly as they appear. If you can't see it clearly, say so — never invent what you see.
- INTERPRETER: if people around the user speak another language and the user asks what they said
  or to translate, tell the user in {AURA_LANGUAGE} what was said.
- Help the user close sales when asked: discover the client's real need first, handle objections
  by reframing to value/ROI, read buying signals, trial-close, and lock a concrete next step.
- Keep replies concise — speak the actual answer to say, no preamble like "the answer is"."""


def _config() -> dict:
    """Live session config as a plain dict (the SDK coerces it)."""
    return {
        "response_modalities": ["AUDIO"],
        "system_instruction": SYSTEM_INSTRUCTION,
        "tools": [{"google_search": {}}],   # grounding: look facts up instead of guessing
        "speech_config": {
            "language_code": LIVE_LANG_CODE,
            "voice_config": {"prebuilt_voice_config": {"voice_name": LIVE_VOICE}},
        },
        "input_audio_transcription": {},
        "output_audio_transcription": {},
    }


async def bridge(ws) -> None:
    """Bridge an already-accepted phone WebSocket to a Gemini Live session."""
    if not GEMINI_API_KEY:
        await ws.send_text(json.dumps({"type": "error",
                                       "text": "GEMINI_API_KEY is not set on the server yet."}))
        return
    try:
        from google import genai            # lazy import so the classic app never breaks
        from google.genai import types
    except Exception as e:  # noqa: BLE001
        await ws.send_text(json.dumps({"type": "error",
                                       "text": f"google-genai not installed: {e}"}))
        return

    client = genai.Client(api_key=GEMINI_API_KEY)
    log.info("opening Gemini Live session (model=%s)", LIVE_MODEL)
    try:
        async with client.aio.live.connect(model=LIVE_MODEL, config=_config()) as session:
            await ws.send_text(json.dumps({"type": "ready"}))

            async def phone_to_gemini():
                while True:
                    msg = await ws.receive()
                    if msg.get("type") == "websocket.disconnect":
                        break
                    if msg.get("bytes"):
                        # mic audio: 16-bit PCM, 16 kHz, mono, little-endian
                        await session.send_realtime_input(
                            audio=types.Blob(data=msg["bytes"], mime_type="audio/pcm;rate=16000"))
                    elif msg.get("text"):
                        try:
                            data = json.loads(msg["text"])
                        except (ValueError, TypeError):
                            continue
                        if data.get("type") == "frame":
                            jpeg = base64.b64decode(data.get("jpeg_b64", "") or "")
                            if jpeg:
                                await session.send_realtime_input(
                                    video=types.Blob(data=jpeg, mime_type="image/jpeg"))

            async def gemini_to_phone():
                async for m in session.receive():
                    data = getattr(m, "data", None)
                    if data:
                        await ws.send_bytes(data)   # spoken audio: 24 kHz PCM -> phone plays it
                    sc = getattr(m, "server_content", None)
                    if sc is not None:
                        it = getattr(sc, "input_transcription", None)
                        if it and getattr(it, "text", None):
                            await ws.send_text(json.dumps({"type": "heard", "text": it.text}))
                        ot = getattr(sc, "output_transcription", None)
                        if ot and getattr(ot, "text", None):
                            await ws.send_text(json.dumps({"type": "aura", "text": ot.text}))
                        if getattr(sc, "interrupted", False):
                            await ws.send_text(json.dumps({"type": "interrupted"}))

            t1 = asyncio.create_task(phone_to_gemini())
            t2 = asyncio.create_task(gemini_to_phone())
            done, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
    except Exception as e:  # noqa: BLE001
        log.warning("live session error: %s", e)
        try:
            await ws.send_text(json.dumps({"type": "error", "text": f"live session error: {e}"}))
        except Exception:  # noqa: BLE001
            pass
