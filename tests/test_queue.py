"""The install/uninstall queue, and talking to Heroic while it is still starting up."""
import glob
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import Sandbox, Server, synced_cache, cfg, logs, util, library, matching, steam_api, wikidata, igdb_api, heroic_ctl, ops, health, app_state, server  # noqa: F401


class FakeHeroic:
    """Heroic's download queue and uninstall status over (fake) time."""
    def __init__(self):
        self.now = 0.0
        self.queue = []            # appNames downloading, first is active
        self.uninstalling = set()
        self.installed = {"legendary:old"}
        self.started = []          # (time, kind, gid) the queue asked for
        self.running = True
        self.finish_after = {}     # appName -> time it's done

    # OpQueue hooks
    def sleep(self, secs):
        self.now += secs
        for app, t in list(self.finish_after.items()):
            if self.now >= t:
                del self.finish_after[app]
                if app in self.queue:
                    self.queue.remove(app)
                    self.installed.add("legendary:" + app)
                if app in self.uninstalling:
                    self.uninstalling.discard(app)
                    self.installed.discard("legendary:" + app)

    def clock(self):
        return self.now

    def status(self):
        return {"running": self.running, "queue": [{"appName": a, "runner": "legendary"} for a in self.queue],
                "uninstalling": [{"appName": a, "runner": "legendary"} for a in self.uninstalling]}

    def is_installed(self, gid):
        return gid in self.installed

    def run(self, job):
        self.started.append((self.now, job["kind"], job["gid"]))
        app = job["app_name"]
        if job["kind"] == "install":
            self.queue.append(app)
            self.finish_after[app] = self.now + 30       # a 30 s "download"
            return {"result": "queued"}
        self.uninstalling.add(app)
        self.finish_after[app] = self.now + 10
        return {"result": "started"}

    def queue_obj(self, path=None):
        return ops.OpQueue(run=self.run, status=self.status, is_installed=self.is_installed,
                         poll_secs=3, sleep=self.sleep, clock=self.clock, path=path)


def game(app):
    return {"id": "legendary:" + app, "title": app.title(), "runner": "legendary", "app_name": app}


class Queue(unittest.TestCase):
    def setUp(self):
        self.f = FakeHeroic()
        self.q = self.f.queue_obj()

    def drain(self):
        while self.q.step():
            pass

    def test_jobs_run_one_after_another_in_order(self):
        for kind, app in [("install", "a"), ("install", "b"), ("uninstall", "old")]:
            self.assertTrue(self.q.add(kind, game(app), {"path": "/x"})["ok"])
        self.assertEqual([j["position"] for j in self.q.view()], [1, 2, 3])
        self.drain()
        times = [t for t, *_ in self.f.started]
        self.assertEqual([(k, g) for _, k, g in self.f.started],
                         [("install", "legendary:a"), ("install", "legendary:b"), ("uninstall", "legendary:old")])
        self.assertGreaterEqual(times[1] - times[0], 30, "b starts only after a's download finished")
        self.assertGreaterEqual(times[2] - times[1], 30)
        self.assertEqual([j["state"] for j in self.q.view()], ["done", "done", "done"])
        self.assertEqual(self.f.installed, {"legendary:a", "legendary:b"})

    def test_a_failure_does_not_block_the_rest(self):
        real_run = self.f.run
        self.f.run = lambda job: {"error": "heroic-not-started"} if job["app_name"] == "a" else real_run(job)
        self.q = self.f.queue_obj()
        self.q.add("install", game("a"), {})
        self.q.add("install", game("b"), {})
        self.drain()
        v = {j["id"]: j for j in self.q.view()}
        self.assertEqual((v["legendary:a"]["state"], v["legendary:a"]["error"]), ("failed", "heroic-not-started"))
        self.assertEqual(v["legendary:b"]["state"], "done")

    def test_install_that_ends_without_the_game_installed_fails(self):
        self.f.is_installed = lambda gid: False
        self.q = self.f.queue_obj()
        self.q.add("install", game("a"), {})
        self.drain()
        self.assertEqual(self.q.view()[0]["error"], "Heroic finished without installing it")

    def test_heroic_closing_mid_download_fails_the_job(self):
        self.q.add("install", game("a"), {})
        real_sleep = self.f.sleep
        self.f.sleep = lambda s: (real_sleep(s), setattr(self.f, "running", self.f.now < 9))
        self.q = self.f.queue_obj()
        self.q.add("install", game("a"), {})
        self.q.step()
        self.assertEqual(self.q.view()[-1]["error"], "Heroic closed")

    def test_cancel_and_duplicates(self):
        self.q.add("install", game("a"), {})
        self.q.add("install", game("b"), {})
        self.assertEqual(self.q.add("install", game("b"), {}), {"error": "already-queued"})
        self.assertTrue(self.q.cancel("legendary:b"))
        self.assertFalse(self.q.cancel("legendary:nope"))
        self.q.jobs[0]["state"] = "running"
        self.assertFalse(self.q.cancel("legendary:a"), "a running job can't be cancelled")
        self.assertEqual(self.q.pending(), 1)

    def test_uninstall_waits_until_heroic_is_done(self):
        self.q.add("uninstall", game("old"), {})
        self.q.step()
        self.assertGreaterEqual(self.f.now, 10)
        self.assertEqual(self.q.view()[0]["state"], "done")
        self.assertNotIn("legendary:old", self.f.installed)

    def test_uninstall_that_leaves_the_game_installed_fails(self):
        self.f.is_installed = lambda gid: True
        self.q = self.f.queue_obj()
        self.q.add("uninstall", game("old"), {})
        self.q.step()
        self.assertEqual(self.q.view()[0]["error"], "still installed afterwards")

    def test_a_crashing_job_is_logged_and_the_queue_continues(self):
        def run(job):
            if job["app_name"] == "a":
                raise ValueError("boom")
            return FakeHeroic.run(self.f, job)
        self.f.run = run
        self.q = self.f.queue_obj()
        self.q.add("install", game("a"), {})
        self.q.add("install", game("b"), {})
        self.drain()
        self.assertEqual([j["state"] for j in self.q.view()], ["failed", "done"])

    def test_finished_jobs_are_forgotten_after_two_minutes(self):
        self.q.add("uninstall", game("old"), {})
        self.q.step()
        self.assertEqual(len(self.q.view()), 1)
        self.f.now += 121
        self.assertEqual(self.q.view(), [])


