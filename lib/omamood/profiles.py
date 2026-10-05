"""Device profiles: which data point (DP) means what, and how values are encoded.

Profiles are JSON files in devices/ (schema: docs/ADDING_DEVICES.md). This
module loads them, picks one for a device, and turns between the device's DPs
and OmaMood's state:

    {"on": bool, "mode": "white"|"colour"|<other>, "brightness": 0-100,
     "hsv": [h 0-360, s 0-1, v 0-1] | None, "color": "#rrggbb" | None,
     "temperature": 0-100 | None, "timer": seconds | None}

Every "plan_*" function returns a list of writes: each item is one {dp: value}
dict, sent as one CONTROL message, in order. Nothing here touches the network,
so it is all unit-tested against pins in tests/pins/.
"""

import colorsys
import copy
import glob
import json
import os


def _read_json(path):
    with open(path) as f:
        return json.load(f)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEVICES_DIR = os.path.join(ROOT, "devices")

CAPABILITIES = ("power", "mode", "brightness", "temperature", "colour", "timer", "realtime")


class ProfileError(Exception):
    pass


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_all(directory=DEVICES_DIR):
    """Return {id: resolved profile}. Raises ProfileError on a malformed file."""
    raw = {}
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        try:
            data = _read_json(path)
        except ValueError as e:
            raise ProfileError("%s: not JSON: %s" % (path, e))
        pid = data.get("id")
        if pid != os.path.splitext(os.path.basename(path))[0]:
            raise ProfileError("%s: id %r must match the file name" % (path, pid))
        data["_file"] = os.path.relpath(path, ROOT)
        raw[pid] = data

    resolved = {}

    def resolve(pid, chain=()):
        if pid in resolved:
            return resolved[pid]
        if pid in chain:
            raise ProfileError("extends cycle: %s" % " -> ".join(chain + (pid,)))
        if pid not in raw:
            raise ProfileError("unknown profile %r" % pid)
        data = raw[pid]
        parent = data.get("extends")
        prof = _merge(resolve(parent, chain + (pid,)), data) if parent else copy.deepcopy(data)
        # A child's "match" and "tested" are its own, never inherited.
        prof["match"] = data.get("match", {})
        prof["tested"] = data.get("tested", [])
        prof["dps"] = {k: v for k, v in prof.get("dps", {}).items() if v is not None}
        validate(prof)
        resolved[pid] = prof
        return prof

    for pid in raw:
        resolve(pid)
    return resolved


def validate(prof):
    where = prof.get("_file", prof.get("id"))
    for key in ("id", "name", "dps", "match"):
        if key not in prof:
            raise ProfileError("%s: missing %r" % (where, key))
    for cap, spec in prof["dps"].items():
        if cap not in CAPABILITIES:
            raise ProfileError("%s: unknown capability %r (known: %s)" % (where, cap, ", ".join(CAPABILITIES)))
        if not isinstance(spec, dict) or "dp" not in spec:
            raise ProfileError("%s: dps.%s needs a 'dp'" % (where, cap))
    if "power" not in prof["dps"]:
        raise ProfileError("%s: every light needs dps.power" % where)
    fmt = prof["dps"].get("colour", {}).get("format")
    if fmt and fmt not in ("hsv16", "rgbhsv"):
        raise ProfileError("%s: unknown colour format %r" % (where, fmt))
    m = prof["match"]
    if not (m.get("dps") or m.get("product")):
        raise ProfileError("%s: match needs 'dps' and/or 'product'" % where)


def pick(profiles, status=None, product=None):
    """Choose the best profile for a device. `status` is its DP dict (from a
    LAN query), `product` a list of names/ids from the cloud. Product matches
    win over DP-signature matches; then higher priority; then more DPs."""
    status = status or {}
    product = [p for p in (product or []) if p]
    best, best_key = None, None
    for prof in profiles.values():
        m = prof["match"]
        by_product = bool(set(m.get("product", [])) & set(product))
        need = m.get("dps") or []
        by_dps = bool(need) and all(str(d) in status for d in need)
        if not (by_product or by_dps):
            continue
        key = (by_product, prof.get("priority", 0), len(need))
        if best_key is None or key > best_key:
            best, best_key = prof, key
    return best


# ---------------------------------------------------------------- encoding

def _scale(v, lo, hi):
    """0-100 percent -> device range."""
    return int(round(lo + (hi - lo) * max(0.0, min(100.0, v)) / 100.0))


def _unscale(raw, lo, hi):
    return 0 if hi == lo else round(100.0 * (raw - lo) / (hi - lo))


def encode_colour(fmt, h, s, v):
    h = int(round(h)) % 360
    if fmt == "hsv16":
        return "%04x%04x%04x" % (h, int(round(s * 1000)), int(round(v * 1000)))
    if fmt == "rgbhsv":
        r, g, b = (int(round(x * 255)) for x in colorsys.hsv_to_rgb(h / 360.0, s, v))
        return "%02x%02x%02x%04x%02x%02x" % (r, g, b, h, int(round(s * 255)), int(round(v * 255)))
    raise ProfileError("unknown colour format %r" % fmt)


def decode_colour(fmt, raw):
    """Return (h 0-360, s 0-1, v 0-1) or None."""
    try:
        if fmt == "hsv16" and len(raw) == 12:
            return int(raw[0:4], 16), int(raw[4:8], 16) / 1000.0, int(raw[8:12], 16) / 1000.0
        if fmt == "rgbhsv" and len(raw) == 14:
            return int(raw[6:10], 16), int(raw[10:12], 16) / 255.0, int(raw[12:14], 16) / 255.0
    except (TypeError, ValueError):
        pass
    return None


