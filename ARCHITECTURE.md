# Aura — Architecture & Design

> A phone-based ambient AI copilot: the camera and mic see and hear the world around you,
> an AI reasons about the context, and whispers useful replies into your earbuds.

## 1. Concept

Four capabilities, one loop:

| Capability | What it means |
|---|---|
| Conversation coaching | Hears the conversation you're in, suggests what to say next |
| Scene awareness | Answers "what am I looking at?" using camera frames |
| Memory / recall | Remembers everything heard/seen; "what did she say her name was?" |
| Proactive mode | Speaks up on its own when it notices something worth saying |

## 2. Hardware (Phase 1: zero cost)

```
[Your phone]                         [Your computer / cloud VM]
 camera + mic  ──WebSocket/WiFi──►    Python server (FastAPI)
 Bluetooth earbuds ◄──audio reply──   STT → LLM(vision) → TTS + memory DB
```

- The **client is a web page** opened in the phone browser — no app store, no native code.
  `getUserMedia` captures mic audio + camera frames; replies play back through
  whatever audio device the phone uses (your earbuds).
- Later phases can swap the phone for ESP32-CAM glasses or an Omi-style pendant
  without changing the server.

## 3. Data flow

```
mic ──4s audio chunks──►  STT (Whisper)  ──text──►  transcript log ──► memory (SQLite + embeddings)
cam ──1 frame / 5s ────►  latest-frame buffer            │
                                                          ▼
                                            Decision gate (cheap LLM call):
                                            "was I addressed? / is this worth interrupting for?"
                                                          │ yes
                                                          ▼
                                            Main LLM (text + latest camera frame + recalled memories)
                                                          │
                                                          ▼
                                            TTS ──audio──► phone ──► earbuds
```

Two interaction modes:

1. **Ask mode (wake word)** — say "Hey Aura, …" and it always answers.
2. **Proactive mode** — every transcribed chunk passes a *decision gate*: a cheap/fast
   LLM call that outputs `SPEAK` or `STAY_SILENT`. This is the single most important
   design element — without it the assistant is unbearable. Tune the gate prompt, not the main prompt.

## 4. Latency budget (pipeline approach)

| Stage | Target |
|---|---|
| Audio chunking (buffer) | ~2–4 s (dominant cost) |
| STT (Whisper API) | ~0.5–1 s |
| Decision gate (small model) | ~0.3–0.7 s |
| Main LLM w/ vision | ~1–2 s |
| TTS first byte + playback | ~0.5–1 s |
| **Total (worst case)** | **~5–8 s** |

That's fine for coaching ("here's a good follow-up question") but not for fluid dialogue.
**Phase 2 upgrade:** replace STT→LLM→TTS with a native speech-to-speech API
(OpenAI Realtime or Gemini Live) for ~0.5–1 s voice replies, and keep the pipeline
only for memory writing and vision. Gemini Live is natively multimodal (audio+video in,
audio out, one model) and is the cleanest fit once you outgrow this scaffold.

## 5. Memory design

- Every transcript chunk and scene description is stored in SQLite with an
  embedding (`text-embedding-3-small`).
- On each main-LLM call, the top-k semantically similar memories are injected into context.
- Ask "what did she say her name was?" → embedding search over the transcript
  finds the moment, the LLM answers from it.
- Phase 2: nightly summarization job compresses raw chunks into "day summaries"
  so the DB stays small and recall stays sharp.

## 6. Model choices (all swappable in one file, `pipeline.py`)

| Role | Default | Alternatives |
|---|---|---|
| STT | OpenAI `whisper-1` | faster-whisper (local, free), Deepgram (streaming) |
| Decision gate | `gpt-4o-mini` | any small/fast model, or local |
| Main brain | `gpt-4o-mini` (vision) | Claude, Gemini Flash; upgrade when quality matters |
| TTS | OpenAI `tts-1` | ElevenLabs (nicer voice), Piper (local, free) |
| Embeddings | `text-embedding-3-small` | local sentence-transformers |

Rough running cost with defaults: on the order of **$0.5–2 per active hour**, dominated
by STT minutes. Local Whisper + Piper drops that to ~LLM-only cost.

## 7. Privacy & legal — read this

- You are recording other people. Many places require **consent of all parties** to
  record conversations (this varies by country/state). Check local law; when in doubt, tell people.
- Keep the memory DB local. Don't upload raw audio anywhere you don't control.
- The client has a big pause button — use it.

## 8. Roadmap

1. **v0 (this repo):** phone browser + laptop server, ask mode + proactive gate + memory.
2. **v1:** swap pipeline for Gemini Live / OpenAI Realtime → sub-second replies; streaming STT.
3. **v2:** on-device wake word, nightly memory summarization, speaker diarization ("who said it").
4. **v3:** dedicated wearable (ESP32-S3 + cam, or build on Omi hardware) — server stays identical.