class CdpErrors(unittest.TestCase):
    def test_protocol_errors_are_raised(self):
        class FakeWS:
            def __init__(self, *a, **k): pass
            def send(self, t): pass
            def recv(self): return json.dumps({"id": 1, "error": {"code": -32000, "message": "Execution context was destroyed."}})
            def close(self): pass
        with mock.patch.object(heroic_ctl, "_MiniWebSocket", FakeWS):
            with self.assertRaisesRegex(RuntimeError, "context was destroyed"):
                heroic_ctl._cdp_eval("ws://127.0.0.1:1/x", "1")

    def test_interrupted_scripts_are_retried(self):
        calls = []
        def cdp(ws, js, timeout):
            calls.append(1)
            if len(calls) < 2:
                raise RuntimeError("Execution context was destroyed.")
            return 42
        with mock.patch.object(heroic_ctl, "ensure_debug_heroic", lambda d: {"ws": "ws://x"}), \
                mock.patch.object(heroic_ctl, "_cdp_eval", cdp), mock.patch.object(time, "sleep", lambda s: None):
            self.assertEqual(heroic_ctl.heroic_eval(None, "1", 5, "test"), {"value": 42})
            calls.clear()
            with mock.patch.object(heroic_ctl, "_cdp_eval", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope"))):
                self.assertEqual(heroic_ctl.heroic_eval(None, "1", 5, "test"), {"error": "script", "detail": "nope"})


def headless_chrome():
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""), str(Path(__file__).resolve().parent.parent / "data/.test/pw"),
             str(Path.home() / ".cache/ms-playwright")]
    for r in roots:
        hits = glob.glob(f"{r}/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell") if r else []
        if hits:
            return hits[0]
    return None


CHROME = headless_chrome()