def hsv_to_hex(h, s, v):
    r, g, b = colorsys.hsv_to_rgb((h % 360) / 360.0, s, v)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def hex_to_hsv(text):
    t = text.strip().lstrip("#")
    if len(t) == 3:
        t = "".join(c * 2 for c in t)
    if len(t) != 6:
        raise ValueError("expected #rrggbb, got %r" % text)
    r, g, b = (int(t[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return h * 360.0, s, v


class Light:
    """A profile bound to nothing but itself: decode DPs, plan writes."""

    def __init__(self, profile):
        self.profile = profile
        self.dps = profile["dps"]
        self.write = profile.get("write", {})

    @property
    def capabilities(self):
        return sorted(self.dps)

    def dp(self, cap):
        spec = self.dps.get(cap)
        return spec["dp"] if spec else None

    # ---- read ----

    def decode(self, status):
        d = self.dps
        st = {"on": None, "mode": None, "brightness": None, "hsv": None, "color": None,
              "temperature": None, "timer": None}
        if d["power"]["dp"] in status:
            st["on"] = bool(status[d["power"]["dp"]])
        if "mode" in d and d["mode"]["dp"] in status:
            raw = status[d["mode"]["dp"]]
            names = {v: k for k, v in d["mode"].get("values", {}).items()}
            st["mode"] = names.get(raw, raw)
        if "colour" in d and d["colour"]["dp"] in status:
            hsv = decode_colour(d["colour"]["format"], status[d["colour"]["dp"]])
            if hsv:
                st["hsv"] = [hsv[0], round(hsv[1], 3), round(hsv[2], 3)]
                st["color"] = hsv_to_hex(*hsv)
        if "temperature" in d and d["temperature"]["dp"] in status:
            spec = d["temperature"]
            st["temperature"] = _unscale(status[spec["dp"]], spec["min"], spec["max"])
        if "timer" in d and d["timer"]["dp"] in status:
            st["timer"] = int(status[d["timer"]["dp"]] or 0)
        # Brightness is the white brightness DP in white mode, the colour's
        # value in colour mode: that is what the lamp actually shows.
        if st["mode"] == "colour" and st["hsv"]:
            st["brightness"] = round(st["hsv"][2] * 100)
        elif "brightness" in d and d["brightness"]["dp"] in status:
            spec = d["brightness"]
            st["brightness"] = max(0, _unscale(status[spec["dp"]], spec["min"], spec["max"]))
        return st

    # ---- write plans ----

    def _order(self, writes, mode_value=None):
        """writes: list of (dp, value). Returns list of {dp: value} messages."""
        if mode_value is not None and "mode" in self.dps:
            mode_dp = self.dps["mode"]["dp"]
            if self.write.get("modeLast", True):
                writes = writes + [(mode_dp, mode_value)]
            else:
                writes = [(mode_dp, mode_value)] + writes
        if self.write.get("onePerMessage", True):
            return [{dp: v} for dp, v in writes]
        merged = {}
        for dp, v in writes:
            merged[dp] = v
        return [merged] if merged else []

    def _mode(self, name):
        return self.dps["mode"].get("values", {}).get(name, name) if "mode" in self.dps else None

    def plan_power(self, on):
        return [{self.dps["power"]["dp"]: bool(on)}]

    def plan_white(self, brightness=None, temperature=None):
        writes = []
        if brightness is not None and "brightness" in self.dps:
            s = self.dps["brightness"]
            writes.append((s["dp"], _scale(max(1, brightness), s["min"], s["max"])))
        if temperature is not None and "temperature" in self.dps:
            s = self.dps["temperature"]
            writes.append((s["dp"], _scale(temperature, s["min"], s["max"])))
        return self._order(writes, self._mode("white"))

    def plan_colour(self, h, s, v):
        if "colour" not in self.dps:
            raise ProfileError("%s has no colour" % self.profile["id"])
        spec = self.dps["colour"]
        value = encode_colour(spec["format"], h, s, max(0.01, min(1.0, v)))
        return self._order([(spec["dp"], value)], self._mode("colour"))

    def plan_brightness(self, pct, state):
        """Change brightness keeping the current mode and colour."""
        if state.get("mode") == "colour" and state.get("hsv") and "colour" in self.dps:
            h, s, _ = state["hsv"]
            spec = self.dps["colour"]
            return [{spec["dp"]: encode_colour(spec["format"], h, s, max(0.01, pct / 100.0))}]
        if "brightness" not in self.dps:
            return []
        spec = self.dps["brightness"]
        return [{spec["dp"]: _scale(max(1, pct), spec["min"], spec["max"])}]

    def plan_timer(self, seconds):
        if "timer" not in self.dps:
            raise ProfileError("%s has no timer" % self.profile["id"])
        return [{self.dps["timer"]["dp"]: max(0, min(int(seconds), self.dps["timer"].get("max", 86400)))}]

    def realtime(self, h, s, v):
        """One instant (non-fading) colour change, or None if unsupported.
        Does not change the lamp's stored state: restore() afterwards."""
        spec = self.dps.get("realtime")
        if not spec or spec.get("format") != "control_v2":
            return None
        return {spec["dp"]: "0" + encode_colour("hsv16", h, s, v) + "00000000"}

    def plan_restore(self, status):
        """Writes that bring the lamp back to a previously read status dict."""
        writes = []
        for cap in ("colour", "brightness", "temperature"):
            spec = self.dps.get(cap)
            if spec and spec["dp"] in status:
                writes.append((spec["dp"], status[spec["dp"]]))
        mode = None
        if "mode" in self.dps and self.dps["mode"]["dp"] in status:
            mode = status[self.dps["mode"]["dp"]]
        out = self._order(writes, mode)
        pdp = self.dps["power"]["dp"]
        if pdp in status:
            out.append({pdp: bool(status[pdp])})
        return out
