"""AI pipeline with swappable providers.

Environment:
  AURA_PROVIDER   openai (default) | gemini | openrouter  -> STT, TTS, embeddings, brain
  AURA_BRAIN      openai | gemini | anthropic | openrouter -> override just the brain
  OPENAI_API_KEY / GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENROUTER_API_KEY

OpenRouter notes: any model on openrouter.ai works for the brain (set
AURA_OPENROUTER_MODEL, e.g. "anthropic/claude-sonnet-4.5" or "google/gemini-2.5-flash").
OpenRouter has no TTS/embeddings, so voice uses free local edge-tts
(pip install edge-tts) and memory uses a built-in local embedding.
STT runs through an audio-capable model (default google/gemini-2.5-flash);
webm audio from the phone is converted with ffmpeg (install ffmpeg on the server).

Everything AI lives in this file. main.py never talks to a model directly.
"""

import asyncio
import base64
import hashlib
import io
import json
import math
import os
import struct
import subprocess
import tempfile

import httpx

PROVIDER = os.environ.get("AURA_PROVIDER", "openai").lower()
BRAIN_PROVIDER = os.environ.get("AURA_BRAIN", PROVIDER).lower()

# Force spoken replies into one language (e.g. "Arabic"). Empty = match the speaker.
AURA_LANGUAGE = os.environ.get("AURA_LANGUAGE", "").strip()


def _lang(system: str) -> str:
    """Append a language directive to a spoken-output system prompt."""
    if AURA_LANGUAGE:
        return (system + f"\n\nIMPORTANT: Always reply ONLY in {AURA_LANGUAGE}, "
                f"in natural {AURA_LANGUAGE}, no matter what language you hear.")
    return system

OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")

OPENAI_BRAIN = os.environ.get("AURA_OPENAI_MODEL", "gpt-4o-mini")
GEMINI_BRAIN = os.environ.get("AURA_GEMINI_MODEL", "gemini-2.5-flash")
ANTHROPIC_BRAIN = os.environ.get("AURA_ANTHROPIC_MODEL", "claude-sonnet-4-5")
OPENROUTER_BRAIN = os.environ.get("AURA_OPENROUTER_MODEL", "openai/gpt-4o-mini")
OPENROUTER_STT = os.environ.get("AURA_OPENROUTER_STT_MODEL", "google/gemini-2.5-flash")
GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"
GEMINI_TTS_VOICE = "Kore"
OPENAI_TTS_VOICE = "nova"
EDGE_TTS_VOICE = os.environ.get("AURA_EDGE_VOICE", "en-US-AriaNeural")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

#: MIME type of the audio bytes speak() returns (client needs this to play them)
AUDIO_MIME = "audio/wav" if PROVIDER == "gemini" else "audio/mpeg"

_http = httpx.AsyncClient(timeout=60)

# --------------------------------------------------------------- prompts

WAKE_WORDS = ("hey aura", "hey ora", "hey or a", "aura")

DETAIL_WORDS = ("everything", "full guide", "explain", "in detail", "teach me",
                "tell me all", "step by step", "history of", "how does", "how do",
                # long-form creative / detailed requests (English)
                "poem", "poetry", "story", "song", "write", "describe", "details",
                "tell me about", "who is", "what is this", "product", "review",
                # Arabic equivalents (transcribed Arabic speech)
                "قصيدة", "قصيده", "اكتب", "اشرح", "قصة", "قصه", "أغنية", "اغنية",
                "بالتفصيل", "كل شيء", "كل شى", "تفاصيل", "من هو", "ما هذا", "منتج",
                "احك", "احكي", "صف", "وصف", "شرح", "قصائد", "بيت شعر")

GATE_PROMPT = """You are the interruption gate for an ambient AI assistant worn by the user.
You see the last thing transcribed from the user's surroundings. Decide if the assistant
should speak into the user's ear right now.

Reply SPEAK only if:
- the user directly addressed the assistant, OR
- someone asked the user a question they may need help answering, OR
- there is clearly urgent/valuable information the user would want immediately.

Otherwise reply STAY_SILENT. Be very conservative: interrupting is costly, silence is free.
Reply with exactly one word: SPEAK or STAY_SILENT."""

