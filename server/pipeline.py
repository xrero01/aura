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
        return (system + f"\n\nIMPORTANT: Always reply ONLY in {AURA_LANGUAGE}, in natural "
                f"{AURA_LANGUAGE}, no matter what language you hear. Use only {AURA_LANGUAGE} "
                f"words — never mix in English words or letters.")
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

How you sound — this matters most: talk like a real, warm human in an actual conversation, not
like a robot, a textbook, or a formal announcement. Keep it short and casual, the way a friend
actually talks out loud — everyday words, contractions, light natural colloquial touches, and
match the other person's own dialect, tone and energy. A little warmth or a natural reaction is
great when it fits ("آه"، "أكيد"، "تمام"، "بصراحة"), but stay brief and never gushy. For a
simple fact, answer the easy casual way a friend would — like "طوكيو، عاصمة اليابان" or just
"طوكيو" — not a stiff line like "عاصمة اليابان هي...". Vary your phrasing so nothing sounds
canned, and let a real personality come through. Sound like a person, not a paragraph.

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
- NEVER invent facts. If you don't know something, or it's about very recent or future
  events you cannot know, say so briefly and naturally — never guess or make up names,
  numbers, prices, dates, or product releases.
- Only describe what is ACTUALLY in the photos. If there is no image, or it doesn't clearly
  show what the user means, say you can't see it or ask them to aim the camera — never
  invent objects, titles, labels, or prices that aren't really there.
- Don't accept false or trick premises. If a question assumes something untrue (the sun is
  cold, a horse has five legs, a fake quote from a famous person), gently correct it instead
  of playing along.
- Safety first: never give medication doses or anything that could cause harm. For sleep,
  medicine, or health questions keep it general and suggest seeing a doctor. Refuse requests
  to hurt people or make weapons. If someone sounds in distress, be caring and gently point
  them toward support.
- Address the user directly and naturally. Don't announce that you're an AI unprompted, but
  if they directly ask whether you're human, be honest and warm — never claim to be a real
  person. Never explain your reasoning, never use markdown or bullet points — this is spoken
  aloud into an ear.
- YOU ARE A PROMPTER: the user says your words out loud to the person in front of them, so give
  the actual line to say — phrased the natural, human way a real person would say it out loud —
  not a meta description of the answer. Skip flat framing like "the answer is" or "you could
  say", but DO keep it warm and natural, not clipped or robotic. For "12 times 12" just say it
  the way a person would ("مية وأربعة وأربعين"). For a poem or story, give only the poem or story.
