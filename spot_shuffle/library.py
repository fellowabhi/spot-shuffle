from dataclasses import dataclass

from spot_shuffle.spotify import SpotifyClient


@dataclass
class LikedTrack:
    track_id: str
    name: str
    artists: str
    added_at: str | None


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
    return [track.track_id for track in fetch_liked_tracks(client)]


def fetch_liked_tracks(client: SpotifyClient) -> list[LikedTrack]:
    items = client.get_paginated("/me/tracks", params={"limit": 50})
    tracks: list[LikedTrack] = []
    for item in items:
        track = item.get("track")
        if not track or not track.get("id"):
            continue
        name = track.get("name") or "Unknown"
        artists = ", ".join(a.get("name", "") for a in track.get("artists", []))
        tracks.append(
            LikedTrack(
                track_id=track["id"],
                name=name,
                artists=artists,
                added_at=item.get("added_at"),
            )
        )
    return tracks


def liked_tracks_as_details(tracks: list[LikedTrack]) -> dict[str, dict]:
    return {
        track.track_id: {
            "id": track.track_id,
            "name": track.name,
            "artists": [{"name": track.artists}],
        }
        for track in tracks
    }
