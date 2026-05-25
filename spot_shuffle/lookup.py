from dataclasses import dataclass

from spot_shuffle.config import Config
from spot_shuffle.history import HistoryStore
from spot_shuffle.library import LikedTrack, fetch_liked_tracks
from spot_shuffle.playlist import compute_expected_order_from_liked, load_snapshot
from spot_shuffle.spotify import SpotifyClient


@dataclass
class TrackLookup:
    track_id: str
    name: str
    artists: str
    added_at: str | None
    last_played_at: str | None
    history_updated_at: str | None
    in_liked_songs: bool
    expected_position: int | None
    expected_total: int
    written_position: int | None
    written_total: int
    written_updated_at: str | None
    play_rank: int | None
    played_total: int


def _position(order: list[str], track_id: str) -> int | None:
    try:
        return order.index(track_id) + 1
    except ValueError:
        return None


def _play_rank(track_id: str, last_played: dict[str, str | None]) -> tuple[int | None, int]:
    played = [
        (tid, ts)
        for tid, ts in last_played.items()
        if ts is not None
    ]
    if not played:
        return None, 0
    played.sort(key=lambda item: item[1], reverse=True)
    played_ids = [tid for tid, _ in played]
    try:
        return played_ids.index(track_id) + 1, len(played_ids)
    except ValueError:
        return None, len(played_ids)


def _history_status(last_played_at: str | None, play_rank: int | None, played_total: int) -> str:
    if last_played_at is None:
        return "never recorded in local history"
    if play_rank == 1:
        return "most recently played in your history"
    if play_rank is not None and play_rank <= 5:
        return f"recently played (#{play_rank} of {played_total} tracked)"
    return "played before (stale — should be toward playlist bottom after refresh)"


def _position_note(position: int | None, total: int, last_played_at: str | None) -> str:
    if position is None or total == 0:
        return "not in playlist order"
    if position == 1:
        return "top — up next"
    if position == total:
        return "bottom — most recently played"
    if last_played_at is None:
        return "never-heard section (top half of playlist)"
    if position > total * 0.8:
        return "near bottom — recently played"
    return "middle of playlist"


def search_liked_tracks(tracks: list[LikedTrack], query: str) -> list[LikedTrack]:
    raw = query.strip()
    needle = raw.lower()
    if not needle:
        return []

    if len(raw) == 22 and raw.isalnum():
        return [track for track in tracks if track.track_id.lower() == needle]

    matches: list[tuple[int, LikedTrack]] = []
    for track in tracks:
        name = track.name.lower()
        artists = track.artists.lower()
        if needle == name:
            matches.append((0, track))
        elif name.startswith(needle):
            matches.append((1, track))
        elif needle in name:
            matches.append((2, track))
        elif needle in artists:
            matches.append((3, track))
    matches.sort(key=lambda item: (item[0], item[1].name.lower()))
    return [track for _, track in matches]


def lookup_track(
    client: SpotifyClient,
    config: Config,
    history: HistoryStore,
    query: str,
) -> list[TrackLookup]:
    liked_tracks = fetch_liked_tracks(client)
    matches = search_liked_tracks(liked_tracks, query)
    if not matches:
        return []

    expected_order, _, last_played = compute_expected_order_from_liked(liked_tracks, history)
    snapshot = load_snapshot(config)
    written_order = snapshot.get("track_ids", [])
    written_updated_at = snapshot.get("updated_at")
    history_rows = history.get_track_records()

    results: list[TrackLookup] = []
    for track in matches:
        record = history_rows.get(track.track_id, {})
        play_rank, played_total = _play_rank(track.track_id, last_played)
        results.append(
            TrackLookup(
                track_id=track.track_id,
                name=track.name,
                artists=track.artists,
                added_at=track.added_at,
                last_played_at=record.get("last_played_at"),
                history_updated_at=record.get("updated_at"),
                in_liked_songs=True,
                expected_position=_position(expected_order, track.track_id),
                expected_total=len(expected_order),
                written_position=_position(written_order, track.track_id),
                written_total=len(written_order),
                written_updated_at=written_updated_at,
                play_rank=play_rank,
                played_total=played_total,
            )
        )
    return results


def format_track_lookup(result: TrackLookup, *, playlist_name: str) -> str:
    lines = [
        f"Track: {result.name} — {result.artists}",
        "=" * min(60, len(result.name) + len(result.artists) + 10),
        f"Track ID:           {result.track_id}",
        f"In Liked Songs:     yes",
        f"Liked on:           {result.added_at or 'unknown'}",
        f"Last played:        {result.last_played_at or 'never recorded'}",
        f"History updated:    {result.history_updated_at or 'n/a'}",
        f"History status:     {_history_status(result.last_played_at, result.play_rank, result.played_total)}",
        "",
        f"Playlist:           {playlist_name}",
    ]

    if result.expected_position is not None:
        lines.append(
            f"Expected position:  #{result.expected_position} of {result.expected_total}"
            f" ({_position_note(result.expected_position, result.expected_total, result.last_played_at)})"
        )
    else:
        lines.append("Expected position:  not in current order")

    if result.written_position is not None:
        lines.append(
            f"Last refresh:       #{result.written_position} of {result.written_total}"
            f" ({_position_note(result.written_position, result.written_total, result.last_played_at)})"
        )
        if result.written_updated_at:
            lines.append(f"Last refresh at:    {result.written_updated_at}")
    else:
        lines.append("Last refresh:       not in snapshot — run refresh")

    if (
        result.expected_position is not None
        and result.written_position is not None
        and result.expected_position != result.written_position
    ):
        delta = result.expected_position - result.written_position
        direction = "down" if delta > 0 else "up"
        lines.append(
            f"Position change:    {abs(delta)} spots {direction} since last refresh"
        )
    elif result.expected_position == result.written_position and result.written_position is not None:
        lines.append("Position change:    unchanged since last refresh")

    if result.play_rank is not None:
        lines.append(
            f"Play recency rank:  #{result.play_rank} of {result.played_total} tracked songs"
        )

    return "\n".join(lines)


def format_lookup_results(
    results: list[TrackLookup],
    *,
    query: str,
    playlist_name: str,
    limit: int,
) -> str:
    if not results:
        return f"No liked songs matched '{query}'."

    if len(results) > limit:
        lines = [f"Found {len(results)} matches for '{query}' (showing first {limit}):\n"]
        for index, result in enumerate(results[:limit], start=1):
            pos = result.expected_position or "?"
            played = result.last_played_at or "never"
            lines.append(
                f"  {index}. {result.name} — {result.artists}"
                f"  (#{pos}, last played: {played})"
            )
        lines.append(f"\nRun again with a more specific query, or: lookup \"{results[0].name}\"")
        return "\n".join(lines)

    sections = [
        format_track_lookup(result, playlist_name=playlist_name)
        for result in results
    ]
    return "\n\n".join(sections)
