"""Date-block rolling aggregates used by the active V1 semantic port."""

from collections import deque


class RollingIndex:
    def __init__(self, days: int) -> None:
        self.days = days
        self.state: dict[object, dict[str, object]] = {}

    def add_daily(self, source_date, daily: dict[object, list[object]]) -> None:
        for key, item in daily.items():
            if key is None:
                continue
            state = self.state.setdefault(key, {"queue": deque(), "starts": 0, "wins": 0, "top3": 0})
            starts, wins, top3 = item
            state["queue"].append((source_date, starts, wins, top3))
            state["starts"] += starts
            state["wins"] += wins
            state["top3"] += top3

    def get(self, key, target_date):
        if key is None or key not in self.state:
            return 0, 0, 0
        state = self.state[key]
        cutoff = target_date.fromordinal(target_date.toordinal() - self.days)
        queue = state["queue"]
        while queue and queue[0][0] < cutoff:
            _, starts, wins, top3 = queue.popleft()
            state["starts"] -= starts
            state["wins"] -= wins
            state["top3"] -= top3
        return state["starts"], state["wins"], state["top3"]