BRAIN_PROMPT = """You are Aura, a sharp, warm AI companion whispering into the user's earbud
while sitting with them in real life. You receive: the recent transcript of the conversation
around the user, LIVE photos from their camera(s) of what is in front of them right now, and
relevant memories from earlier.

How to answer:
- Simple factual question or quick help: 1-3 natural spoken sentences. Don't ramble.
- Creative or rich request — a POEM, a story, a song, a toast, a detailed explanation, a
  product description, "tell me everything", "describe this": give the COMPLETE thing.
  Write the FULL poem (several lines), the full explanation, the full product rundown.
  Never cut a poem or story short, never say "here's a short version".
- USE THE CAMERA. The photos show exactly what the user is looking at. When they say
  "this", "that", "who is this", "what is this", "what am I looking at", or ask about a
  product/object/person/place/text in view, look carefully at the image and answer about
  the ACTUAL thing you see — name it, read its label, describe its details, its price tag
  if visible, who the person appears to be, etc. Weave real details from the image in.
- If someone in the conversation asks the user a question, help the user answer it well.
- To recall: use the memories provided, including [seen] memories of what the camera saw.
- Address the user directly and naturally. Never mention being an AI, never explain your
  reasoning, never use markdown or bullet points — this is spoken aloud into an ear."""

COACH_PROMPT = """You are Aura, a real-time {lesson} instructor speaking into your student's earbud.
You receive camera views of what the student sees/does and a transcript of recent audio.
{template}
Give ONE short spoken instruction, correction, or encouragement — the single most useful
thing right now. HARD LIMIT: 2 sentences. Plain speech only — never markdown, lists,
headings or questions back to the student. Be concrete and specific to what you observe.
If there is nothing new or useful to say since your last instruction, reply with exactly: WAIT"""

SCENE_PROMPT = """Describe this camera frame in 1-2 factual sentences for a memory log.
Focus on objects, their locations, people, text, and anything a person might later want
to find again (keys, phone, wallet, documents, doors, addresses). No commentary."""

SUMMARY_PROMPT = """You are Aura wrapping up a {lesson} practice session that has now ENDED.
Below are the instructions you gave during it. Speak a short encouraging wrap-up to the
student (3-5 sentences): what they worked on, what improved, and the ONE thing to drill
next time. This is a closing statement — do not ask questions or continue the lesson.
Plain speech only, no markdown, no lists."""


# --------------------------------------------------------------- small logic

def has_wake_word(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in WAKE_WORDS)


def wants_detail(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in DETAIL_WORDS)


def _image_parts_openai(frames: list[tuple[str, bytes]]) -> list[dict]:
    out = []
    for label, jpeg in frames:
        b64 = base64.b64encode(jpeg).decode()
        out.append({"type": "text", "text": f"Camera '{label}':"})
        out.append({"type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"}})
    return out


# --------------------------------------------------------------- brain (chat) layer

async def llm_chat(system: str, text: str, frames: list[tuple[str, bytes]] | None = None,
                   max_tokens: int = 200) -> str:
    """One chat call routed to the configured brain provider. Frames are (label, jpeg)."""
    frames = frames or []
    if BRAIN_PROVIDER == "gemini":
        return await _gemini_chat(system, text, frames, max_tokens)
    if BRAIN_PROVIDER == "anthropic":
        return await _anthropic_chat(system, text, frames, max_tokens)
    if BRAIN_PROVIDER == "openrouter":
        return await _openrouter_chat(system, text, frames, max_tokens)
    return await _openai_chat(system, text, frames, max_tokens)


async def _openrouter_chat(system, text, frames, max_tokens, extra_parts=None) -> str:
    content: list[dict] = [{"type": "text", "text": text}]
    content += _image_parts_openai(frames)
    if extra_parts:
        content += extra_parts
    r = await _http.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {OPENROUTER_KEY}"},
        json={"model": OPENROUTER_BRAIN, "max_tokens": max_tokens,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": content}]},
    )
    r.raise_for_status()
    return (r.json()["choices"][0]["message"]["content"] or "").strip()


async def _openai_chat(system, text, frames, max_tokens) -> str:
    content: list[dict] = [{"type": "text", "text": text}]
    content += _image_parts_openai(frames)
    r = await _http.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_KEY}"},
        json={"model": OPENAI_BRAIN, "max_tokens": max_tokens,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": content}]},
    )
    r.raise_for_status()
    return (r.json()["choices"][0]["message"]["content"] or "").strip()


