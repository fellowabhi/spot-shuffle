import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from spot_shuffle.history import HistoryStore
from spot_shuffle.library import LikedTrack
from spot_shuffle.lookup import search_liked_tracks
from spot_shuffle.ordering import order_tracks


class OrderingTests(unittest.TestCase):
    def test_never_played_first_then_oldest_played(self) -> None:
        track_ids = ["a", "b", "c", "d"]
        last_played = {
            "c": "2024-01-03T00:00:00Z",
            "d": "2024-01-01T00:00:00Z",
        }
        ordered = order_tracks(track_ids, last_played)
        self.assertEqual(set(ordered[:2]), {"a", "b"})
        self.assertEqual(ordered[2:], ["d", "c"])

    def test_stable_sort_for_same_timestamp(self) -> None:
        track_ids = ["z", "y", "x"]
        last_played = {
            "z": "2024-01-01T00:00:00Z",
            "y": "2024-01-01T00:00:00Z",
            "x": "2024-01-02T00:00:00Z",
        }
        ordered = order_tracks(track_ids, last_played)
        self.assertEqual(ordered, ["y", "z", "x"])


class HistoryTests(unittest.TestCase):
    def test_upsert_keeps_newest_played_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = HistoryStore(Path(tmp) / "test.db")
            store.upsert_play("track1", "2024-01-01T00:00:00Z")
            store.upsert_play("track1", "2024-01-05T00:00:00Z")
            store.upsert_play("track1", "2024-01-03T00:00:00Z")

            last_played = store.get_last_played_map()
            self.assertEqual(last_played["track1"], "2024-01-05T00:00:00Z")

    def test_count_with_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = HistoryStore(Path(tmp) / "test.db")
            store.upsert_play("track1", "2024-01-01T00:00:00Z")
            store.upsert_play("track2", "2024-01-02T00:00:00Z")
            self.assertEqual(store.count_with_history(), 2)


class LookupTests(unittest.TestCase):
    def test_search_by_name_and_track_id(self) -> None:
        tracks = [
            LikedTrack("3IhM5Mber8KA0NaRNpK2px", "Lay Low", "Tiësto", None),
            LikedTrack("def4567890123456789012", "Lose Control", "Teddy Swims", None),
        ]
        self.assertEqual(search_liked_tracks(tracks, "lay low")[0].name, "Lay Low")
        self.assertEqual(
            search_liked_tracks(tracks, "3IhM5Mber8KA0NaRNpK2px")[0].track_id,
            "3IhM5Mber8KA0NaRNpK2px",
        )


class SpotifyRetryTests(unittest.TestCase):
    def test_retry_after_header(self) -> None:
        from spot_shuffle.spotify import retry_after_seconds

        response = MagicMock(headers={"Retry-After": "12"})
        self.assertEqual(retry_after_seconds(response), 12.0)

    def test_retry_after_missing_uses_default(self) -> None:
        from spot_shuffle.spotify import DEFAULT_RETRY_AFTER_SECONDS, retry_after_seconds

        response = MagicMock(headers={})
        self.assertEqual(retry_after_seconds(response), DEFAULT_RETRY_AFTER_SECONDS)

    @patch("spot_shuffle.spotify.time.sleep")
    @patch("spot_shuffle.spotify.requests.request")
    def test_request_retries_on_429(self, mock_request, mock_sleep) -> None:
        from spot_shuffle.auth import TokenStore
        from spot_shuffle.config import Config
        from spot_shuffle.spotify import SpotifyClient

        rate_limited = MagicMock(status_code=429, headers={"Retry-After": "2"})
        ok = MagicMock(status_code=200, content=b"{}", headers={})
        ok.raise_for_status = MagicMock()
        mock_request.side_effect = [rate_limited, ok]

        config = Config(
            client_id="id",
            client_secret="secret",
            redirect_uri="http://127.0.0.1:8080/callback",
            playlist_name="Least Heard",
            playlist_id=None,
            db_path=Path("data/spot_shuffle.db"),
            tokens_path=Path(".tokens.json"),
            sync_interval_minutes=15,
        )
        store = MagicMock(spec=TokenStore)
        store.load.return_value = {"access_token": "token", "refresh_token": "refresh"}

        client = SpotifyClient(config, store)
        response = client.request("GET", "/me")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_request.call_count, 2)
        mock_sleep.assert_called_once_with(2.0)


if __name__ == "__main__":
    unittest.main()
