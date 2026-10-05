"""`omamood bridge`: the long-running helper Service.qml starts. Contract: BRIDGE.md.

stdin   one JSON command per line, e.g. {"cmd": "power", "lamp": "all", "value": "toggle", "id": 7}
stdout  one JSON object per line:
          {"type": "state", "lamps": [...], "setup": {...}, "settings": {...}}   on every change
          {"type": "result", "id": 7, "ok": true} | {"type": "result", "id": 7, "ok": false, "error": "..."}
stderr  human-readable log lines

Each lamp gets a worker thread holding one TCP connection: it reads the
device's STATUS pushes (so the lamp's own buttons show up live), sends
heartbeats, reconnects with backoff, and executes that lamp's commands in
order. Exit codes: 0 stdin closed, 4 setup failure (e.g. Python too old).
"""

import json
import os
import queue
import select
import signal
import subprocess
import sys
import threading
import time
import traceback

from . import cloud, discovery, lamps, profiles, store, wallpaper

HEARTBEAT = 9.0
STALE = 25.0
BACKOFF_MAX = 60.0


class Bridge:
    def __init__(self, out=sys.stdout):
        self.out = out
        self.out_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.dirty = threading.Event()
        self.profile_db = profiles.load_all()
        self.workers = {}
        self.setup = {"stage": "idle"}
        # "Find lamps": idle -> searching -> done (found/moved/missing), back to idle after a while.
        self.discovery = {"stage": "idle"}
        self.discovery_lock = threading.Lock()
        self.setup_cancel = threading.Event()
        self.settings = {"saturationFloor": 0.7, "sleepAction": "off"}
        self.sleep_snapshot = None

    # ---- output ----

    def emit(self, obj):
        line = json.dumps(obj, separators=(",", ":"))
        with self.out_lock:
            self.out.write(line + "\n")
            self.out.flush()

    def log(self, *parts):
        print("omamood:", *parts, file=sys.stderr, flush=True)

    def mark(self):
        self.dirty.set()

    def state(self):
        return {"type": "state",
                "lamps": [w.lamp.state() for w in list(self.workers.values())],
                "setup": dict(self.setup),
                "discovery": dict(self.discovery),
                "configured": len(self.workers)}

    def publisher(self):
        # Coalesce bursts (a CONTROL echoes several pushes) into one line.
        while True:
            self.dirty.wait()
            time.sleep(0.03)
            self.dirty.clear()
            self.emit(self.state())

    # ---- lamps ----

    def load(self):
        records = store.load_devices()
        keep = {r["id"] for r in records}
        for lid in list(self.workers):
            if lid not in keep:
                self.workers.pop(lid).stop()
        for r in records:
            w = self.workers.get(r["id"])
            if w is None:
                w = Worker(self, lamps.Lamp(r, self.profile_db))
                self.workers[r["id"]] = w
                w.start()
            else:
                w.lamp.record.update(r)
        self.mark()

    def targets(self, cmd):
        sel = cmd.get("lamp", "all")
        if sel in (None, "", "all"):
            return list(self.workers.values())
        found = [w for w in self.workers.values() if sel in (w.lamp.id, w.lamp.name)
                 or w.lamp.name.lower() == str(sel).lower()]
        if not found:
            raise lamps.CommandError("no lamp %r (have: %s)" % (sel, ", ".join(w.lamp.name for w in self.workers.values()) or "none"))
        return found

    # ---- commands ----

    def handle(self, cmd):
        name = cmd.get("cmd")
        if name in ("power", "brightness", "white", "color", "colour", "hsv", "timer", "blink", "refresh"):
            ws = self.targets(cmd)
            if name == "blink":
                ws = [w for w in ws if w.lamp.online and (cmd.get("whenOff") or w.lamp.state().get("on"))]
            for w in ws:
                w.submit(cmd)
            return {"lamps": [w.lamp.name for w in ws]}
        if name in ("wallpaper", "accent", "themeColor"):
            return self.theme_colour(cmd)
        if name == "settings":
            self.settings.update({k: v for k, v in cmd.items() if k not in ("cmd", "id")})
            return {"settings": self.settings}
        if name == "reload":
            self.load()
            return {}
        if name == "state":
            self.emit(self.state())
            return {}
        if name == "setup.qr":
            return self.start_setup(cmd)
        if name == "setup.cancel":
            self.setup_cancel.set()
            if self.setup.get("stage") in ("error", "done", "idle"):
                self.set_setup(stage="idle")
            return {}
        if name == "remove":
            sel = cmd.get("lamp")
            if sel in (None, "", "all"):
                raise lamps.CommandError("remove needs one lamp (id or name)")
            removed = store.remove_device(sel)
            if removed is None:
                raise lamps.CommandError("no lamp %r" % sel)
            self.load()
            return {"removed": removed.get("name") or removed["id"]}
        if name == "discover":
            if self.discovery.get("stage") == "searching":
                return {"already": True}
            threading.Thread(target=self.rediscover, daemon=True).start()
            return {}
        raise lamps.CommandError("unknown command %r" % name)

    def theme_colour(self, cmd):
        name = cmd["cmd"]
        if cmd.get("value"):
            colour = cmd["value"]
        elif name == "wallpaper":
            path = cmd.get("path") or wallpaper.current_wallpaper()
            if not path:
                raise lamps.CommandError("no current wallpaper")
            colour = wallpaper.image_colour(path)
        else:
            colour = wallpaper.theme_colour("accent")
        h, s = wallpaper.lamp_hsv(colour, float(self.settings.get("saturationFloor", 0.7)))
        sent = []
        for w in self.targets(cmd):
            st = w.lamp.state()
            if not w.lamp.online or (not st.get("on") and not cmd.get("force")):
                continue
            w.submit({"cmd": "hsv", "value": [h, s, 1.0]})
            sent.append(w.lamp.name)
        return {"source": colour, "lamps": sent}

    def sleep(self, going):
        if self.settings.get("sleepAction", "off") != "off":
            return
        if going:
            self.sleep_snapshot = {lid: dict(w.lamp.status) for lid, w in self.workers.items() if w.lamp.online}
            for w in self.workers.values():
                if w.lamp.online:
                    w.submit({"cmd": "power", "value": "off"})
            time.sleep(0.5)     # give the writes a moment before the machine suspends
        elif self.sleep_snapshot:
            snap, self.sleep_snapshot = self.sleep_snapshot, None
            for lid, status in snap.items():
                if lid in self.workers:
                    self.workers[lid].submit({"cmd": "restore", "status": status, "wait": 20})

    def watch_sleep(self):
        """logind PrepareForSleep via gdbus (glib2 is always present)."""
        try:
            p = subprocess.Popen(["gdbus", "monitor", "--system", "--dest", "org.freedesktop.login1",
                                  "--object-path", "/org/freedesktop/login1"],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except OSError:
            return
        for line in p.stdout:
            if "PrepareForSleep" in line:
                self.sleep("true" in line.split("PrepareForSleep", 1)[1])

    # ---- setup ----

    def set_setup(self, **kw):
        self.setup = kw
        self.mark()

    def start_setup(self, cmd):
        code = str(cmd.get("userCode", "")).strip()
        if not code:
            raise lamps.CommandError("userCode is required (Smart Life > Me > Settings > Account and Security > User Code)")
        self.setup_cancel.clear()
        threading.Thread(target=self.run_setup, args=(code,), daemon=True).start()
        return {}

    def run_setup(self, code):
        try:
            self.set_setup(stage="requesting")
            token = cloud.request_qr(code)
            self.set_setup(stage="scan", qr=qr_matrix(cloud.QR_PREFIX + token))
            login, deadline = None, time.monotonic() + 180
            while login is None and time.monotonic() < deadline and not self.setup_cancel.is_set():
                time.sleep(2)
                login = cloud.poll_login(token, code)
            if login is None:
                self.set_setup(stage="idle" if self.setup_cancel.is_set() else "error",
                               error="" if self.setup_cancel.is_set() else "The QR code expired. Try again.")
                return
            self.set_setup(stage="fetching")
            session = cloud.Session(login)
            try:
                found = cloud.to_device_records(session.devices())
            finally:
                session.logout()
            added, skipped = setup_devices(found, self.profile_db, log=self.log,
                                           progress=lambda s: self.set_setup(stage=s))
            self.load()
            self.set_setup(stage="done", added=[d["name"] for d in added], skipped=skipped)
        except Exception as e:  # report every failure to the panel
            self.log("setup failed:", traceback.format_exc())
            self.set_setup(stage="error", error=str(e))

    def rediscover(self):
        if not self.discovery_lock.acquire(blocking=False):
            return
        try:
            self.discovery = {"stage": "searching", "started": time.time()}
            self.mark()
            records = store.load_devices()
            before = {r["id"]: r.get("ip") for r in records}
            reached = discovery.locate(records, log=self.log)
            store.save_devices(records)
            self.load()
            found = [{"name": r.get("name") or r["id"], "ip": r.get("ip"),
                      "moved": before.get(r["id"]) != r.get("ip")}
                     for r in records if r["id"] in reached]
            missing = [r.get("name") or r["id"] for r in records if r["id"] not in reached]
            self.discovery = {"stage": "done", "found": found, "missing": missing,
                              "seconds": round(time.time() - self.discovery["started"], 1)}
        except Exception as e:
            self.log("discover failed:", e)
            self.discovery = {"stage": "done", "found": [], "missing": [], "error": str(e)}
        finally:
            self.discovery_lock.release()
            self.mark()
        # The result stays up long enough to read, then the line goes away.
        done = self.discovery
        def clear():
            if self.discovery is done:
                self.discovery = {"stage": "idle"}
                self.mark()
        threading.Timer(15, clear).start()

    # ---- main ----

    def run(self):
        threading.Thread(target=self.publisher, daemon=True).start()
        threading.Thread(target=self.watch_sleep, daemon=True).start()
        self.load()
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            cmd = None
            try:
                cmd = json.loads(line)
                result = self.handle(cmd) or {}
                if cmd.get("id") is not None:
                    self.emit(dict(type="result", id=cmd["id"], ok=True, **result))
            except Exception as e:
                self.log("command failed:", line, "->", e)
                rid = cmd.get("id") if isinstance(cmd, dict) else None
                self.emit({"type": "result", "id": rid, "ok": False, "error": str(e)})
        return 0


class Worker(threading.Thread):
    def __init__(self, bridge, lamp):
        super().__init__(daemon=True)
        self.bridge = bridge
        self.lamp = lamp
        self.q = queue.Queue()
        self.stopping = False
        # A self-pipe wakes the worker the moment a command arrives, while it
        # sleeps in select() on the lamp's socket: no polling, no added latency.
        self.wake_r, self.wake_w = os.pipe()
        os.set_blocking(self.wake_r, False)

    def submit(self, cmd):
        self.q.put(cmd)
        os.write(self.wake_w, b"x")

    def stop(self):
        self.stopping = True
        self.submit(None)

    def run(self):
        backoff = 1.0
        while not self.stopping:
            try:
                self.lamp.connect()
                backoff = 1.0
                self.bridge.mark()
                self.serve()
            except Exception as e:
                self.lamp.error = str(e)
                self.bridge.log("%s: %s" % (self.lamp.name, e))
            self.lamp.close()
            self.bridge.mark()
            if self.stopping:
                break
            # Wait out the backoff, but wake for queued commands (they retry the connection).
            try:
                cmd = self.q.get(timeout=backoff)
                if cmd is not None:
                    self.q.put(cmd)
            except queue.Empty:
                pass
            backoff = min(BACKOFF_MAX, backoff * 2)

    def serve(self):
        last_beat = time.monotonic()
        while not self.stopping:
            try:
                cmd = self.q.get_nowait()
            except queue.Empty:
                cmd = None
            if cmd is not None:
                self.execute(cmd)
                continue
            dev = self.lamp.device
            if dev.buf:
                ready = [dev.fileno()]
            else:
                ready, _, _ = select.select([dev.fileno(), self.wake_r], [], [], 1.0)
            if self.wake_r in ready:
                try:
                    os.read(self.wake_r, 4096)
                except BlockingIOError:
                    pass
            if dev.fileno() in ready and self.lamp.pump(0.05):
                self.bridge.mark()
            now = time.monotonic()
            if now - last_beat > HEARTBEAT:
                self.lamp.device.heartbeat()
                last_beat = now
            if now - self.lamp.last_seen > STALE:
                raise ConnectionError("no reply for %d s" % STALE)

    def execute(self, cmd):
        name = cmd.get("cmd")
        try:
            if name == "blink":
                self.lamp.blink(cmd.get("color") or cmd.get("value") or "#ff0000",
                                int(cmd.get("count", 3)), float(cmd.get("period", 0.5)))
            elif name == "restore":
                self.lamp.send(self.lamp.light.plan_restore(cmd["status"]))
            elif name == "refresh":
                self.lamp.update(self.lamp.device.query(), replace=True)
            else:
                self.lamp.send(self.lamp.plan(cmd))
            self.lamp.error = None
        except (OSError, ConnectionError):
            # The connection died mid-command: run it again once reconnected.
            if not cmd.get("_retried"):
                self.q.put(dict(cmd, _retried=True))
            raise
        except lamps.CommandError as e:
            self.lamp.error = str(e)
            self.bridge.log("%s: %s" % (self.lamp.name, e))
        self.bridge.mark()


def qr_matrix(text):
    """QR code as rows of '0'/'1' (with quiet zone), via qrencode."""
    out = subprocess.run(["qrencode", "-t", "ASCII", "-m", "2", text], capture_output=True, text=True, timeout=10)
    if out.returncode != 0:
        raise RuntimeError("qrencode failed: %s" % out.stderr.strip())
    # Two characters per module: "##" dark, "  " light; every line equally wide.
    return ["".join("1" if line[i] == "#" else "0" for i in range(0, len(line), 2))
            for line in out.stdout.splitlines() if line]


def setup_devices(found, profile_db, log=lambda *_: None, progress=lambda _s: None):
    """Cloud records -> located, profiled, saved lamps. Returns (added, skipped)."""
    candidates = [d for d in found if d.pop("light", True)]
    skipped = [d["name"] for d in found if d not in candidates]
    progress("locating")
    statuses = discovery.locate(candidates, log=log)
    added = []
    for d in candidates:
        status = statuses.get(d["id"])
        prof = profiles.pick(profile_db, status or {}, d.get("product"))
        if prof is None:
            skipped.append("%s (no profile for DPs %s)" % (d["name"], ",".join(sorted(status or {}))))
            continue
        d["profile"] = prof["id"]
        d.setdefault("version", "3.3")
        if not status:
            log("%s: not found on the LAN yet; saved without an IP" % d["name"])
        added.append(d)
    if added:
        store.upsert_devices(added)
    return added, skipped


def main():
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)   # PR_SET_PDEATHSIG: die with the shell
    except OSError:
        pass
    if os.getppid() == 1:
        return 0
    return Bridge().run()
