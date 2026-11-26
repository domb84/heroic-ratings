"""Battery level and the system health log (memory, swap, pressure, top processes)."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path


# ------------------------------------------------------------------- system health log
#
# To find out what's happening when the whole Deck freezes (e.g. during big installs):
# memory, swap, unwritten disk data, the kernel's pressure figures and the biggest
# processes, every 30 s, or every 5 s while Heroic is downloading / uninstalling.
# Each line is fsync'ed so it survives a hard reset.

def battery(root: Path = Path("/sys/class/power_supply")) -> dict | None:
    """{"percent": 0-100, "charging": bool} for the first battery, or None (no battery)."""
    try:
        supplies = sorted(root.iterdir())
    except OSError:
        return None
    for d in supplies:
        try:
            if (d / "type").read_text().strip() != "Battery":
                continue
            pct = int((d / "capacity").read_text().strip())
            status = (d / "status").read_text().strip() if (d / "status").exists() else ""
        except (OSError, ValueError):
            continue
        return {"percent": max(0, min(100, pct)), "charging": status in ("Charging", "Full"), "status": status}
    return None


def parse_meminfo(text: str) -> dict[str, int]:
    """/proc/meminfo -> {field: kB}."""
    out = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        parts = v.split()
        if parts and parts[0].isdigit():
            out[k.strip()] = int(parts[0])
    return out


def parse_psi(text: str) -> dict[str, float]:
    """/proc/pressure/<x> -> {"some": avg10, "full": avg10} (percent of time stalled, last 10 s)."""
    out = {}
    for line in text.splitlines():
        kind, *fields = line.split()
        for f in fields:
            if f.startswith("avg10="):
                out[kind] = float(f[6:])
    return out


def top_processes(n: int = 6, proc: Path = Path("/proc")) -> list[list]:
    """[[rss MB, name, pid]] of the n processes using the most memory."""
    rows = []
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            text = (d / "status").read_text()
        except OSError:
            continue
        st = parse_meminfo(text)
        name = next((l.split(":", 1)[1].strip() for l in text.splitlines() if l.startswith("Name:")), "?")
        if st.get("VmRSS"):
            rows.append([round(st["VmRSS"] / 1024), name, int(d.name)])
    return sorted(rows, reverse=True)[:n]


def health_sample() -> dict:
    def rd(path):
        try:
            return Path(path).read_text()
        except OSError:
            return ""
    m = parse_meminfo(rd("/proc/meminfo"))
    mb = lambda k: round(m.get(k, 0) / 1024)
    out = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "mem_avail_mb": mb("MemAvailable"), "mem_total_mb": mb("MemTotal"),
           "swap_used_mb": mb("SwapTotal") - mb("SwapFree"), "dirty_mb": mb("Dirty"), "writeback_mb": mb("Writeback")}
    for kind in ("memory", "io", "cpu"):
        out[f"psi_{kind}"] = parse_psi(rd(f"/proc/pressure/{kind}"))
    try:
        out["load1"] = float(rd("/proc/loadavg").split()[0])
    except (IndexError, ValueError):
        pass
    try:
        out["top"] = top_processes()
    except OSError:
        pass
    return out


class HealthMonitor:
    def __init__(self, path: Path, busy=lambda: False, idle_secs: float = 30, busy_secs: float = 5,
                 max_bytes: int = 2_000_000):
        self.path, self.busy, self.idle_secs, self.busy_secs, self.max_bytes = path, busy, idle_secs, busy_secs, max_bytes
        self.stop = threading.Event()

    def write(self, sample: dict):
        """Append one JSON line and fsync it (a hard reset must not lose it); keep one old file."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                os.replace(self.path, self.path.with_name(self.path.name + ".1"))
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(sample, separators=(",", ":")) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            pass

    def run(self):
        while not self.stop.is_set():
            busy = self.busy()
            sample = health_sample()
            sample["busy"] = busy
            self.write(sample)
            self.stop.wait(self.busy_secs if busy else self.idle_secs)

    def start(self):
        threading.Thread(target=self.run, name="health", daemon=True).start()
        return self
