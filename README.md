# Aura 👁️🎧

Your phone's camera and mic watch the world around you; an AI thinks about what's
happening and whispers replies, suggestions, and recalled memories into your earbuds.

- 🎙 **Hears** everything → transcribes → remembers (SQLite + embeddings)
- 👁 **Sees** through the camera → answers "what am I looking at?"
- 🗣 **Coaches** conversations → suggests what to say next
- ⚡ **Proactive mode** → speaks up on its own when it's worth it (LLM decision gate)

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design, latency budget, and roadmap.

## Quick start (one click)

```bash
export OPENROUTER_API_KEY=sk-or-...   # Windows: set OPENROUTER_API_KEY=sk-or-...
./start.sh                            # Windows: start.bat
```

(Or use `OPENAI_API_KEY` / `GEMINI_API_KEY` instead — the launcher auto-detects.)

That installs deps, starts the server, opens an HTTPS tunnel (ngrok/cloudflared),
and prints the URL to open on your phone. Connect earbuds, tap **Start**, done.

### Choose your AI (any model, any provider)

| Env var | Values | Effect |
|---|---|---|
| `AURA_PROVIDER` | `openai` (default) / `gemini` / `openrouter` | STT + TTS + embeddings + brain |
| `AURA_BRAIN` | `openai` / `gemini` / `anthropic` / `openrouter` | override just the brain (thinking/coaching) |
| `AURA_OPENAI_MODEL` / `AURA_GEMINI_MODEL` / `AURA_ANTHROPIC_MODEL` / `AURA_OPENROUTER_MODEL` | any model name | pick smarter/cheaper models |

**OpenRouter** (`AURA_PROVIDER=openrouter`, tested end-to-end): one key, any model on
openrouter.ai — set `AURA_OPENROUTER_MODEL` (default `openai/gpt-4o-mini`; try
`anthropic/claude-sonnet-4.5` for a super-smart brain, `google/gemini-2.5-flash` for cheap+fast).
Voice replies use free local edge-tts (in requirements) and hearing runs through an
audio-capable model. **Install ffmpeg on the server** (`winget install ffmpeg` /
`sudo apt install ffmpeg`) — it converts the phone's audio for transcription.

Other examples: everything on Google — `AURA_PROVIDER=gemini GEMINI_API_KEY=...` ·
Claude brain — `AURA_BRAIN=anthropic ANTHROPIC_API_KEY=...` with any provider for voice.

Aura is an **installable app (PWA)**: open the URL in Chrome on Android →
menu → **Add to Home screen**. It then opens full-screen like a native app;
tap Start once and everything runs.

## Get the APK (no Android Studio needed)

The `android/` folder is a native wrapper app, and GitHub builds the APK for you:

1. Push this repo to GitHub:
   ```bash
   git init && git add . && git commit -m "Aura"
   git branch -M main
   git remote add origin https://github.com/<you>/aura.git
   git push -u origin main
   ```
2. On GitHub: **Actions** tab → **Build Android APK** run → download the **aura-apk** artifact.
3. Copy `app-debug.apk` to your phone and install it (allow "install unknown apps").
4. Open Aura → enter your server URL (the ngrok https address) once → done.

The APK is the best experience: replies auto-play with zero taps, the screen never
sleeps, and mic/camera permissions are native. (Press Back on the main screen to
change the server URL later.)

## How to use

- **Just talk — no wake word.** By default ("Just talk" in settings) Aura answers anything
  you say: "what am I looking at?", "where did I put my keys?". "Hey Aura" also still works,
  and is the fallback when Just-talk is switched off.
- **UI:** central orb shows state (green pulse = listening, blue spin = thinking,
  bars = speaking). Camera preview top-right (tap to enlarge). Chat feed below.
  Bottom bar: lessons 🎓, pause/listen, settings, and the red **power button = full
  shutdown** (stops mic, camera, background service; app won't auto-start next open).
- Aura keeps running while you switch apps (APK keeps a foreground service +
  "Aura is active" notification) — only the power button turns it off.
- Say **"tell me everything / explain / step by step"** — switches to full-guide answers.
- Toggle **Proactive** — Aura decides on its own when to whisper (conservative by design;
  tune `GATE_PROMPT` in `server/pipeline.py` to make it bolder or quieter).
- **Lesson mode** — type a topic (e.g. `horse riding`) and tap **Teach me**. Aura becomes a
  live instructor: every ~20 s it looks at all cameras, listens, and speaks the single most
  useful instruction into your ear (or stays silent). Built-in expert templates: horse riding,
  cooking, gym, presentation, language practice, chess, driving — anything else works too.
  Tap **End lesson** and Aura speaks a progress summary and saves it to memory
  ("what did I improve this week?" works later).
- **Ambient toggle (privacy/session mode)** — turn Ambient OFF and Aura completely ignores
  and never stores anything except when you address it directly or during your lessons.
  Use this in places where recording other people is restricted (see
  RESEARCH-AND-ROADMAP.md part 3 — in the UAE/Gulf this is a criminal matter, take it
  seriously). Aura never stores raw audio in any mode — only text transcripts, on your device.

## Extra cameras

| Camera | How |
|---|---|
| Second phone | Open the same URL on it → tap **Join as extra camera** → name it (e.g. `helmet`). |
| WiFi action cam / IP cam | Enter its `rtsp://` URL in the app → **Add cam**. Needs `pip install opencv-python-headless` on the server. |
| USB (OTG) camera | If Android exposes it as a camera device, pick it in extra-camera mode. Many phones don't — a second phone or RTSP cam is more reliable. |

All cameras feed the same brain: scene memory, questions, and lessons see every view.

## Repo layout

```
client/index.html   phone web app: mic chunks + camera frames over WebSocket, plays replies
server/main.py      FastAPI WebSocket server, orchestration
server/pipeline.py  all AI calls: STT, decision gate, vision LLM, TTS, embeddings
server/memory.py    SQLite memory store + cosine-similarity recall
```

## ⚠️ Privacy

You're recording people around you. Many jurisdictions require everyone's consent to
record conversations — check your local law, and tell people when in doubt. All memory
stays in a local SQLite file (`server/aura_memory.db`); delete it to forget everything.
