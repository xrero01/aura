# Aura — Deep Research Report & Build Roadmap
*Competitive landscape, lessons, differentiation strategy, and the full roadmap (researched July 2026)*

---

## Part 1 — Who already built this (and what happened to them)

### The dead and the absorbed

| Product | What it was | Result | The lesson |
|---|---|---|---|
| **Humane AI Pin** | $699 pin + $24/mo, camera+voice assistant | DEAD. Sold ~10K of 100K target; at one point daily returns > daily sales; assets sold to HP for $116M (Feb 2025), devices bricked | Don't replace the phone. Don't overprice. Latency kills (2–3s felt broken) |
| **Rabbit R1** | $199 handheld AI agent | Sold 100K but only ~5,000 daily users; staff unpaid since 2025 | Hype sells once; only real daily utility retains |
| **Friend pendant** | $129 always-listening AI companion | ~3,000 sold vs $1M+ ads; mass public backlash | People hate "AI companion surveillance" framing |
| **Limitless pendant** | $99–199 memory pendant | Acquired by Meta (Dec 2025), hardware killed; exited EU/UK entirely over legal burden | Memory wearables are valuable — but legal weight is real |
| **Bee** | $49.99 always-listening bracelet | Acquired by Amazon (Jul 2025) | Cheap hardware + clear utility = the acquirable formula |

### The winners