- CRITICAL: Respond ONLY to the user's LATEST message (shown under "The user just said").
  Anything under "Earlier context" is only background for pronouns/topic — NEVER re-answer
  those older questions and NEVER repeat a previous answer. One reply, for the latest thing."""

# Expert B2B-services sales knowledge, injected into the brain when the user asks a
# sales question (so "practice & advice" answers are sharp, not generic).
SALES_KNOWLEDGE = (
    "The user works in B2B services sales. When they ask about selling, closing, deals, "
    "clients, pricing, objections, discovery, negotiation, proposals, or follow-up, answer as a "
    "world-class B2B sales expert. Be specific and practical: give the exact technique AND example "
    "sentences they can actually say — never generic advice like 'be polite' or 'emphasize quality'.\n"
    "Principles to apply:\n"
    "- Discovery before pitching: ask open questions to uncover the client's real pain, what it "
    "costs their business, who else decides, budget, and timeline (BANT). Listen about 70%.\n"
    "- Sell value and ROI, never features; quantify the cost of leaving the problem unsolved.\n"
    "- Objections — acknowledge, ask a question to find the real issue, then reframe. Price too high: "
    "anchor to ROI and the cost of inaction, break price into value per outcome. 'I'll think about it': "
    "surface the real hesitation and agree one small next step. 'Just send info': propose a short "
    "specific call with an agenda. 'We already have a vendor': ask what they wish it did better. "
    "'No budget': quantify the problem's cost and find who controls budget. Talking to a non-decider: "
    "equip them to sell it internally and get a meeting with the real decision-maker.\n"
    "- Watch buying signals (questions about price, onboarding, timeline) and trial-close.\n"
    "- Answer direct questions such as price clearly and confidently, then trial-close.\n"
    "- Close with a clear ask and always lock a concrete next step with a specific date.\n"
    "- Negotiate by anchoring high and never discounting without getting something in return.\n"
    "- For a follow-up message: reference the specific pain discussed, add one new insight or proof, "
    "and propose a specific next step and time; give a short ready-to-send draft when asked."
)

# words that switch the brain into expert-sales mode (English + Arabic)
SALES_WORDS = (
    "sales", "selling", "sell ", "close the deal", "closing", "deal", "client", "customer",
    "prospect", "objection", "pricing", "negotiat", "proposal", "follow up", "follow-up",
    "upsell", "discount", "pitch",
    "مبيعات", "بيع", "أبيع", "عميل", "عملاء", "زبون", "صفقة", "صفقات", "إغلاق", "اغلاق",
    "اعتراض", "تسعير", "خصم", "تفاوض", "التفاوض", "متابعة", "صاحب القرار", "ميزانية العميل",
)

# only attach the live camera image when the question is really about what's in view —
# otherwise we skip the image upload, which makes ordinary answers noticeably faster.
VISUAL_WORDS = (
    "this", "that", "what is", "what am i", "who is", "see", "look", "read", "picture", "photo",
    "camera", "in front", "color", "colour", "label", "price tag", "product",
    "هذا", "هذه", "ذلك", "أمامي", "قدامي", "تشوف", "شوف", "اقرأ", "الصورة", "شكل", "لون",
    "وش هذا", "ما هذا", "ايش هذا", "منتج", "السعر المكتوب",
)

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


REPEAT_GATE = """You are Aura's earbud assistant. A moment ago you told the user a line to say
out loud to the person in front of them. Now you hear the user speaking. Decide: is the user
simply REPEATING or relaying that line to the other person — even if reworded, numbers spoken
as words instead of digits, colloquial, or only part of it — OR is this something NEW (a new
question or statement, from the user or someone else) that needs a fresh answer?
Reply with exactly one word: REPEAT or NEW."""


async def is_repeat_of_reply(reply: str, utterance: str) -> bool:
    """True if the wearer is just relaying Aura's own recent line (robust to numbers spoken
    as words, rewording, and partial repeats — the word-overlap check can't catch those)."""
    msg = f'You told the user to say:\n"{reply}"\n\nNow you hear the user say:\n"{utterance}"'
    try:
        out = await llm_chat(REPEAT_GATE, msg, max_tokens=3)
    except Exception:  # noqa: BLE001
        return False
    return "REPEAT" in out.upper()


async def think(current: str, context: str, frames: list[tuple[str, bytes]],
                memories: list[str], detailed: bool = False) -> str:
    parts = []
    if memories:
        parts.append("Relevant memories:\n" + "\n".join(f"- {m}" for m in memories))
    ctx = (context or "").strip()
    # keep only the lines BEFORE the current utterance as background
    if ctx and ctx != current.strip():
        if current.strip() and ctx.endswith(current.strip()):
            ctx = ctx[: -len(current.strip())].strip()
        if ctx:
            parts.append("Earlier context (background only — do NOT re-answer):\n" + ctx)
    parts.append(f'The user just said:\n"{current}"\n\nReply to THIS only.')
    text = "\n\n".join(parts)
    system = BRAIN_PROMPT
    low = current.lower()
    if any(w in low for w in SALES_WORDS):
        system = BRAIN_PROMPT + "\n\n" + SALES_KNOWLEDGE
    # skip the camera image unless the question is about what's in view (faster replies)
    use_frames = frames if any(w in low for w in VISUAL_WORDS) else []
    return await llm_chat(_lang(system), text, use_frames, max_tokens=1500 if detailed else 220)


