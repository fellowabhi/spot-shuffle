import time

from spot_shuffle.config import Config
from spot_shuffle.library import fetch_liked_track_ids
from spot_shuffle.history import HistoryStore
from spot_shuffle.ordering import order_tracks
from spot_shuffle.spotify import SpotifyClient

BATCH_SIZE = 100
BATCH_SLEEP_SECONDS = 0.2


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


def refresh_playlist(
    client: SpotifyClient,
    config: Config,
    history: HistoryStore,
) -> tuple[str, int]:
    playlist_id = get_or_create_playlist(client, config)
    track_ids = fetch_liked_track_ids(client)
    last_played = history.get_last_played_map()
    ordered_ids = order_tracks(track_ids, last_played)
    uris = [f"spotify:track:{track_id}" for track_id in ordered_ids]
    set_playlist_tracks(client, playlist_id, uris)
    return playlist_id, len(uris)
