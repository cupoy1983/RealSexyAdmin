"""持久化主动导航间隔及冷却；目标站、任务及失败分类由调用方决定。"""

import asyncio
import math
import time
from pathlib import Path

from .state import read_private_json, write_private_json


class TrafficGate:
    def __init__(self, path: Path, interval_seconds=5, *, clock=time.time, sleep=asyncio.sleep):
        self.path, self.clock, self.sleep = path, clock, sleep
        self.interval = max(0, float(interval_seconds))
        loaded = read_private_json(path)
        self.state = loaded if path.exists() else {"last_navigation": 0, "cooldown_until": 0, "reason": None}
        if not isinstance(self.state, dict) or any(type(self.state.get(key)) not in (int, float) or not math.isfinite(self.state[key])
                                                  or self.state[key] < 0 for key in ("last_navigation", "cooldown_until")):
            raise ValueError("访问节奏状态无效，停止领取任务")

    @property
    def remaining(self):
        return max(0, self.state["cooldown_until"] - self.clock())

    async def navigate(self, interval_seconds=0):
        delay = max(self.remaining, self.state["last_navigation"] + max(self.interval, interval_seconds) - self.clock())
        if delay > 0:
            await self.sleep(delay)
        self.state["last_navigation"] = self.clock()
        write_private_json(self.path, self.state)

    def cooldown(self, seconds, reason):
        seconds = float(seconds)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("冷却时间无效")
        self.state.update(cooldown_until=max(self.state["cooldown_until"], self.clock() + seconds), reason=str(reason)[:80])
        write_private_json(self.path, self.state)
