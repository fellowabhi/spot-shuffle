import tempfile
import unittest
from pathlib import Path

from spot_shuffle.history import HistoryStore
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


if __name__ == "__main__":
    unittest.main()
