import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from spot_shuffle.config import Config
from spot_shuffle.library import fetch_liked_tracks, liked_tracks_as_details
from spot_shuffle.history import HistoryStore
from spot_shuffle.ordering import order_tracks
from spot_shuffle.spotify import SpotifyClient

BATCH_SIZE = 100
BATCH_SLEEP_SECONDS = 0.2


@dataclass
class TrackSummary:
    track_id: str
    name: str
    artists: str
    position: int
    last_played_at: str | None = None
    old_position: int | None = None


@dataclass
class RefreshSummary:
    playlist_id: str
    playlist_name: str
    total_tracks: int
    tracks_reordered: int
    never_played: int
    with_history: int
    moved_down: list[TrackSummary] = field(default_factory=list)
    up_next: list[TrackSummary] = field(default_factory=list)


def find_playlist_by_name(client: SpotifyClient, name: str) -> dict | None:
    playlists = client.get_paginated("/me/playlists", params={"limit": 50})
    for playlist in playlists:
        if playlist.get("name") == name:
            return playlist
    return None


def get_or_create_playlist(client: SpotifyClient, config: Config) -> str:
    existing = find_playlist_by_name(client, config.playlist_name)
    if existing:
        return existing["id"]

    created = client.post(
        "/me/playlists",
        json={
            "name": config.playlist_name,
            "public": False,
            "description": "Auto-sorted by least recently heard (Spot Shuffle).",
        },
    )
    return created["id"]


def _order_snapshot_path(config: Config) -> Path:
    return config.db_path.parent / "last_playlist_order.json"


def load_previous_order(config: Config) -> list[str]:
    path = _order_snapshot_path(config)
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save_current_order(config: Config, track_ids: list[str]) -> None:
    path = _order_snapshot_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(track_ids))


def get_playlist_track_ids(client: SpotifyClient, playlist_id: str) -> list[str]:
    items = client.get_paginated(
        f"/playlists/{playlist_id}/tracks",
        params={"limit": 100},
    )
    track_ids: list[str] = []
    for item in items:
        track = item.get("track") or {}
        track_id = track.get("id")
        if track_id:
            track_ids.append(track_id)
    return track_ids


def set_playlist_tracks(client: SpotifyClient, playlist_id: str, uris: list[str]) -> None:
    if not uris:
        client.put(f"/playlists/{playlist_id}/items", json={"uris": []})
        return

    first_batch = uris[:BATCH_SIZE]
    client.put(f"/playlists/{playlist_id}/items", json={"uris": first_batch})

    for start in range(BATCH_SIZE, len(uris), BATCH_SIZE):
        batch = uris[start : start + BATCH_SIZE]
        client.post(f"/playlists/{playlist_id}/items", json={"uris": batch})
        time.sleep(BATCH_SLEEP_SECONDS)


def _build_track_summary(
    track_id: str,
    position: int,
    details: dict[str, dict],
    last_played: dict[str, str | None],
    old_position: int | None = None,
) -> TrackSummary:
    track = details.get(track_id, {})
    name = track.get("name") or track_id
    artists = ", ".join(a.get("name", "") for a in track.get("artists", []))
    return TrackSummary(
        track_id=track_id,
        name=name,
        artists=artists,
        position=position,
        last_played_at=last_played.get(track_id),
        old_position=old_position,
    )


def _summarize_changes(
    old_order: list[str],
    new_order: list[str],
    last_played: dict[str, str | None],
    details: dict[str, dict],
) -> tuple[int, list[TrackSummary], list[TrackSummary]]:
    old_positions = {track_id: index + 1 for index, track_id in enumerate(old_order)}
    new_positions = {track_id: index + 1 for index, track_id in enumerate(new_order)}

    moved_down: list[TrackSummary] = []
    for track_id, new_pos in new_positions.items():
        old_pos = old_positions.get(track_id)
        if old_pos is None or new_pos <= old_pos:
            continue
        moved_down.append(
            _build_track_summary(
                track_id,
                new_pos,
                details,
                last_played,
                old_position=old_pos,
            )
        )

    moved_down.sort(
        key=lambda item: (
            item.last_played_at or "",
            item.position - (item.old_position or item.position),
        ),
        reverse=True,
    )

    up_next = [
        _build_track_summary(track_id, index + 1, details, last_played)
        for index, track_id in enumerate(new_order[:5])
    ]

    reordered = sum(
        1
        for track_id in new_order
        if track_id in old_positions and old_positions[track_id] != new_positions[track_id]
    )
    return reordered, moved_down[:10], up_next


def format_refresh_summary(summary: RefreshSummary) -> str:
    lines = [
        "Refresh summary",
        "===============",
        f"Playlist:              {summary.playlist_name} ({summary.playlist_id})",
        f"Total tracks:          {summary.total_tracks}",
        f"Tracks reordered:      {summary.tracks_reordered}",
        f"Never played in DB:    {summary.never_played}",
        f"With play history:     {summary.with_history}",
    ]

    if summary.moved_down:
        lines.extend(["", "Moved down (recent/stale tracks sent toward the end):"])
        for item in summary.moved_down:
            old = item.old_position or "?"
            lines.append(
                f"  #{old} -> #{item.position}: {item.name} — {item.artists}"
                + (f" (played {item.last_played_at})" if item.last_played_at else "")
            )
    else:
        lines.extend(["", "Moved down: none (order unchanged or no previous snapshot)."])

    if summary.up_next:
        lines.extend(["", "Up next (top of playlist):"])
        for item in summary.up_next:
            played = item.last_played_at or "never recorded"
            lines.append(f"  #{item.position}: {item.name} — {item.artists} ({played})")

    return "\n".join(lines)


def refresh_playlist(
    client: SpotifyClient,
    config: Config,
    history: HistoryStore,
) -> RefreshSummary:
    playlist_id = get_or_create_playlist(client, config)
    old_order = load_previous_order(config)

    liked_tracks = fetch_liked_tracks(client)
    track_ids = [track_id for track_id, _, _ in liked_tracks]
    liked_details = liked_tracks_as_details(liked_tracks)
    last_played = history.get_last_played_map()
    ordered_ids = order_tracks(track_ids, last_played)
    uris = [f"spotify:track:{track_id}" for track_id in ordered_ids]
    set_playlist_tracks(client, playlist_id, uris)
    save_current_order(config, ordered_ids)

    details = liked_details
    reordered, moved_down, up_next = _summarize_changes(
        old_order, ordered_ids, last_played, details
    )
    never_played = sum(1 for track_id in track_ids if last_played.get(track_id) is None)

    return RefreshSummary(
        playlist_id=playlist_id,
        playlist_name=config.playlist_name,
        total_tracks=len(ordered_ids),
        tracks_reordered=reordered,
        never_played=never_played,
        with_history=len(track_ids) - never_played,
        moved_down=moved_down,
        up_next=up_next,
    )
