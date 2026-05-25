import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from spot_shuffle.config import Config
from spot_shuffle.history import HistoryStore
from spot_shuffle.library import fetch_liked_tracks, liked_tracks_as_details
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
    data = load_snapshot(config)
    return data.get("track_ids", [])


def load_snapshot(config: Config) -> dict:
    path = _order_snapshot_path(config)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    if isinstance(raw, list):
        return {"track_ids": raw, "updated_at": None}
    return raw


def save_current_order(config: Config, track_ids: list[str]) -> None:
    path = _order_snapshot_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "track_ids": track_ids,
    }
    path.write_text(json.dumps(payload, indent=2))


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


def compute_expected_order_from_liked(
    liked_tracks: list,
    history: HistoryStore,
) -> tuple[list[str], dict[str, dict], dict[str, str | None]]:
    track_ids = [track.track_id for track in liked_tracks]
    details = liked_tracks_as_details(liked_tracks)
    last_played = history.get_last_played_map()
    return order_tracks(track_ids, last_played), details, last_played


def compute_expected_order(
    client: SpotifyClient,
    history: HistoryStore,
) -> tuple[list[str], dict[str, dict], dict[str, str | None]]:
    return compute_expected_order_from_liked(fetch_liked_tracks(client), history)


def _positions_match(left: list[str], right: list[str]) -> tuple[bool, int]:
    if len(left) != len(right):
        return False, max(len(left), len(right))
    mismatches = sum(1 for a, b in zip(left, right) if a != b)
    return mismatches == 0, mismatches


def _find_mismatches(
    expected: list[str],
    actual: list[str],
    details: dict[str, dict],
    limit: int = 5,
) -> list[tuple[int, str, str]]:
    mismatches: list[tuple[int, str, str]] = []
    for index, (exp_id, act_id) in enumerate(zip(expected, actual)):
        if exp_id == act_id:
            continue
        exp_track = details.get(exp_id, {})
        act_track = details.get(act_id, {})
        exp_label = f"{exp_track.get('name', exp_id)} — {', '.join(a.get('name', '') for a in exp_track.get('artists', []))}"
        act_label = f"{act_track.get('name', act_id)} — {', '.join(a.get('name', '') for a in act_track.get('artists', []))}"
        mismatches.append((index + 1, exp_label, act_label))
        if len(mismatches) >= limit:
            break
    return mismatches


def _format_track_line(
    track_id: str,
    position: int,
    details: dict[str, dict],
    last_played: dict[str, str | None],
    *,
    match: str | None = None,
) -> str:
    track = details.get(track_id, {})
    name = track.get("name") or track_id
    artists = ", ".join(a.get("name", "") for a in track.get("artists", []))
    played = last_played.get(track_id)
    played_note = "never recorded" if played is None else f"played {played}"
    suffix = f" {match}" if match else ""
    return f"  #{position}: {name} — {artists} ({played_note}){suffix}"


def _format_tail_preview(
    title: str,
    order: list[str],
    details: dict[str, dict],
    last_played: dict[str, str | None],
    preview: int,
    compare: list[str] | None = None,
) -> list[str]:
    lines = [title]
    if not order:
        lines.append("  (empty)")
        return lines

    start = max(len(order) - preview, 0)
    for index, track_id in enumerate(order[start:], start=start):
        match = None
        if compare is not None:
            match = "✓" if index < len(compare) and compare[index] == track_id else "✗"
        lines.append(
            _format_track_line(track_id, index + 1, details, last_played, match=match)
        )
    return lines


def _format_order_preview(
    title: str,
    order: list[str],
    details: dict[str, dict],
    last_played: dict[str, str | None],
    preview: int,
    compare: list[str] | None = None,
) -> list[str]:
    lines = [title]
    if not order:
        lines.append("  (empty)")
        return lines

    for index, track_id in enumerate(order[:preview]):
        match = None
        if compare is not None:
            match = "✓" if index < len(compare) and compare[index] == track_id else "✗"
        lines.append(
            _format_track_line(track_id, index + 1, details, last_played, match=match)
        )

    if len(order) > preview * 2:
        lines.append(f"  ... ({len(order) - preview * 2} tracks omitted) ...")

    if len(order) > preview:
        start = max(len(order) - preview, preview)
        for index, track_id in enumerate(order[start:], start=start):
            match = None
            if compare is not None:
                match = "✓" if index < len(compare) and compare[index] == track_id else "✗"
            lines.append(
                _format_track_line(track_id, index + 1, details, last_played, match=match)
            )

    return lines


@dataclass
class VerifySummary:
    playlist_id: str
    playlist_name: str
    expected: list[str]
    written: list[str]
    written_updated_at: str | None
    spotify: list[str] | None
    spotify_error: str | None
    details: dict[str, dict]
    last_played: dict[str, str | None]
    preview: int = 5