@unittest.skipUnless(CHROME, "headless Chromium (Playwright) not installed")
class StartupRace(unittest.TestCase):
    """A stand-in Heroic that, like the real one, starts on a blank page and only then
    navigates to its UI. The first request used to fail in that gap."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / "app.html").write_text("<script>window.api = { ping: async () => 'pong' };</script>")
        # the start page navigates to the UI after 1.5 s (like Heroic's window loading)
        (d / "start.html").write_text("<script>setTimeout(() => location.href = 'app.html', 1500)</script>")
        self.profile = d / "profile"
        self.chrome = subprocess.Popen([CHROME, "--no-sandbox", "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
                                        f"--user-data-dir={self.profile}", (d / "start.html").as_uri()],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def tearDown(self):
        self.chrome.kill()
        self.chrome.wait()
        self.tmp.cleanup()

    def test_first_request_waits_for_the_ui_instead_of_failing(self):
        deadline = time.time() + 10
        while not heroic_ctl._active_debug_port(self.profile) and time.time() < deadline:
            time.sleep(0.1)
        # straight away, while the page is still the start page
        with mock.patch.object(heroic_ctl, "heroic_running", lambda: True):
            r = heroic_ctl.heroic_eval(self.profile, "window.api.ping()", 10, "test")
        self.assertEqual(r, {"value": "pong"})


class SavedQueue(unittest.TestCase):
    """The queue survives quitting the app (or a crash) and carries on next time."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name, "queue.json")
        self.f = FakeHeroic()

    def tearDown(self):
        self.tmp.cleanup()

    def test_waiting_and_running_jobs_come_back_in_order(self):
        q = self.f.queue_obj(self.path)
        q.add("install", game("a"), {"path": "/games"})
        q.add("uninstall", game("old"), {"removePrefix": True})
        q.add("install", game("c"), {})
        q.cancel("legendary:c")
        with q.lock:
            q.jobs[0]["state"] = "running"     # quit mid-download
        q2 = self.f.queue_obj(self.path)
        self.assertEqual(q2.resumed, 2)
        self.assertEqual([(j["kind"], j["gid"], j["state"], j["params"]) for j in q2.jobs],
                         [("install", "legendary:a", "queued", {"path": "/games"}),
                          ("uninstall", "legendary:old", "queued", {"removePrefix": True})])
        while q2.step():
            pass
        self.assertEqual([j["state"] for j in q2.view()], ["done", "done"])
        self.assertEqual(json.loads(self.path.read_text()), [], "finished jobs aren't resumed again")
        self.assertEqual(self.f.queue_obj(self.path).resumed, 0)

    def test_a_job_heroic_already_has_is_followed_not_repeated(self):
        q = self.f.queue_obj(self.path)
        q.add("install", game("a"), {})
        self.f.queue.append("a")                       # Heroic kept downloading while the app was closed
        self.f.finish_after["a"] = 20
        self.f.run = lambda job: {"result": "queued"}  # what the install script answers for a queued game
        q2 = self.f.queue_obj(self.path)
        q2.step()
        self.assertEqual(q2.view()[0]["state"], "done")
        self.assertEqual(self.f.queue, [])

    def test_damaged_files_are_ignored(self):
        for bad in ("not json", '{"a": 1}', '[1, {"kind": "format"}, {"kind": "install"}]'):
            self.path.write_text(bad)
            self.assertEqual(self.f.queue_obj(self.path).jobs, [])

    def test_resumed_jobs_wait_for_the_library(self):
        q = self.f.queue_obj(self.path)
        q.add("install", game("a"), {})
        q2 = self.f.queue_obj(self.path).start()
        time.sleep(0.2)
        self.assertEqual(self.f.started, [], "nothing runs before the library is loaded")
        q2.ready.set()
        for _ in range(50):
            if self.f.started:
                break
            time.sleep(0.05)
        self.assertEqual([s[1:] for s in self.f.started], [("install", "legendary:a")])


class QueueApi(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox(synced_cache()).__enter__()
        self.st = self.sb.state()
        self.st.reload_library()
        self.srv = Server(self.st)       # the queue worker isn't started: jobs stay queued

    def tearDown(self):
        self.srv.close()
        self.sb.__exit__(None, None, None)

    def test_queue_through_the_api(self):
        code, r = self.srv.post("/api/install-start", {"id": "legendary:beta", "options": {"path": "/games"}})
        self.assertEqual((r["ok"], r["result"], r["position"]), (True, "queued", 1))
        r = self.srv.post("/api/uninstall-start", {"id": "legendary:alpha", "removePrefix": True})[1]
        self.assertEqual(r["position"], 2)
        self.assertEqual(self.srv.post("/api/install-start", {"id": "legendary:beta", "options": {"path": "/games"}})[1]["error"],
                         "already-queued")
        self.assertEqual(self.srv.post("/api/install-start", {"id": "legendary:gamma", "options": {"path": "relative"}})[1]["error"], "bad-path")
        with mock.patch.object(heroic_ctl, "heroic_downloads", lambda d: {"running": False}):
            jobs = self.srv.post("/api/downloads")[1]["jobs"]
        self.assertEqual([(j["id"], j["kind"], j["position"]) for j in jobs],
                         [("legendary:beta", "install", 1), ("legendary:alpha", "uninstall", 2)])
        self.assertEqual(self.st.ops.jobs[1]["params"], {"removePrefix": True, "removeSettings": False})
        self.assertEqual(self.srv.post("/api/launch", {"id": "legendary:alpha"})[1]["error"], "heroic-busy",
                         "no launching while the queue would be cut off")
        self.assertTrue(self.srv.post("/api/queue-cancel", {"id": "legendary:alpha"})[1]["ok"])
        self.assertEqual(self.st.ops.pending(), 1)
        # quitting now and starting again: the install is still queued, and the page is told
        st2 = self.sb.state()
        self.assertEqual([j["gid"] for j in st2.ops.jobs], ["legendary:beta"])
        srv2 = Server(st2)
        try:
            with mock.patch.object(heroic_ctl, "heroic_downloads", lambda d: {"running": False}):
                self.assertEqual(srv2.post("/api/downloads")[1]["resumed"], 1)
        finally:
            srv2.close()


if __name__ == "__main__":
    unittest.main()
