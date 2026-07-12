"""End-to-end test of the whole Aura loop with fake AI (no API key needed).

Run:  cd server && python3 test_e2e.py

Tests the REAL server, routing, memory, gate logic, lesson coach, privacy modes
and multi-camera hub. Only the external AI calls are replaced with deterministic
fakes, so this proves the assistant's plumbing works everywhere.
"""

import pathlib
import time

import memory
import pipeline
import main
from fastapi.testclient import TestClient

# ---------------- fake AI (deterministic, offline) ----------------

calls = {"think": [], "coach": [], "gate": [], "describe": 0, "summary": []}


async def fake_transcribe(audio_bytes, mimetype="audio/webm"):
    return audio_bytes.decode("utf-8")          # fake mic: bytes ARE the words


async def fake_embed(text):
    t = text.lower()
    if "key" in t:
        return [1.0, 0.0, 0.0]
    if "horse" in t:
        return [0.0, 1.0, 0.0]
    return [0.0, 0.0, 1.0]


async def fake_should_speak(tail):
    calls["gate"].append(tail)
    return "?" in tail


async def fake_think(tail, frames, memories, detailed=False):
    calls["think"].append({"frames": [f[0] for f in frames],
                           "memories": memories, "detailed": detailed})
    if memories:
        return "ANSWER-FROM-MEMORY: " + memories[0]
    return ("LONG-GUIDE-ANSWER " * 5).strip() if detailed else "SHORT-ANSWER"


async def fake_coach(lesson, tail, frames, history, template=""):
    calls["coach"].append({"lesson": lesson, "template": template})
    return "Sit up straight and relax your grip." if len(calls["coach"]) == 1 else "WAIT"


async def fake_describe(frame):
    calls["describe"] += 1
    return "keys on a wooden desk next to a blue mug"


async def fake_speak(text):
    return b"AUDIO:" + text[:20].encode()


async def fake_summary(lesson, instructions):
    calls["summary"].append(lesson)
    return f"Great {lesson} session. Next time, drill your posture."


pipeline.transcribe = fake_transcribe
pipeline.embed = fake_embed
pipeline.should_speak = fake_should_speak
pipeline.think = fake_think
pipeline.coach = fake_coach
pipeline.describe_frame = fake_describe
pipeline.speak = fake_speak
pipeline.summarize_lesson = fake_summary

# ---------------- test setup ----------------

memory.DB_PATH = pathlib.Path("/tmp/aura_test.db")
if memory.DB_PATH.exists():
    memory.DB_PATH.unlink()
main.COACH_INTERVAL_S = 0.3
client = TestClient(main.app)
ok = lambda name: print(f"  PASS  {name}")  # noqa: E731