def verify_playlist(
    client: SpotifyClient,
    config: Config,
    history: HistoryStore,
    *,
    preview: int = 5,
) -> VerifySummary:
    playlist_id = get_or_create_playlist(client, config)
    expected, details, last_played = compute_expected_order(client, history)
    snapshot = load_snapshot(config)
    written = snapshot.get("track_ids", [])

    spotify: list[str] | None = None
    spotify_error: str | None = None
    try:
        spotify = get_playlist_track_ids(client, playlist_id)
    except Exception as exc:
        spotify_error = str(exc)

    return VerifySummary(
        playlist_id=playlist_id,
        playlist_name=config.playlist_name,
        expected=expected,
        written=written,
        written_updated_at=snapshot.get("updated_at"),
        spotify=spotify,
        spotify_error=spotify_error,
        details=details,
        last_played=last_played,
        preview=preview,
    )


def format_verify_summary(summary: VerifySummary) -> str:
    preview = summary.preview
    expected = summary.expected
    written = summary.written
    spotify = summary.spotify
    details = summary.details
    last_played = summary.last_played

    bottom_expected = expected[-preview:] if expected else []
    bottom_written = written[-preview:] if written else []
    bottom_spotify = spotify[-preview:] if spotify else []
    bottom_matches_written = bool(written) and bottom_expected == bottom_written
    bottom_matches_spotify = bool(spotify) and bottom_expected == bottom_spotify

    lines = [
        "Playlist verify",
        "===============",
        f"Playlist: {summary.playlist_name} ({summary.playlist_id})",
        f"Tracks: {len(expected)} liked songs",
    ]
    if summary.written_updated_at:
        lines.append(f"Last refresh: {summary.written_updated_at}")
    elif written:
        lines.append("Last refresh: unknown time (legacy snapshot)")
    else:
        lines.append("Last refresh: never — run refresh first")

    lines.extend(["", "Health check:"])
    if not written:
        lines.append("  No refresh snapshot yet. Run: python -m spot_shuffle.cli refresh")
    elif bottom_matches_written:
        lines.append("  Bottom of playlist is up to date ✓")
        lines.append("  (Recently played tracks match what a refresh would produce)")
    else:
        lines.append("  Bottom of playlist is stale ✗")
        lines.append("  Run: python -m spot_shuffle.cli sync && python -m spot_shuffle.cli refresh")

    if spotify is not None:
        if bottom_matches_spotify:
            lines.append("  Spotify live playlist matches expected bottom ✓")
        else:
            lines.append("  Spotify live playlist differs from expected ✗")
    else:
        lines.append("  Spotify live read unavailable (API restriction on this app)")
        lines.append("  Compare the snapshot below manually in the Spotify app")

    lines.extend(["", "Note: top of playlist shuffles never-heard tracks each refresh."])
    lines.extend(["      Focus on the bottom — that is where recently played songs should be."])

    reference = spotify if spotify is not None else written
    reference_label = "Spotify" if spotify is not None else "Last refresh"

    lines.extend(
        _format_order_preview(
            "\nExpected now (top — shuffles each refresh):",
            expected,
            details,
            last_played,
            preview,
        )
    )
    lines.extend(
        _format_tail_preview(
            f"\nExpected now (bottom — should match {reference_label.lower()}):",
            expected,
            details,
            last_played,
            preview,
            compare=reference,
        )
    )
    if reference:
        lines.extend(
            _format_order_preview(
                f"\n{reference_label} (what should be on Spotify):",
                reference,
                details,
                last_played,
                preview,
            )
        )
        lines.extend(
            _format_tail_preview(
                f"\n{reference_label} (bottom):",
                reference,
                details,
                last_played,
                preview,
                compare=expected,
            )
        )

    if spotify is not None and not bottom_matches_spotify:
        mismatches = _find_mismatches(expected, spotify, details, limit=preview)
        if mismatches:
            lines.extend(["", "Bottom mismatches (expected vs Spotify):"])
            for position, exp_label, spotify_label in mismatches:
                if position <= len(expected) - preview:
                    continue
                lines.append(f"  #{position}: expected {exp_label}")
                lines.append(f"         spotify  {spotify_label}")
    elif written and not bottom_matches_written:
        lines.extend(["", "Bottom mismatches (expected now vs last refresh):"])
        for index, (exp_id, wr_id) in enumerate(zip(bottom_expected, bottom_written)):
            if exp_id == wr_id:
                continue
            position = len(expected) - preview + index + 1
            exp_track = details.get(exp_id, {})
            wr_track = details.get(wr_id, {})
            exp_label = f"{exp_track.get('name', exp_id)} — {', '.join(a.get('name', '') for a in exp_track.get('artists', []))}"
            wr_label = f"{wr_track.get('name', wr_id)} — {', '.join(a.get('name', '') for a in wr_track.get('artists', []))}"
            lines.append(f"  #{position}: expected now {exp_label}")
            lines.append(f"         last refresh {wr_label}")

    return "\n".join(lines)


def refresh_playlist(
    client: SpotifyClient,
    config: Config,
    history: HistoryStore,
) -> RefreshSummary:
    playlist_id = get_or_create_playlist(client, config)
    old_order = load_previous_order(config)

    liked_tracks = fetch_liked_tracks(client)
    track_ids = [track.track_id for track in liked_tracks]
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
