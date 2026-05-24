import argparse
import sys

from spot_shuffle.auth import TokenStore, run_auth_flow
from spot_shuffle.config import load_config
from spot_shuffle.history import HistoryStore, format_sync_summary, sync_recently_played
from spot_shuffle.library import fetch_liked_track_ids
from spot_shuffle.playlist import format_refresh_summary, refresh_playlist
from spot_shuffle.scheduler import run_loop
from spot_shuffle.spotify import SpotifyClient


def cmd_auth() -> None:
    config = load_config()
    store = TokenStore(config.tokens_path)
    run_auth_flow(config, store)


def cmd_sync() -> None:
    config = load_config()
    store = TokenStore(config.tokens_path)
    client = SpotifyClient(config, store)
    history = HistoryStore(config.db_path)
    result = sync_recently_played(client, history)
    print(format_sync_summary(result))
    print(f"\nDatabase: {config.db_path}")


def cmd_refresh() -> None:
    config = load_config()
    store = TokenStore(config.tokens_path)
    client = SpotifyClient(config, store)
    history = HistoryStore(config.db_path)
    summary = refresh_playlist(client, config, history)
    print(format_refresh_summary(summary))


def cmd_status() -> None:
    config = load_config()
    store = TokenStore(config.tokens_path)
    client = SpotifyClient(config, store)
    history = HistoryStore(config.db_path)

    liked_count = len(fetch_liked_track_ids(client))
    with_history = history.count_with_history()
    oldest, newest = history.oldest_and_newest()

    print(f"Liked Songs:              {liked_count}")
    print(f"Tracks with play history: {with_history}")
    print(f"Never recorded in DB:     {liked_count - with_history}")
    print(f"Oldest last_played_at:    {oldest or 'n/a'}")
    print(f"Newest last_played_at:    {newest or 'n/a'}")
    print(f"Database:                 {config.db_path}")
    print(f"Target playlist:          {config.playlist_name}")


def cmd_run(args: argparse.Namespace) -> None:
    config = load_config()
    interval = args.interval or config.sync_interval_minutes
    run_loop(interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spot_shuffle",
        description="Maintain a Spotify playlist sorted by least recently heard.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("auth", help="Authorize with Spotify (one-time setup)")
    subparsers.add_parser("sync", help="Sync recently played tracks into local DB")
    subparsers.add_parser(
        "refresh", help="Rebuild the Least Heard playlist from Liked Songs"
    )
    subparsers.add_parser("status", help="Show library and history stats")

    run_parser = subparsers.add_parser(
        "run", help="Loop: sync + refresh on an interval"
    )
    run_parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Minutes between sync/refresh cycles (default: SYNC_INTERVAL_MINUTES)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    commands = {
        "auth": cmd_auth,
        "sync": cmd_sync,
        "refresh": cmd_refresh,
        "status": cmd_status,
        "run": cmd_run,
    }
    if args.command == "run":
        cmd_run(args)
    else:
        commands[args.command]()


if __name__ == "__main__":
    main(sys.argv[1:])
