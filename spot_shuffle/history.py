import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from spot_shuffle.spotify import SpotifyClient


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _max_iso(a: str | None, b: str | None) -> str | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


class HistoryStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS track_plays (
                    track_id TEXT PRIMARY KEY,
                    last_played_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def upsert_play(self, track_id: str, played_at: str) -> None:
        now = _utc_now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_played_at FROM track_plays WHERE track_id = ?",
                (track_id,),
            ).fetchone()
            last_played_at = _max_iso(
                row["last_played_at"] if row else None,
                played_at,
            )
            conn.execute(
                """
                INSERT INTO track_plays (track_id, last_played_at, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(track_id) DO UPDATE SET
                    last_played_at = excluded.last_played_at,
                    updated_at = excluded.updated_at
                """,
                (track_id, last_played_at, now),
            )

    def get_last_played_map(self) -> dict[str, str | None]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT track_id, last_played_at FROM track_plays"
            ).fetchall()
        return {row["track_id"]: row["last_played_at"] for row in rows}

    def count_with_history(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM track_plays WHERE last_played_at IS NOT NULL"
            ).fetchone()
        return int(row["count"])

    def oldest_and_newest(self) -> tuple[str | None, str | None]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    MIN(last_played_at) AS oldest,
                    MAX(last_played_at) AS newest
                FROM track_plays
                WHERE last_played_at IS NOT NULL
                """
            ).fetchone()
        return row["oldest"], row["newest"]


def sync_recently_played(client: SpotifyClient, store: HistoryStore) -> int:
    data = client.get_json("/me/player/recently-played", params={"limit": 50})
    updated = 0
    for item in data.get("items", []):
        track = item.get("track") or {}
        track_id = track.get("id")
        played_at = item.get("played_at")
        if not track_id or not played_at:
            continue
        store.upsert_play(track_id, played_at)
        updated += 1
    return updated