with client.websocket_connect("/ws") as ws:

    # 1. camera frame arrives -> scene memory is written
    ws.send_json({"type": "frame", "jpeg_b64": "aGVsbG8="})
    time.sleep(0.4)
    assert calls["describe"] >= 1
    ok("1. camera frame seen -> scene described & stored in memory")

    # 2. wake word question -> hears, answers, speaks (with MIME type)
    ws.send_bytes(b"Hey Aura, what am I looking at right now")
    assert ws.receive_json()["type"] == "transcript"
    r = ws.receive_json(); assert r["type"] == "reply_text"
    a = ws.receive_json()
    assert a["type"] == "reply_audio" and a["audio_b64"] and a["mime"]
    assert calls["think"][-1]["frames"] == ["wearer"]
    ok("2. mic question -> transcribed -> answered -> VOICE sent to earphone")

    # 3. long guide mode
    ws.send_bytes(b"Hey Aura explain everything about the history of Egypt")
    ws.receive_json(); r = ws.receive_json(); ws.receive_json()
    assert calls["think"][-1]["detailed"] is True and "LONG-GUIDE" in r["text"]
    ok("3. 'explain everything' -> full detailed guide mode")

    # 4. PROACTIVE: someone else asks a question -> Aura replies UNASKED
    ws.send_bytes(b"Excuse me, do you know the way to the train station?")
    ws.receive_json()
    r = ws.receive_json(); assert r["type"] == "reply_text"
    ws.receive_json()
    ok("4. proactive: overheard question -> answered WITHOUT being asked")

    # 5. proactive gate stays SILENT for boring chatter
    n = len(calls["think"])
    ws.send_bytes(b"nice weather today we walked a lot")
    ws.receive_json()
    time.sleep(0.3)
    assert len(calls["think"]) == n
    ok("5. boring chatter -> gate stays silent (no annoying interruptions)")

    # 6. MEMORY RECALL: 'where are my keys' finds what the CAMERA saw
    ws.send_bytes(b"Hey Aura where did I put my keys")
    ws.receive_json(); r = ws.receive_json(); ws.receive_json()
    assert "keys on a wooden desk" in r["text"]
    ok("6. 'where are my keys?' -> recalled from what the camera saw earlier")

    # 7. EXTRA CAMERA joins and its view reaches the brain
    with client.websocket_connect("/ws?role=cam&name=helmet") as cam:
        cam.send_json({"type": "frame", "jpeg_b64": "aGVsbWV0"})
        time.sleep(0.2)
        ws.send_bytes(b"Hey Aura what do you see")
        ws.receive_json(); ws.receive_json(); ws.receive_json()
        assert sorted(calls["think"][-1]["frames"]) == ["helmet", "wearer"]
    ok("7. second camera (helmet) -> brain sees BOTH views at once")

    # 8. LESSON MODE with template: coach speaks, uses the horse-riding template
    ws.send_json({"type": "config", "lesson": "horse riding"})
    r = ws.receive_json(); assert "horse riding lesson" in r["text"]
    ws.receive_json()
    tip = ws.receive_json()
    assert tip["text"].startswith("Sit up straight")
    assert "posture" in calls["coach"][0]["template"]   # expert template injected
    ws.receive_json()
    time.sleep(0.8)
    assert len(calls["coach"]) >= 2
    ok("8. lesson mode: live coaching with expert template, silent when WAIT")

    # 9. wake word still answered DURING a lesson
    ws.send_bytes(b"Hey Aura am I holding the reins correctly")
    ws.receive_json(); r = ws.receive_json(); ws.receive_json()
    assert r["type"] == "reply_text"
    ok("9. questions still answered during a lesson")

    # 10. LESSON END -> spoken progress summary, stored in memory
    ws.send_json({"type": "config", "lesson": ""})
    r = ws.receive_json()
    assert r["type"] == "reply_text" and "Great horse riding session" in r["text"]
    ws.receive_json()   # summary audio
    assert calls["summary"] == ["horse riding"]
    ok("10. lesson end -> spoken summary + progress saved")

    # 11. SESSION MODE (ambient off): chatter is DROPPED, wake word still works
    ws.send_json({"type": "config", "ambient": False})
    before = len(calls["gate"])
    ws.send_bytes(b"they said the secret code is 4512?")
    time.sleep(0.4)
    assert len(calls["gate"]) == before        # not even gated: fully dropped
    assert "4512" not in memory.recent_transcript(minutes=1)   # never stored
    ws.send_bytes(b"Hey Aura what time is it")
    assert ws.receive_json()["type"] == "transcript"
    assert ws.receive_json()["type"] == "reply_text"
    ws.receive_json()
    ok("11. privacy session mode: bystander speech dropped, direct questions work")

# 12. JUST-TALK MODE: no wake word — a plain question gets answered
calls["think"].clear()
with client.websocket_connect("/ws") as ws2:
    ws2.send_json({"type": "config", "answer_all": True, "ambient": True})
    ws2.send_bytes(b"what is the capital of France")
    assert ws2.receive_json()["type"] == "transcript"
    r = ws2.receive_json(); assert r["type"] == "reply_text"
    ws2.receive_json()
    ok("12. just-talk mode: answered WITHOUT any wake word")

print("\nALL 12 END-TO-END TESTS PASSED")
