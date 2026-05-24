import time

from spot_shuffle.config import load_config
from spot_shuffle.history import HistoryStore, sync_recently_played
from spot_shuffle.playlist import refresh_playlist
from spot_shuffle.spotify import SpotifyClient
from spot_shuffle.auth import TokenStore


def run_loop(sync_interval_minutes: int) -> None:
    config = load_config()
    store = TokenStore(config.tokens_path)
    client = SpotifyClient(config, store)
    history = HistoryStore(config.db_path)

    print(f"Running sync + refresh every {sync_interval_minutes} minutes. Ctrl+C to stop.")
    while True:
        try:
            updated = sync_recently_played(client, history)
            playlist_id, count = refresh_playlist(client, config, history)
            print(
                f"Synced {updated} recent plays; refreshed playlist "
                f"'{config.playlist_name}' ({count} tracks, id={playlist_id})"
            )
        except Exception as exc:
            print(f"Error during sync/refresh: {exc}")
        time.sleep(sync_interval_minutes * 60)