- **Meta Ray-Ban glasses** — 7M+ units in 2025 (~80% market share). Won by being normal glasses first, AI second: $299, fashion brand, no new social cost. ([CNBC](https://www.cnbc.com/2026/02/11/ray-ban-maker-essilorluxottica-triples-sales-of-meta-ai-glasses.html))
- **Plaud NotePin** — the quiet financial winner: profitable, no VC, ~$250M revenue 2025, 2M+ devices, $100M+ ARR software. Won with a *narrow job*: meeting notes for professionals. ([Forbes](https://www.forbes.com/sites/iainmartin/2025/09/02/how-an-ai-notetaker-became-one-of-the-few-profitable-ai-startups/))
- **Coming heavyweights:** Samsung/Google Android XR glasses (fall 2026), Apple glasses (late 2027), OpenAI × Jony Ive device (2027). The giants all slipped — there is still a window.

### Open-source (your direct neighbors)

- **Omi (Based Hardware)** — ~13K GitHub stars, $89 pendant, the dominant open project. Mic-only: **no camera loop, no speaker — answers arrive as phone notifications, not voice in your ear**. ([github.com/BasedHardware/omi](https://github.com/BasedHardware/omi))
- **MentraOS** — open smart-glasses OS (2.2K stars), the closest to "answers in your ear," but it's an OS for glasses hardware, not a phone-first assistant. ([github.com/Mentra-Community/MentraOS](https://github.com/Mentra-Community/MentraOS))
- **ADeus, OpenGlass, Owl** — the 2024 pioneers; all dead or absorbed into Omi. Owl's founder built Bee → acquired by Amazon.
- **screenpipe** — 19.8K stars but desktop-only (screen + mic lifelogging).

**Verdict: nobody has shipped exactly Aura.** The unclaimed combination is: *phone-first (no hardware to buy) + camera AND mic + voice replies in the ear + proactive speaking + multi-camera + live lesson coaching.* Omi has memory but no eyes/voice-out. Meta has eyes/voice but no open memory or coaching. Coaching apps (below) have no wearable ambition.

---

## Part 2 — The biggest finding: your lesson mode is the open lane

Real-time camera coaching exists only as fragmented, single-sport phone apps: GOATY (golf, pose-tracking + voice cues), SwingVision (tennis), Carv 2 (skiing, in-ear voice coaching — proof people pay for exactly this experience), Equestic EQ Coach-Copilot (horse riding — validates your exact use case!). **No one owns horizontal "real-time voice coaching for any skill through any camera."** That is Aura's lesson mode. Analysts' consensus after the Humane/Friend failures: winners are *narrow-purpose tools*, not general "AI companions" — and proactive, memory-rich assistance is named as the open 2026 opportunity. Market context: AI glasses forecast to grow from 10M units (2026) to 35–75M by 2030 ([Omdia](https://omdia.tech.informa.com/pr/2025/sep/ai-glasses-market-poised-to-hit-10-million-units-in-2026-omdia-forecasts)).

**Strategic suggestion:** lead with **"Aura — your real-time AI coach for any skill"** (riding, cooking, chess, fixing, presenting). The ambient always-on memory assistant stays in the product but becomes a feature, not the pitch. Coaching is opt-in, session-based, and *you* are the subject — which also neatly avoids most of the legal minefield below.

---

## Part 3 — ⚠️ Legal reality (read before shipping)

This changes the product design, especially in the Gulf:

- **UAE:** recording a private conversation without ALL parties' consent is a **crime** — Art. 44 Cybercrime Law: ≥6 months prison and AED 150K–500K fine, even if you're a participant; sharing recordings is a separate offense; device confiscation applies. Same family of laws in Saudi (sharing: up to SAR 500K) and Egypt (up to 1 year). ([Chambers](https://chambers.com/articles/privacy-violations-and-secret-disclosure-under-the-uae))
- **EU:** so burdensome that Limitless simply exited the EU/UK rather than comply. Germany criminalizes participant recording (§201 StGB, up to 3 years). EU AI Act high-risk obligations for speaker/face ID from Aug 2, 2026.
- **US:** 12 all-party-consent states (CA, IL, FL, WA...).
- **Industry answer:** Limitless "Consent Mode" (voice-ID pauses recording until a new speaker consents) is the emerging standard; Meta now disables the Ray-Ban camera if its recording LED is tampered with.

**Design consequences for Aura:**
1. Ship with **Session Mode as default** (explicit start/stop — lessons, solo activities, your own dictation) rather than default always-on ambient recording.
2. Store transcripts + scene descriptions, **discard raw audio** after transcription (Bee's model) — dramatically lowers risk and storage.
3. Add a **consent gate** setting for ambient mode + a visible on-screen recording indicator.
4. Everything stays local (your SQLite) — never cloud-sync raw recordings. Already Aura's design. Keep it as a headline feature: "your memory never leaves your device."

---

## Part 4 — Technology suggestions (what to upgrade to)

Your current stack (chunked Whisper → GPT → TTS, ~5–8s) is right for v0. The research says the upgrade path is:

| Component | Best choice (mid-2026) | Why |
|---|---|---|
| **Live voice+vision brain** | **Google Gemini Live API** | The ONLY cloud API with continuous live video (1 FPS) + audio in one session; ~$0.02–0.03/hr-minute; free tier for prototyping; needs session-resumption code |
| Voice-only alternative | OpenAI gpt-realtime-2.1-mini or Grok Voice ($0.05/min flat) or Nova 2 Sonic (~$0.015/min, cheapest) | If vision stays on the slow path |
| Streaming STT (pipeline mode) | Deepgram Nova-3 ($0.0077/min, <300ms) | Replaces chunked Whisper, cuts seconds of latency |
| TTS | ElevenLabs Flash (~75ms) or gpt-4o-mini-tts (~$0.015/min) | |
| Orchestration | LiveKit Agents or Pipecat (open source) | Production-grade audio plumbing instead of raw WebSockets |
| Memory | Keep raw-transcript RAG (what Aura does!) + add nightly summary tiers | 2026 benchmark: raw RAG *beats* fancy summary-memory systems for lifelog recall ([arXiv 2604.11182](https://arxiv.org/html/2604.11182)); add RAPTOR-style daily/weekly summaries on top |
| On-device (offline) | Gemini Nano v3 via ML Kit (flagship phones) | Free, private, offline transcription + vision; avoid streaming whisper.cpp on Android (too slow) |

Target: voice-to-voice under **1.5s** (industry median), which feels conversational. Your current architecture already isolates all AI calls in `pipeline.py` — the swap is contained.

---

## Part 5 — The full roadmap

### Phase 0 — NOW (done ✅)
Working prototype: phone PWA + APK wrapper, multi-camera hub, scene memory, proactive gate, lesson coach, 9/9 integration tests passing. Push to GitHub, build APK, use it daily yourself with your OpenAI key.

### Phase 1 — Make it fast & legal-safe (2–4 weeks)
- Swap the pipeline to **Gemini Live** for the live conversation path (voice+video in one stream, sub-2s replies); keep the current pipeline for memory-writing and recall answers.
- Streaming STT (Deepgram) for the ambient transcript.
- **Session Mode default + consent toggle + recording indicator; discard raw audio after transcription.**
- Wake-word on-device instead of text matching (e.g. Porcupine/openWakeWord).

### Phase 2 — Own the coaching wedge (1–2 months)
- Polish Lesson Mode into the headline: lesson templates (riding, cooking, gym form, presentations, language practice), pose-tracking on-device (MediaPipe) feeding the coach for body-skill lessons, session summary + progress tracking after each lesson ("what improved, what to drill next").
- Multi-camera lesson setups (tripod phone + your view = coach sees both).
- Memory upgrades: nightly summarization tiers; "what did I learn this week?"

### Phase 3 — Real Android app & distribution (2–3 months)
- Native Kotlin app (foreground service = screen-off listening, proper background audio; the WebView APK can't do this). Server logic unchanged.
- Google Play listing (coaching app framing passes review easily; ambient recorder framing will not).
- Optional BYO-key model: users bring their own Gemini/OpenAI key = zero AI cost for you, or a $X/mo hosted tier (Plaud's model: hardware/app cheap, subscription is the business).

### Phase 4 — Beyond the phone (later)
- Hardware experiments: chest mount / neckband phone holder first (free), then camera glasses when a good open option ships (MentraOS-compatible glasses, or Omi Glass dev kit $299).
- Multi-user, shared coach sessions (instructor watches remotely while AI coaches live).
- If traction: this is exactly the category Amazon/Meta/HP have been buying (Bee, Limitless, Humane teams).

### What NOT to do (paid for by others' money)
- Don't build custom hardware first (Humane: $230M lesson). Phone-first is right.
- Don't market it as an "AI friend/companion" (Friend's backlash) or a phone replacement.
- Don't default to always-on ambient recording of other people, especially in the Gulf/EU — the criminal exposure is yours as the user, and app stores will reject it.
- Don't chase general "assistant that does everything" — every winner had one sharp job.

---

## Sources (key)
Meta sales: [CNBC](https://www.cnbc.com/2026/02/11/ray-ban-maker-essilorluxottica-triples-sales-of-meta-ai-glasses.html) · Humane post-mortems: [TechCrunch](https://techcrunch.com/2025/02/18/humanes-ai-pin-is-dead-as-hp-buys-startups-assets-for-116m/), [Failure Museum](https://failure.museum/humane-ai-pin/) · Bee→Amazon: [TechCrunch](https://techcrunch.com/2025/07/22/amazon-acquires-bee-the-ai-wearable-that-records-everything-you-say/) · Limitless→Meta: [TechCrunch](https://techcrunch.com/2025/12/05/meta-acquires-ai-device-startup-limitless/) · Plaud: [Forbes](https://www.forbes.com/sites/iainmartin/2025/09/02/how-an-ai-notetaker-became-one-of-the-few-profitable-ai-startups/) · Omi: [GitHub](https://github.com/BasedHardware/omi) · MentraOS: [GitHub](https://github.com/Mentra-Community/MentraOS) · Gemini Live: [pricing](https://ai.google.dev/gemini-api/docs/pricing), [capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities) · OpenAI Realtime: [docs](https://developers.openai.com/api/docs/guides/realtime) · Lifelog memory benchmark: [arXiv](https://arxiv.org/html/2604.11182) · UAE law: [Chambers](https://chambers.com/articles/privacy-violations-and-secret-disclosure-under-the-uae), [UAE Legislation](https://uaelegislation.gov.ae/en/legislations/1526) · EU exit: [TechInformed](https://techinformed.com/meta-acquires-limitless-pendant-users-moved-to-free-unlimited-plan/) · Coaching comps: [GOATY](https://goatcode.ai/golf-coaching-app-real-time-feedback.html), [Carv 2](https://www.techradar.com/health-fitness/Carv-2-Ski-Coach-review), [Equestic](https://www.worldofshowjumping.com/en/News/Advertorials/Setting-a-new-standard-in-equestrian-training-Equestic-introduces-EQ-Coach-Copilot.html) · Market size: [Omdia](https://omdia.tech.informa.com/pr/2025/sep/ai-glasses-market-poised-to-hit-10-million-units-in-2026-omdia-forecasts)
