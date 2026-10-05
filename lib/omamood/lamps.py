"""High-level lamp operations shared by the bridge (long-running, one thread per
lamp, persistent connection) and the CLI (one-shot).

`Lamp` wraps one configured device: its profile, its last known DPs, and the
command vocabulary every front end uses:

    power on|off|toggle      brightness N | +N | -N      white [N]
    color #hex [N]           hsv H S [V]                 timer MINUTES
    blink [#hex] [count]     snapshot / restore          refresh

The same JSON command objects travel over the bridge's stdin, the shell's IPC
and the CLI, so an agent can use any of them: {"cmd": "color", "value": "#ff0000"}.
"""

import time

from . import profiles, tuya

SETTLE = 0.06   # seconds between consecutive CONTROL messages


class CommandError(Exception):
    pass


def parse_pct(value, current=None):
    """'50' -> 50, '+10' -> current+10, '-10' -> current-10, clamped 1..100."""
    text = str(value).strip().rstrip("%")
    try:
        if text[:1] in "+-" and current is not None:
            v = current + float(text)
        else:
            v = float(text)
    except ValueError:
        raise CommandError("not a percentage: %r" % value)
    return int(max(1, min(100, round(v))))


NAMED = {
    "red": "#ff0000", "orange": "#ff6400", "yellow": "#ffc800", "green": "#00ff00",
    "cyan": "#00ffff", "blue": "#0000ff", "purple": "#8c00ff", "pink": "#ff0080", "white": "#ffffff",
}


def parse_colour(value):
    """'#rrggbb' | 'rrggbb' | name | {'h','s','v'} -> (h, s, v)."""
    if isinstance(value, dict):
        return float(value["h"]), float(value["s"]), float(value.get("v", 1))
    if isinstance(value, (list, tuple)):
        return tuple(float(x) for x in value)
    text = str(value).strip().lower()
    try:
        return profiles.hex_to_hsv(NAMED.get(text, text))
    except ValueError:
        raise CommandError("not a colour: %r (use #rrggbb or one of %s)" % (value, ", ".join(NAMED)))


class Lamp:
    def __init__(self, record, profile_db):
        self.record = record
        self.id = record["id"]
        self.name = record.get("name") or self.id
        self.profile_db = profile_db
        self.status = {}
        self.light = None
        self.device = None
        self.online = False
        self.error = None
        self.snapshots = {}
        self.last_seen = 0.0
        if record.get("profile") in profile_db:
            self.light = profiles.Light(profile_db[record["profile"]])

    # ---- connection ----

    def connect(self, timeout=3.0):
        if not self.record.get("ip"):
            raise ConnectionError("no IP address yet (run discover)")
        self.device = tuya.Device(self.id, self.record["ip"], self.record["key"],
                                  self.record.get("version", "3.3"), timeout=timeout)
        self.device.connect()
        self.update(self.device.query(), replace=True)
        if self.light is None:
            prof = profiles.pick(self.profile_db, self.status, self.record.get("product"))
            if prof is None:
                raise CommandError("no device profile matches DPs %s (see docs/ADDING_DEVICES.md)"
                                   % ", ".join(sorted(self.status, key=lambda k: int(k) if k.isdigit() else 0)))
            self.light = profiles.Light(prof)
            self.record["profile"] = prof["id"]
        if self.light and self.device.query_cmd == tuya.CONTROL_NEW:
            self.device.query_dps = sorted({s["dp"] for s in self.light.dps.values()})
        self.online = True
        self.error = None

    def close(self):
        if self.device:
            self.device.close()
        self.device = None
        self.online = False

    def update(self, dps, replace=False):
        if replace:
            self.status = dict(dps)
        else:
            self.status.update(dps)
        self.last_seen = time.monotonic()

    def pump(self, timeout):
        """Read one message (push or ack); apply pushed DPs. Returns True if state changed."""
        msg = self.device.recv(timeout)
        if msg is None:
            return False
        self.last_seen = time.monotonic()
        if msg.dps:
            self.update(msg.dps)
            return True
        return False

    def send(self, writes):
        for i, dps in enumerate(writes):
            if i:
                time.sleep(SETTLE)
            self.device.set_dps(dps)
            self.update(dps)    # optimistic; the device's STATUS push confirms

    # ---- state ----

    def state(self):
        out = {"id": self.id, "name": self.name, "online": self.online, "error": self.error,
               "ip": self.record.get("ip"), "profile": self.record.get("profile")}
        if self.light:
            out.update(self.light.decode(self.status))
            out["capabilities"] = self.light.capabilities
        return out

    # ---- commands ----

    def plan(self, cmd):
        """Command dict -> list of DP writes (no I/O). Raises CommandError."""
        if self.light is None:
            raise CommandError("%s has no profile yet" % self.name)
        st = self.light.decode(self.status)
        name = cmd.get("cmd")
        if name == "power":
            v = str(cmd.get("value", "toggle")).lower()
            on = (not st["on"]) if v == "toggle" else v in ("on", "true", "1")
            return self.light.plan_power(on)
        if name == "brightness":
            pct = parse_pct(cmd.get("value"), st.get("brightness"))
            return self.light.plan_brightness(pct, st)
        if name == "white":
            pct = parse_pct(cmd["value"], st.get("brightness")) if cmd.get("value") not in (None, "") else st.get("brightness") or 100
            return self._on_first(st, self.light.plan_white(pct, cmd.get("temperature")))
        if name in ("color", "colour", "hsv"):
            h, s, v = parse_colour(cmd.get("value"))
            if cmd.get("brightness") not in (None, ""):
                v = parse_pct(cmd["brightness"]) / 100.0
            elif cmd.get("keepBrightness", True) and st.get("brightness"):
                v = st["brightness"] / 100.0
            return self._on_first(st, self.light.plan_colour(h, s, v))
        if name == "timer":
            return self.light.plan_timer(float(cmd.get("value", cmd.get("minutes", 0))) * 60)
        raise CommandError("unknown command %r" % name)

    def _on_first(self, st, writes):
        return writes if st["on"] or st["on"] is None else writes + self.light.plan_power(True)

    def blink(self, colour="#ff0000", count=3, period=0.5):
        """Flash `count` times with instant changes, then restore exactly."""
        h, s, v = parse_colour(colour)
        on_msg = self.light.realtime(h, s, max(v, 0.6))
        saved = dict(self.status)
        if on_msg is None:
            # No realtime DP: fall back to ordinary (fading) writes.
            for _ in range(count):
                self.send(self.light.plan_colour(h, s, max(v, 0.6)))
                time.sleep(period)
                self.send(self.light.plan_restore(saved))
                time.sleep(period)
            return
        off_msg = self.light.realtime(0, 0, 0.01)
        was_on = saved.get(self.light.dp("power"))
        if not was_on:
            self.device.set_dps(self.light.plan_power(True)[0])
        for i in range(count):
            self.device.set_dps(on_msg)
            time.sleep(period / 2)
            self.device.set_dps(off_msg)
            time.sleep(period / 2)
        self.send(self.light.plan_restore(saved))