async def _gemini_chat(system, text, frames, max_tokens) -> str:
    parts: list[dict] = [{"text": text}]
    for label, jpeg in frames:
        parts.append({"text": f"Camera '{label}':"})
        parts.append({"inline_data": {"mime_type": "image/jpeg",
                                      "data": base64.b64encode(jpeg).decode()}})
    r = await _http.post(
        f"{GEMINI_URL}/{GEMINI_BRAIN}:generateContent?key={GEMINI_KEY}",
        json={"system_instruction": {"parts": [{"text": system}]},
              "contents": [{"role": "user", "parts": parts}],
              "generationConfig": {"maxOutputTokens": max_tokens}},
    )
    r.raise_for_status()
    cands = r.json().get("candidates", [])
    if not cands:
        return ""
    return "".join(p.get("text", "") for p in cands[0]["content"]["parts"]).strip()


async def _anthropic_chat(system, text, frames, max_tokens) -> str:
    content: list[dict] = [{"type": "text", "text": text}]
    for label, jpeg in frames:
        content.append({"type": "text", "text": f"Camera '{label}':"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.b64encode(jpeg).decode()}})
    r = await _http.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01"},
        json={"model": ANTHROPIC_BRAIN, "max_tokens": max_tokens, "system": system,
              "messages": [{"role": "user", "content": content}]},
    )
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json()["content"]).strip()


# --------------------------------------------------------------- STT

def _sniff_audio_format(b: bytes, fallback: str) -> str:
    if b[:4] == b"\x1aE\xdf\xa3":
        return "webm"
    if b[:3] == b"ID3" or (len(b) > 1 and b[0] == 0xFF and (b[1] & 0xE0) == 0xE0):
        return "mp3"
    if b[:4] == b"RIFF":
        return "wav"
    return fallback


def _to_wav(audio: bytes) -> bytes:
    """Convert any audio (webm/opus etc.) to 16k mono WAV using ffmpeg."""
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(audio)
        src = f.name
    dst = src + ".wav"
    try:
        subprocess.run(["ffmpeg", "-y", "-i", src, "-ar", "16000", "-ac", "1", dst],
                       capture_output=True, check=True)
        with open(dst, "rb") as f:
            return f.read()
    finally:
        for p in (src, dst):
            try:
                os.unlink(p)
            except OSError:
                pass


async def transcribe(audio_bytes: bytes, mimetype: str = "audio/webm") -> str:
    if PROVIDER == "openrouter":
        fmt = _sniff_audio_format(audio_bytes, mimetype.split("/")[-1].split(";")[0])
        if fmt not in ("wav", "mp3"):   # OpenRouter audio input accepts wav/mp3
            audio_bytes = await asyncio.to_thread(_to_wav, audio_bytes)
            fmt = "wav"
        r = await _http.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {OPENROUTER_KEY}"},
            json={"model": OPENROUTER_STT, "max_tokens": 500,
                  "messages": [{"role": "user", "content": [
                      {"type": "text",
                       "text": "Transcribe this audio exactly. Output only the spoken words. "
                               "If there is no speech, output nothing."},
                      {"type": "input_audio",
                       "input_audio": {"data": base64.b64encode(audio_bytes).decode(),
                                       "format": fmt}}]}]},
        )
        r.raise_for_status()
        return (r.json()["choices"][0]["message"]["content"] or "").strip()
    if PROVIDER == "gemini":
        r = await _http.post(
            f"{GEMINI_URL}/{GEMINI_BRAIN}:generateContent?key={GEMINI_KEY}",
            json={"contents": [{"role": "user", "parts": [
                {"text": "Transcribe this audio exactly. Output only the spoken words, "
                         "or an empty response if there is no speech."},
                {"inline_data": {"mime_type": mimetype.split(";")[0],
                                 "data": base64.b64encode(audio_bytes).decode()}}]}],
                  "generationConfig": {"maxOutputTokens": 500}},
        )
        r.raise_for_status()
        cands = r.json().get("candidates", [])
        if not cands:
            return ""
        return "".join(p.get("text", "") for p in cands[0]["content"]["parts"]).strip()
    # openai whisper
    files = {"file": (f"chunk.{mimetype.split('/')[-1].split(';')[0]}", io.BytesIO(audio_bytes)),
             "model": (None, "whisper-1")}
    r = await _http.post("https://api.openai.com/v1/audio/transcriptions",
                         headers={"Authorization": f"Bearer {OPENAI_KEY}"}, files=files)
    r.raise_for_status()
    return r.json().get("text", "").strip()


