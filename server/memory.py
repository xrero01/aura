"""Long-term memory: SQLite + embeddings, cosine-similarity recall."""

import json
import math
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "aura_memory.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            kind TEXT NOT NULL,          -- 'heard' | 'seen' | 'said_by_aura'
            text TEXT NOT NULL,
            embedding TEXT               -- JSON list of floats
        )"""
    )
    return conn


def store(kind: str, text: str, embedding: list[float] | None = None) -> None:
    if not text.strip():
        return
    with _conn() as conn:
        conn.execute(
            "INSERT INTO memories (ts, kind, text, embedding) VALUES (?, ?, ?, ?)",
            (time.time(), kind, text, json.dumps(embedding) if embedding else None),
        )


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def recall(query_embedding: list[float], k: int = 5, exclude_recent_s: float = 60) -> list[str]:
    """Top-k most similar memories.

    Recent 'heard' entries are skipped (they're already in the transcript context),
    but 'seen' camera memories are always searchable — even from seconds ago.
    """
    cutoff = time.time() - exclude_recent_s
    with _conn() as conn:
        rows = conn.execute(
            "SELECT ts, kind, text, embedding FROM memories "
            "WHERE embedding IS NOT NULL AND (kind != 'heard' OR ts < ?)",
            (cutoff,),
        ).fetchall()
    scored = [
        (_cosine(query_embedding, json.loads(emb)), ts, kind, text)
        for ts, kind, text, emb in rows
    ]
    scored.sort(reverse=True)
    out = []
    for score, ts, kind, text in scored[:k]:
        if score < 0.25:
            continue
        when = time.strftime("%a %H:%M", time.localtime(ts))
        out.append(f"[{when}, {kind}] {text}")
    return out


def recent_by_kind(kinds: tuple[str, ...], hours: float = 24, limit: int = 200) -> list[tuple]:
    """Recent memories of the given kinds within a time window (for day recaps/search).
    Returns (ts, kind, text) rows oldest-first."""
    cutoff = time.time() - hours * 3600
    placeholders = ",".join("?" for _ in kinds)
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT ts, kind, text FROM memories WHERE ts >= ? AND kind IN ({placeholders}) "
            "ORDER BY ts",
            (cutoff, *kinds),
        ).fetchall()
    return rows[-limit:]


def recent_transcript(minutes: float = 3, max_chars: int = 2000) -> str:
    """The rolling context window of what was just heard."""
    cutoff = time.time() - minutes * 60
    with _conn() as conn:
        rows = conn.execute(
            "SELECT text FROM memories WHERE kind='heard' AND ts >= ? ORDER BY ts", (cutoff,)
        ).fetchall()
    return "\n".join(r[0] for r in rows)[-max_chars:]
