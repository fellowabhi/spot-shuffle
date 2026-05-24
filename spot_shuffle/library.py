from spot_shuffle.spotify import SpotifyClient


def fetch_liked_track_uris(client: SpotifyClient) -> list[str]:
    items = client.get_paginated("/me/tracks", params={"limit": 50})
    uris: list[str] = []
    for item in items:
        track = item.get("track")
        if not track or not track.get("id"):
            continue
        uris.append(track["uri"])
    return uris


def fetch_liked_track_ids(client: SpotifyClient) -> list[str]:
    return [track_id for track_id, _, _ in fetch_liked_tracks(client)]


def fetch_liked_tracks(client: SpotifyClient) -> list[tuple[str, str, str]]:
    items = client.get_paginated("/me/tracks", params={"limit": 50})
    tracks: list[tuple[str, str, str]] = []
    for item in items:
        track = item.get("track")
        if not track or not track.get("id"):
            continue
        name = track.get("name") or "Unknown"
        artists = ", ".join(a.get("name", "") for a in track.get("artists", []))
        tracks.append((track["id"], name, artists))
    return tracks


def liked_tracks_as_details(tracks: list[tuple[str, str, str]]) -> dict[str, dict]:
    return {
        track_id: {"id": track_id, "name": name, "artists": [{"name": artists}]}
        for track_id, name, artists in tracks
    }