async def coach(lesson: str, transcript_tail: str, frames: list[tuple[str, bytes]],
                last_instructions: list[str], template: str = "") -> str:
    text = ("Your previous instructions:\n"
            + "\n".join(f"- {i}" for i in last_instructions[-5:])
            + f"\n\nRecent audio transcript:\n{transcript_tail or '(silence)'}")
    system = COACH_PROMPT.format(lesson=lesson, template=template)
    return await llm_chat(_lang(system), text, frames, max_tokens=100)


# ---- Looki-style life-log: modes, proactive highlights, day recap ----

SCENE_FOCUS = {
    "event": " Focus on people, faces, names, badges, business cards, and social interactions.",
    "fitness": " Focus on the physical activity, movement, exercise, form, effort, and surroundings.",
    "everyday": " Focus on objects and their locations, text, documents, places, and tasks.",
}


async def describe_frame(frame_jpeg: bytes, mode: str = "") -> str:
    prompt = SCENE_PROMPT + SCENE_FOCUS.get(mode, "")
    return await llm_chat(prompt, "Describe this frame.", [("view", frame_jpeg)], max_tokens=80)


MODE_PROMPT = """Classify the user's current situation into exactly ONE word based on the camera
scene and recent audio:
EVENT (meeting people, networking, a party, travelling, out and about),
FITNESS (exercising, gym, running, sport, any physical activity),
EVERYDAY (home, work/desk, errands, ordinary routine).
Reply with only one word: EVENT, FITNESS, or EVERYDAY."""


async def detect_mode(scene: str, transcript_tail: str) -> str:
    text = f"Camera sees:\n{scene or '(nothing clear)'}\n\nRecent audio:\n{transcript_tail or '(quiet)'}"
    try:
        out = (await llm_chat(MODE_PROMPT, text, max_tokens=3)).upper()
    except Exception:  # noqa: BLE001
        return ""
    if "EVENT" in out:
        return "event"
    if "FITNESS" in out:
        return "fitness"
    if "EVERYDAY" in out:
        return "everyday"
    return ""


HIGHLIGHT_PROMPT = """You are Aura's proactive life-log, like a smart wearable that saves the
moments that matter without being asked. Given what the camera sees and the recent talk, decide
if THIS moment is a genuine highlight worth saving: meeting or being introduced to a person, a
name worth remembering, an important fact or decision, a document/receipt/place, a milestone, or
a warm or notable moment. If yes, reply with ONE short caption (max 12 words) describing it. If
it is ordinary and nothing stands out, reply with exactly: NONE."""


async def detect_highlight(scene: str, transcript_tail: str) -> str:
    text = f"Camera sees:\n{scene or '(nothing clear)'}\n\nRecent talk:\n{transcript_tail or '(quiet)'}"
    try:
        out = (await llm_chat(_lang(HIGHLIGHT_PROMPT), text, max_tokens=30)).strip()
    except Exception:  # noqa: BLE001
        return ""
    if not out or out.upper().startswith("NONE"):
        return ""
    return out


RECAP_PROMPT = """You are Aura giving the user a warm, natural spoken recap of their day, built
from the moments you saved. In 3-6 natural spoken sentences: walk through what happened, the
people and things that mattered, and end with one friendly insight or gentle suggestion. Talk
like a real person catching a friend up — no lists, no markdown, no headings."""


async def summarize_day(items: list[str]) -> str:
    if not items:
        return ""
    text = "Moments saved today (oldest first):\n" + "\n".join(f"- {i}" for i in items)
    return await llm_chat(_lang(RECAP_PROMPT), text, max_tokens=400)


async def summarize_lesson(lesson: str, instructions: list[str]) -> str:
    text = "Instructions given:\n" + "\n".join(f"- {i}" for i in instructions)
    return await llm_chat(_lang(SUMMARY_PROMPT.format(lesson=lesson)), text, max_tokens=250)
