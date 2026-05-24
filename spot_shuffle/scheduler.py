import time

from spot_shuffle.config import load_config
from spot_shuffle.history import HistoryStore, format_sync_summary, sync_recently_played
from spot_shuffle.playlist import format_refresh_summary, refresh_playlist
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
            sync_result = sync_recently_played(client, history)
            summary = refresh_playlist(client, config, history)
            print(format_sync_summary(sync_result))
            print()
            print(format_refresh_summary(summary))
        except Exception as exc:
            print(f"Error during sync/refresh: {exc}")
        time.sleep(sync_interval_minutes * 60)
