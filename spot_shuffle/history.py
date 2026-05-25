import sqlite3
from dataclasses import dataclass, field
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


@dataclass
class SyncTrackChange:
    track_id: str
    name: str
    artists: str
    played_at: str
    change: str  # "new" or "updated"


@dataclass
class SyncResult:
    entries_processed: int
    changes: list[SyncTrackChange] = field(default_factory=list)


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

    def upsert_play(self, track_id: str, played_at: str) -> str | None:
        """Return 'new', 'updated', or None if last_played_at did not change."""
        now = _utc_now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_played_at FROM track_plays WHERE track_id = ?",
                (track_id,),
            ).fetchone()
            previous = row["last_played_at"] if row else None
            last_played_at = _max_iso(previous, played_at)
            if previous == last_played_at:
                return None

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
        return "new" if previous is None else "updated"

    def get_last_played_map(self) -> dict[str, str | None]:
        return {
            track_id: record.get("last_played_at")
            for track_id, record in self.get_track_records().items()
        }

    def get_track_records(self) -> dict[str, dict[str, str | None]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT track_id, last_played_at, updated_at FROM track_plays"
            ).fetchall()
        return {
            row["track_id"]: {
                "last_played_at": row["last_played_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        }

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


def _track_label(track: dict) -> tuple[str, str, str]:
    track_id = track.get("id") or ""
    name = track.get("name") or "Unknown"
    artists = ", ".join(a.get("name", "") for a in track.get("artists", []))
    return track_id, name, artists


def sync_recently_played(client: SpotifyClient, store: HistoryStore) -> SyncResult:
    data = client.get_json("/me/player/recently-played", params={"limit": 50})
    result = SyncResult(entries_processed=0)
    for item in data.get("items", []):
        track = item.get("track") or {}
        track_id, name, artists = _track_label(track)
        played_at = item.get("played_at")
        if not track_id or not played_at:
            continue
        result.entries_processed += 1
        change = store.upsert_play(track_id, played_at)
        if change:
            result.changes.append(
                SyncTrackChange(
                    track_id=track_id,
                    name=name,
                    artists=artists,
                    played_at=played_at,
                    change=change,
                )
            )
    return result


def format_sync_summary(result: SyncResult) -> str:
    lines = [
        "Sync summary",
        "============",
        f"Recently played entries processed: {result.entries_processed}",
        f"Play history changes:               {len(result.changes)}",
    ]
    if not result.changes:
        lines.append("No new or updated play timestamps.")
        return "\n".join(lines)

    lines.append("")
    lines.append("Updated tracks:")
    for item in result.changes[:10]:
        label = f"{item.name} — {item.artists}"
        if item.change == "new":
            lines.append(f"  + {label} (first recorded, played {item.played_at})")
        else:
            lines.append(f"  ~ {label} (played {item.played_at})")
    if len(result.changes) > 10:
        lines.append(f"  ... and {len(result.changes) - 10} more")
    return "\n".join(lines)
