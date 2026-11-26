"""The install / uninstall queue (one job at a time)."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from . import util
from .logs import log, log_error


# ------------------------------------------------------------------- install / uninstall queue
#
# Installs and uninstalls queue up here and run one at a time: each starts only when the
# previous one has finished (an install when its download has left Heroic's queue, an
# uninstall when Heroic stops reporting it). A failed job doesn't hold up the rest.
# The waiting and running jobs are saved to queue.json, so quitting (or a crash) doesn't lose
# them: they run again next time. That's safe because the jobs are idempotent - installing a
# game Heroic is already downloading just follows that download, and uninstalling a game
# that's gone counts as done.

class OpQueue:
    def __init__(self, run, status, is_installed, poll_secs: float = 3, sleep=time.sleep, clock=time.time,
                 path: Path | None = None):
        """run(job) -> {"result"} | {"error"}; status() -> heroic_downloads(); is_installed(gid) -> bool.
        path: where the queue is saved (None: not saved)."""
        self.run, self.status, self.is_installed = run, status, is_installed
        self.poll_secs, self.sleep, self.clock = poll_secs, sleep, clock
        self.path = path
        self.jobs: list[dict] = []          # queued + running, in order
        self.finished: list[dict] = []      # most recent last (kept briefly for the UI)
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.ready = threading.Event()      # set once the library is loaded (resumed jobs need it)
        self.seq = 0
        self.resumed = self._load()

    # -- saving
    def _load(self) -> int:
        saved = util.read_json(self.path, []) if self.path else []
        keys = ("kind", "gid", "title", "runner", "app_name", "params")
        for j in saved if isinstance(saved, list) else []:
            if not (isinstance(j, dict) and all(k in j for k in keys) and j["kind"] in ("install", "uninstall")
                    and isinstance(j["params"], dict)):
                continue
            if any(x["gid"] == j["gid"] for x in self.jobs):
                continue
            self.seq += 1
            self.jobs.append({**{k: j[k] for k in keys}, "n": self.seq, "state": "queued", "added": j.get("added", 0)})
        if self.jobs:
            log(f"resuming {len(self.jobs)} queued job(s):", ", ".join(f"{j['kind']} {j['title']}" for j in self.jobs))
        return len(self.jobs)

    def _save(self):
        """Call with the lock held."""
        if not self.path:
            return
        try:
            util.write_json_atomic(self.path, [{k: j[k] for k in ("kind", "gid", "title", "runner", "app_name",
                                                                  "params", "added")} for j in self.jobs])
        except OSError as e:
            log_error("couldn't save the queue:", exc=e)

    # -- used by the HTTP handler
    def add(self, kind: str, game: dict, params: dict) -> dict:
        with self.lock:
            if any(j["gid"] == game["id"] for j in self.jobs):
                return {"error": "already-queued"}
            self.seq += 1
            job = {"n": self.seq, "kind": kind, "gid": game["id"], "title": game["title"], "runner": game["runner"],
                   "app_name": game["app_name"], "params": params, "state": "queued", "added": self.clock()}
            self.jobs.append(job)
            self._save()
            pos = len(self.jobs)
        log("queued", kind, game["title"], f"(position {pos})")
        self.wake.set()
        return {"ok": True, "position": pos}

    def cancel(self, gid: str) -> bool:
        with self.lock:
            for j in self.jobs:
                if j["gid"] == gid and j["state"] == "queued":
                    self.jobs.remove(j)
                    self._save()
                    log("cancelled queued", j["kind"], j["title"])
                    return True
        return False

    def pending(self) -> int:
        with self.lock:
            return len(self.jobs)

    def view(self) -> list[dict]:
        """For the UI: queued/running jobs with their position, plus recent results."""
        with self.lock:
            now = self.clock()
            self.finished = [j for j in self.finished if now - j["ended"] < 120]
            return ([{"id": j["gid"], "kind": j["kind"], "state": j["state"], "position": i + 1}
                     for i, j in enumerate(self.jobs)] +
                    [{"id": j["gid"], "kind": j["kind"], "state": j["state"], "error": j.get("error"), "n": j["n"]}
                     for j in self.finished])

    # -- the worker
    def step(self) -> bool:
        """Run the next job to completion. Returns False if there was nothing to do."""
        with self.lock:
            job = self.jobs[0] if self.jobs else None
            if job:
                job["state"] = "running"
        if not job:
            return False
        log("starting", job["kind"], job["title"])
        try:
            error = self._run(job)
        except Exception as e:   # one bad job mustn't stop the queue
            log_error("queue job crashed:", job["kind"], job["title"], exc=e)
            error = "crashed"
        with self.lock:
            self.jobs.remove(job)
            self._save()
            job.update(state="failed" if error else "done", error=error, ended=self.clock())
            self.finished.append(job)
        log(job["kind"], job["title"], "->", f"failed ({error})" if error else "done")
        return True

    def _wait(self, still_running, max_secs: float) -> bool:
        """Poll until still_running() is False. Returns False on timeout."""
        start = self.clock()
        while still_running():
            if self.clock() - start > max_secs:
                return False
            self.sleep(self.poll_secs)
        return True

    def _run(self, job) -> str | None:
        app, gid = job["app_name"], job["gid"]
        self.status()                                   # make sure Heroic's progress listener is in place
        r = self.run(job)
        if "error" in r:
            return r.get("detail") or r["error"]
        res = r.get("result")
        if job["kind"] == "install":
            if res == "installed":
                return None
            if res != "queued":
                return f"Heroic answered {res!r}"
            seen = {"in_queue": False}
            started = self.clock()

            def downloading():
                d = self.status()
                if not d.get("running"):
                    raise RuntimeError("Heroic closed")
                inq = any(q.get("appName") == app for q in d.get("queue") or [])
                seen["in_queue"] |= inq
                # not in Heroic's queue: finished, or (if never seen) it never got there
                return inq or (not seen["in_queue"] and self.clock() - started < 30)
            try:
                if not self._wait(downloading, max_secs=24 * 3600):
                    return "timed out"
            except RuntimeError as e:
                return str(e)
            return None if self.is_installed(gid) else "Heroic finished without installing it"
        # uninstall
        if res not in ("started", "done"):
            return f"Heroic answered {res!r}"

        def uninstalling():
            d = self.status()
            return d.get("running") and any(u.get("appName") == app for u in d.get("uninstalling") or [])
        if res == "started" and not self._wait(uninstalling, max_secs=3600):
            return "timed out"
        for _ in range(10):                            # Heroic's installed list can lag a moment
            if not self.is_installed(gid):
                return None
            self.sleep(1)
        return "still installed afterwards"

    def worker(self):
        self.ready.wait()
        while True:
            if not self.step():
                self.wake.wait()
                self.wake.clear()

    def start(self):
        threading.Thread(target=self.worker, name="queue", daemon=True).start()
        return self