# --------------------------------------------------------------- TTS

def _pcm_to_wav(pcm: bytes, rate: int = 24000) -> bytes:
    hdr = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(pcm), b"WAVE", b"fmt ",
                      16, 1, 1, rate, rate * 2, 2, 16, b"data", len(pcm))
    return hdr + pcm


async def _edge_speak(text: str) -> bytes:
    """Free local TTS via Microsoft Edge voices (pip install edge-tts). Returns MP3."""
    import edge_tts
    out = io.BytesIO()
    async for chunk in edge_tts.Communicate(text, EDGE_TTS_VOICE).stream():
        if chunk["type"] == "audio":
            out.write(chunk["data"])
    return out.getvalue()


async def speak(text: str) -> bytes:
    """Text -> spoken audio bytes (MIME type = AUDIO_MIME)."""
    if PROVIDER == "openrouter":
        return await _edge_speak(text)
    if PROVIDER == "gemini":
        r = await _http.post(
            f"{GEMINI_URL}/{GEMINI_TTS_MODEL}:generateContent?key={GEMINI_KEY}",
            json={"contents": [{"role": "user", "parts": [{"text": text}]}],
                  "generationConfig": {
                      "responseModalities": ["AUDIO"],
                      "speechConfig": {"voiceConfig": {
                          "prebuiltVoiceConfig": {"voiceName": GEMINI_TTS_VOICE}}}}},
        )
        r.raise_for_status()
        b64 = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
        return _pcm_to_wav(base64.b64decode(b64))
    r = await _http.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {OPENAI_KEY}"},
        json={"model": "tts-1", "voice": OPENAI_TTS_VOICE, "input": text},
    )
    r.raise_for_status()
    return r.content


# --------------------------------------------------------------- embeddings

def _local_embed(text: str, dim: int = 512) -> list[float]:
    """Dependency-free embedding: hashed character trigrams. Good enough for
    personal-memory recall when the provider (OpenRouter) has no embeddings API."""
    v = [0.0] * dim
    t = " " + text.lower() + " "
    for i in range(len(t) - 2):
        h = int.from_bytes(hashlib.md5(t[i:i + 3].encode()).digest()[:4], "big")
        v[h % dim] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


async def embed(text: str) -> list[float]:
    if PROVIDER == "openrouter":
        return _local_embed(text)
    if PROVIDER == "gemini":
        r = await _http.post(
            f"{GEMINI_URL}/gemini-embedding-001:embedContent?key={GEMINI_KEY}",
            json={"content": {"parts": [{"text": text}]}},
        )
        r.raise_for_status()
        return r.json()["embedding"]["values"]
    r = await _http.post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {OPENAI_KEY}"},
        json={"model": "text-embedding-3-small", "input": text},
    )
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


# --------------------------------------------------------------- assistant roles

async def should_speak(latest_utterance: str) -> bool:
    reply = await llm_chat(GATE_PROMPT, latest_utterance, max_tokens=4)
    return "SPEAK" in reply.upper()


async def think(transcript_tail: str, frames: list[tuple[str, bytes]], memories: list[str],
                detailed: bool = False) -> str:
    text = ""
    if memories:
        text += "Relevant memories:\n" + "\n".join(f"- {m}" for m in memories) + "\n\n"
    text += f"Recent transcript:\n{transcript_tail}"
    return await llm_chat(_lang(BRAIN_PROMPT), text, frames, max_tokens=1500 if detailed else 320)


async def coach(lesson: str, transcript_tail: str, frames: list[tuple[str, bytes]],
                last_instructions: list[str], template: str = "") -> str:
    text = ("Your previous instructions:\n"
            + "\n".join(f"- {i}" for i in last_instructions[-5:])
            + f"\n\nRecent audio transcript:\n{transcript_tail or '(silence)'}")
    system = COACH_PROMPT.format(lesson=lesson, template=template)
    return await llm_chat(_lang(system), text, frames, max_tokens=100)


async def describe_frame(frame_jpeg: bytes) -> str:
    return await llm_chat(SCENE_PROMPT, "Describe this frame.", [("view", frame_jpeg)],
                          max_tokens=80)


async def summarize_lesson(lesson: str, instructions: list[str]) -> str:
    text = "Instructions given:\n" + "\n".join(f"- {i}" for i in instructions)
    return await llm_chat(_lang(SUMMARY_PROMPT.format(lesson=lesson)), text, max_tokens=250)
