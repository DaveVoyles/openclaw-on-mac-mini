"""Container resource monitoring with configurable thresholds and JSON persistence."""

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from subprocess_utils import run as _run

log = logging.getLogger("openclaw")

MONITOR_FILE = Path(os.getenv("RESOURCE_MONITOR_PATH", "/app/data/resource_monitors.json"))


@dataclass
class ResourceThreshold:
    container: str
    cpu_percent: float = 80.0
    memory_percent: float = 90.0
    enabled: bool = True
    last_alert: float = 0.0
    cooldown_seconds: int = 300


class ResourceMonitor:
    """Tracks per-container CPU/memory thresholds and checks for violations."""

    def __init__(self):
        self._thresholds: dict[str, ResourceThreshold] = {}
        self._lock = asyncio.Lock()
        self._load()

    def _load(self):
        if MONITOR_FILE.exists():
            try:
                data = json.loads(MONITOR_FILE.read_text())
                for name, t in data.items():
                    self._thresholds[name] = ResourceThreshold(**t)
            except Exception as e:
                log.warning("Failed to load resource monitors: %s", e)

    def _save(self):
        MONITOR_FILE.parent.mkdir(parents=True, exist_ok=True)
        MONITOR_FILE.write_text(
            json.dumps({k: asdict(v) for k, v in self._thresholds.items()}, indent=2)
        )

    def set_threshold(
        self, container: str, cpu: float = 80.0, memory: float = 90.0
    ) -> ResourceThreshold:
        t = ResourceThreshold(container=container, cpu_percent=cpu, memory_percent=memory)
        self._thresholds[container] = t
        self._save()
        return t

    def remove(self, container: str) -> bool:
        if container in self._thresholds:
            del self._thresholds[container]
            self._save()
            return True
        return False

    def list_all(self) -> list[ResourceThreshold]:
        return list(self._thresholds.values())

    async def _get_stats_raw(self) -> list[dict]:
        """Fetch per-container CPU% and Memory% via docker stats."""
        rc, out, err = await _run(
            [
                "docker", "stats", "--no-stream",
                "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemPerc}}",
            ],
            timeout=30,
        )
        if rc != 0:
            log.debug("docker stats failed: %s", err)
            return []

        results = []
        for line in out.strip().splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            try:
                results.append({
                    "name": parts[0].strip(),
                    "cpu": float(parts[1].strip().rstrip("%")),
                    "memory": float(parts[2].strip().rstrip("%")),
                })
            except (ValueError, IndexError):
                continue
        return results

    async def check_all(self) -> list[tuple[ResourceThreshold, dict]]:
        """Check all monitored containers. Returns (threshold, stats) for violations."""
        if not self._thresholds:
            return []

        now = time.time()
        violations: list[tuple[ResourceThreshold, dict]] = []

        try:
            stats = await self._get_stats_raw()
        except Exception as e:
            log.debug("Resource monitor stats fetch failed: %s", e)
            return []

        stats_by_name = {s["name"].lower(): s for s in stats}

        for name, threshold in self._thresholds.items():
            if not threshold.enabled:
                continue
            if now - threshold.last_alert < threshold.cooldown_seconds:
                continue

            s = stats_by_name.get(name.lower())
            if s is None:
                continue

            if s["cpu"] > threshold.cpu_percent or s["memory"] > threshold.memory_percent:
                threshold.last_alert = now
                self._save()
                violations.append((threshold, {"cpu": s["cpu"], "memory": s["memory"]}))

        return violations


resource_monitor = ResourceMonitor()
