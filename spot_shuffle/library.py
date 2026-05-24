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
    items = client.get_paginated("/me/tracks", params={"limit": 50})
    track_ids: list[str] = []
    for item in items:
        track = item.get("track")
        if not track or not track.get("id"):
            continue
        track_ids.append(track["id"])
    return track_ids
