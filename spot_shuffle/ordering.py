import random


def order_tracks(
    track_ids: list[str],
    last_played: dict[str, str | None],
) -> list[str]:
    never_played = [tid for tid in track_ids if last_played.get(tid) is None]
    played = [tid for tid in track_ids if last_played.get(tid) is not None]

    random.shuffle(never_played)
    played.sort(key=lambda tid: (last_played[tid], tid))

    return never_played + played
