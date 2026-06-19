import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

SCOPES = " ".join(
    [
        "user-library-read",
        "user-read-recently-played",
        "playlist-modify-private",
        "playlist-read-private",
    ]
)

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"


@dataclass(frozen=True)
class Config:
    client_id: str
    client_secret: str
    redirect_uri: str
    playlist_name: str
    playlist_id: str | None
    db_path: Path
    tokens_path: Path
    sync_interval_minutes: int


def load_config() -> Config:
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        raise SystemExit(
            "Missing SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET. "
            "Copy .env.example to .env and fill in your Spotify app credentials."
        )

    return Config(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=os.environ.get(
            "SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8080/callback"
        ),
        playlist_name=os.environ.get("SPOTIFY_PLAYLIST_NAME", "Least Heard"),
        playlist_id=os.environ.get("SPOTIFY_PLAYLIST_ID") or None,
        db_path=Path(os.environ.get("SPOTIFY_DB_PATH", "data/spot_shuffle.db")),
        tokens_path=Path(os.environ.get("SPOTIFY_TOKENS_PATH", ".tokens.json")),
        sync_interval_minutes=int(os.environ.get("SYNC_INTERVAL_MINUTES", "15")),
    )
