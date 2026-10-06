"""The `omamood` command. Human output by default, `--json` for scripts and agents.

Lamp commands go through the running Omarchy shell when the OmaMood service
is loaded (so the bar stays in sync), otherwise straight to the lamps.

Exit codes: 0 ok, 1 command failed, 2 usage, 3 no lamps configured,
4 environment problem (missing tool, unsupported protocol).
"""

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import time

from . import cloud, discovery, lamps, profiles, store, tuya, wallpaper

LAMP_CMDS = ("on", "off", "toggle", "brightness", "white", "color", "timer", "blink", "wallpaper", "accent")


def _out(args, data, human):
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(human)


def _err(args, message, code=1):
    if args.json:
        print(json.dumps({"ok": False, "error": message}))
    else:
        print("omamood: %s" % message, file=sys.stderr)
    return code


# ---------------------------------------------------------------- shell IPC

def shell_call(method, *argv, timeout=3):
    """Call the plugin's IPC target; None if the shell or service is not there."""
    if os.environ.get("OMAMOOD_NO_SHELL") or not shutil.which("omarchy-shell"):
        return None
    try:
        p = subprocess.run(["omarchy-shell", "omamood", method, *argv],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def shell_running():
    return shell_call("ping") == "pong"


# ---------------------------------------------------------------- commands

def to_command(args):
    a = args.action
    cmd = {"lamp": args.lamp or "all"}
    if a in ("on", "off", "toggle"):
        cmd.update(cmd="power", value=a)
    elif a == "brightness":
        cmd.update(cmd="brightness", value=args.value)
    elif a == "white":
        cmd.update(cmd="white", value=args.value)
    elif a == "color":
        cmd.update(cmd="color", value=args.value, brightness=args.brightness)
    elif a == "timer":
        cmd.update(cmd="timer", value=args.value)
    elif a == "blink":
        cmd.update(cmd="blink", color=args.color, count=args.count, whenOff=args.force)
    elif a in ("wallpaper", "accent"):
        cmd.update(cmd=a, force=args.force, path=getattr(args, "image", None))
    return {k: v for k, v in cmd.items() if v is not None}


def connect_all(selector="all"):
    db = profiles.load_all()
    records = store.load_devices()
    if not records:
        raise SystemExit(3)
    out = []
    for r in records:
        if selector not in (None, "", "all") and selector not in (r["id"], r.get("name")) \
                and str(r.get("name", "")).lower() != str(selector).lower():
            continue
        lamp = lamps.Lamp(r, db)
        try:
            lamp.connect()
        except Exception as e:
            lamp.error = str(e)
        out.append(lamp)
    if not out:
        raise lamps.CommandError("no lamp %r" % selector)
    return out


def run_direct(cmd):
    """Execute a lamp command without the shell. Returns the lamps' states."""
    targets = connect_all(cmd.get("lamp"))
    try:
        name = cmd["cmd"]
        hs = None
        if name in ("wallpaper", "accent"):
            colour = (wallpaper.image_colour(cmd.get("path") or wallpaper.current_wallpaper())
                      if name == "wallpaper" else wallpaper.theme_colour("accent"))
            hs = wallpaper.lamp_hsv(colour)
        for lamp in targets:
            if not lamp.online:
                continue
            st = lamp.state()
            if name == "blink":
                if st.get("on") or cmd.get("whenOff"):
                    lamp.blink(cmd.get("color") or "#ff0000", int(cmd.get("count", 3)))
            elif hs is not None:
                if st.get("on") or cmd.get("force"):
                    lamp.send(lamp.plan({"cmd": "hsv", "value": [hs[0], hs[1], 1.0]}))
            else:
                lamp.send(lamp.plan(cmd))
            deadline = time.monotonic() + 0.6
            while time.monotonic() < deadline:
                lamp.pump(max(0.05, deadline - time.monotonic()))
        return [l.state() for l in targets]
    finally:
        for l in targets:
            l.close()


def describe(state):
    if not state.get("online"):
        return "%s: offline (%s)" % (state["name"], state.get("error") or "unreachable")
    if not state.get("on"):
        return "%s: off" % state["name"]
    parts = ["on", str(state.get("mode"))]
    if state.get("mode") == "colour" and state.get("color"):
        parts.append(state["color"])
    if state.get("brightness") is not None:
        parts.append("%d%%" % state["brightness"])
    if state.get("timer"):
        parts.append("off in %d min" % (state["timer"] // 60))
    return "%s: %s" % (state["name"], ", ".join(parts))


def cmd_lamp(args):
    cmd = to_command(args)
    if shell_running():
        res = shell_call("command", json.dumps(cmd))
        if res is None or res.startswith("error"):
            return _err(args, res or "shell call failed")
        time.sleep(0.4 if cmd["cmd"] != "blink" else 0.1)
        states = json.loads(shell_call("status") or "{}").get("lamps", [])
        via = "shell"
    else:
        states = run_direct(cmd)
        via = "direct"
    _out(args, {"ok": True, "via": via, "lamps": states}, "\n".join(describe(s) for s in states))
    return 0


def cmd_status(args):
    if shell_running():
        data = json.loads(shell_call("status") or "{}")
        states, via = data.get("lamps", []), "shell"
    else:
        states, via = [], "direct"
        for l in connect_all(args.lamp):
            states.append(l.state())
            l.close()
    _out(args, {"ok": True, "via": via, "lamps": states},
         "\n".join(describe(s) for s in states) or "no lamps configured (omamood setup qr)")
    return 0


def cmd_devices(args):
    recs = store.load_devices()
    safe = [{k: v for k, v in r.items() if k != "key" or args.show_keys} for r in recs]
    _out(args, {"ok": True, "devices": safe, "file": store.devices_path()},
         "\n".join("%-24s %-15s %-16s %s" % (r.get("name"), r.get("ip") or "-", r.get("profile") or "-", r["id"])
                   for r in recs) or "no devices in %s" % store.devices_path())
    return 0


def cmd_profiles(args):
    db = profiles.load_all()
    rows = [{"id": p["id"], "name": p["name"], "match": p["match"], "capabilities": sorted(p["dps"]),
             "tested": [t["model"] for t in p["tested"]], "file": p["_file"]} for p in db.values()]
    _out(args, {"ok": True, "profiles": rows},
         "\n".join("%-16s %s\n%16s match %s; tested: %s" % (r["id"], r["name"], "", json.dumps(r["match"]),
                                                            ", ".join(r["tested"]) or "not yet")
                   for r in rows))
    return 0


KEY_HELP = ("the local key is never taken on the command line (other processes can read "
            "arguments, and shells keep them in history): type it at the prompt, or pipe it "
            "with --key-stdin")


class KeyError_(ValueError):
    pass


def read_key(args, stdin=None):
    """The 16-character local key, from stdin (--key-stdin) or a hidden prompt."""
    stdin = stdin or sys.stdin
    if getattr(args, "key", None):
        raise KeyError_("--key is not accepted: " + KEY_HELP)
    if getattr(args, "key_stdin", False):
        key = stdin.readline().strip()
    elif stdin.isatty():
        key = getpass.getpass("Local key (hidden): ").strip()
    else:
        raise KeyError_("no key given: " + KEY_HELP)
    if len(key) != 16:
        raise KeyError_("a Tuya local key is 16 characters, got %d" % len(key))
    return key


def cmd_probe(args):
    """Dump a device's raw DPs and what OmaMood makes of them: the first step of
    adding support for a new lamp (docs/ADDING_DEVICES.md)."""
    if args.id and args.ip:
        rec = {"id": args.id, "key": read_key(args), "ip": args.ip, "version": args.version, "name": args.id}
    else:
        recs = [r for r in store.load_devices() if args.lamp in (None, "all", r["id"], r.get("name"))]
        if not recs:
            return _err(args, "no such lamp; pass --id and --ip (the key is asked for) to probe an unconfigured device", 3)
        rec = recs[0]
    db = profiles.load_all()
    d = tuya.Device(rec["id"], rec["ip"], rec["key"], rec.get("version", "3.3"))
    d.connect()
    status = d.query()
    d.close()
    prof = profiles.pick(db, status, rec.get("product"))
    data = {"ok": True, "device": {k: rec.get(k) for k in ("id", "name", "ip", "version", "product")},
            "dps": status, "profile": prof["id"] if prof else None,
            "state": profiles.Light(prof).decode(status) if prof else None}
    if not prof:
        data["draft_profile"] = draft_profile(status)
    human = ["DPs:"] + ["  %4s = %r" % (k, v) for k, v in sorted(status.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 999)]
    human.append("profile: %s" % (data["profile"] or "none matches - draft below"))
    human.append(json.dumps(data["state"] or data.get("draft_profile"), indent=2))
    _out(args, data, "\n".join(human))
    return 0


def draft_profile(status):
    """A starting point for devices/<id>.json from a raw status dict."""
    bools = [k for k, v in status.items() if isinstance(v, bool)]
    hex12 = [k for k, v in status.items() if isinstance(v, str) and len(v) == 12 and all(c in "0123456789abcdef" for c in v)]
    hex14 = [k for k, v in status.items() if isinstance(v, str) and len(v) == 14 and all(c in "0123456789abcdef" for c in v)]
    modes = [k for k, v in status.items() if v in ("white", "colour", "scene", "music")]
    ints = [k for k, v in status.items() if isinstance(v, int) and not isinstance(v, bool)]
    dps = {}
    if bools:
        dps["power"] = {"dp": bools[0], "type": "bool"}
    if modes:
        dps["mode"] = {"dp": modes[0], "type": "enum", "values": {"white": "white", "colour": "colour"}}
    if ints:
        dps["brightness"] = {"dp": ints[0], "type": "int", "min": 10, "max": 1000}
    if hex12:
        dps["colour"] = {"dp": hex12[0], "type": "colour", "format": "hsv16"}
    elif hex14:
        dps["colour"] = {"dp": hex14[0], "type": "colour", "format": "rgbhsv"}
    return {"id": "my-lamp", "name": "Brand Model", "priority": 100,
            "match": {"product": ["<product name from `omamood devices --json`>"], "dps": sorted(status)},
            "dps": dps, "write": {"onePerMessage": True, "modeLast": True}, "tested": []}


def cmd_remove(args):
    """Forget a lamp: drop it from devices.json (its key with it)."""
    target = args.name or (args.lamp if args.lamp != "all" else None)
    if not target:
        return _err(args, "say which lamp: omamood remove NAME", 2)
    if shell_running():
        before = json.loads(shell_call("status") or "{}").get("lamps", [])
        if not any(target.lower() in (l["id"].lower(), str(l["name"]).lower()) for l in before):
            return _err(args, "no lamp %r (have: %s)" % (target, ", ".join(l["name"] for l in before) or "none"))
        res = shell_call("command", json.dumps({"cmd": "remove", "lamp": target}))
        if res != "ok":
            return _err(args, res or "shell call failed")
        time.sleep(0.3)
        left = [l["name"] for l in json.loads(shell_call("status") or "{}").get("lamps", [])]
    else:
        if store.remove_device(target) is None:
            return _err(args, "no lamp %r" % target)
        left = [d.get("name") for d in store.load_devices()]
    _out(args, {"ok": True, "removed": target, "lamps": left},
         "removed %s; left: %s" % (target, ", ".join(left) or "none"))
    return 0


def cmd_discover(args):
    recs = store.load_devices()
    if not recs:
        return _err(args, "no devices configured", 3)
    statuses = discovery.locate(recs, log=(lambda *_: None) if args.json else (lambda *m: print(*m, file=sys.stderr)))
    store.save_devices(recs)
    shell_call("reload")
    _out(args, {"ok": True, "found": sorted(statuses), "devices": [{"id": r["id"], "name": r.get("name"), "ip": r.get("ip")} for r in recs]},
         "\n".join("%-24s %s" % (r.get("name"), r.get("ip") if r["id"] in statuses else "not found") for r in recs))
    return 0


def cmd_setup_qr(args):
    from .bridge import setup_devices
    code = args.user_code or input("Smart Life User Code (Me > Settings > Account and Security > User Code): ").strip()
    token = cloud.request_qr(code)
    text = cloud.QR_PREFIX + token
    if shutil.which("qrencode"):
        subprocess.run(["qrencode", "-t", "ANSIUTF8", "-m", "2", text], stdout=sys.stderr)
    print("Scan with Smart Life (+ > Scan) and tap Confirm login. Waiting up to 3 minutes...", file=sys.stderr)
    login, deadline = None, time.monotonic() + 180
    while login is None and time.monotonic() < deadline:
        time.sleep(2)
        login = cloud.poll_login(token, code)
    if login is None:
        return _err(args, "the QR code expired")
    session = cloud.Session(login)
    try:
        found = cloud.to_device_records(session.devices())
    finally:
        session.logout()
    added, skipped = setup_devices(found, profiles.load_all(), log=lambda *m: print(*m, file=sys.stderr))
    shell_call("reload")
    _out(args, {"ok": True, "added": [{"name": d["name"], "ip": d.get("ip"), "profile": d["profile"]} for d in added],
                "skipped": skipped},
         "added: %s\nskipped: %s" % (", ".join(d["name"] for d in added) or "none", ", ".join(skipped) or "none"))
    return 0


def cmd_setup_manual(args):
    from .bridge import setup_devices
    rec = {"id": args.id, "key": read_key(args), "name": args.name or args.id, "version": args.version,
           "product": [args.product] if args.product else []}
    if args.ip:
        rec["ip"] = args.ip
    added, skipped = setup_devices([rec], profiles.load_all())
    shell_call("reload")
    if not added:
        return _err(args, "could not add: %s" % ", ".join(skipped))
    _out(args, {"ok": True, "added": [{k: v for k, v in d.items() if k != "key"} for d in added]},
         "added %s (%s) as %s" % (added[0]["name"], added[0].get("ip") or "IP not found yet", added[0]["profile"]))
    return 0


# ---------------------------------------------------------------- argparse

def parser():
    p = argparse.ArgumentParser(prog="omamood", description="Control Tuya Wi-Fi lamps from Omarchy (LAN, no cloud).")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--lamp", default="all", help="lamp name or id (default: all)")
    # The same two options are accepted after the command too (`omamood status --json`).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="machine-readable output")
    common.add_argument("--lamp", default=argparse.SUPPRESS, help="lamp name or id (default: all)")
    sub = p.add_subparsers(dest="action", metavar="COMMAND")
    _add = sub.add_parser
    sub.add_parser = lambda name, **kw: _add(name, parents=[common], **kw)

    sub.add_parser("status", help="show every lamp's state")
    for a, h in (("on", "turn on"), ("off", "turn off"), ("toggle", "toggle power")):
        sub.add_parser(a, help=h)
    s = sub.add_parser("brightness", help="set brightness: 50, +10, -10")
    s.add_argument("value")
    s = sub.add_parser("white", help="white mode, optional brightness")
    s.add_argument("value", nargs="?")
    s = sub.add_parser("color", help="colour: #rrggbb or a name")
    s.add_argument("value")
    s.add_argument("--brightness")
    s = sub.add_parser("timer", help="turn off after MINUTES (0 cancels)")
    s.add_argument("value")
    s = sub.add_parser("blink", help="flash, then restore")
    s.add_argument("--color", default="#ff0000")
    s.add_argument("--count", type=int, default=3)
    s.add_argument("--force", action="store_true", help="also when the lamp is off")
    s = sub.add_parser("wallpaper", help="colour from the current wallpaper (or IMAGE)")
    s.add_argument("image", nargs="?")
    s.add_argument("--force", action="store_true", help="also when the lamp is off")
    s = sub.add_parser("accent", help="colour from the theme accent")
    s.add_argument("--force", action="store_true")

    sub.add_parser("devices", help="configured lamps").add_argument("--show-keys", action="store_true")
    sub.add_parser("profiles", help="known device profiles")
    sub.add_parser("discover", help="find the lamps' IP addresses again")
    s = sub.add_parser("remove", help="forget a lamp (its key is deleted)")
    s.add_argument("name", nargs="?", help="lamp name or id")
    s = sub.add_parser("probe", help="dump a device's raw data points (adding a device)")
    for f in ("--id", "--ip"):
        s.add_argument(f)
    s.add_argument("--key-stdin", action="store_true", help="read the local key from stdin instead of prompting")
    s.add_argument("--key", help=argparse.SUPPRESS)     # refused with an explanation
    s.add_argument("--version", default="3.3")

    s = sub.add_parser("setup", help="add lamps")
    ss = s.add_subparsers(dest="how", metavar="HOW", required=True)
    _add_ss = ss.add_parser
    ss.add_parser = lambda name, **kw: _add_ss(name, parents=[common], **kw)
    q = ss.add_parser("qr", help="log in with a Smart Life QR code (no developer account)")
    q.add_argument("--user-code")
    m = ss.add_parser("manual", help="add a lamp from its id and local key")
    m.add_argument("--id", required=True)
    m.add_argument("--key-stdin", action="store_true", help="read the local key from stdin instead of prompting")
    m.add_argument("--key", help=argparse.SUPPRESS)     # refused with an explanation
    m.add_argument("--ip")
    m.add_argument("--name")
    m.add_argument("--product")
    m.add_argument("--version", default="3.3")

    sub.add_parser("bridge", help="JSON-lines helper for the Omarchy shell (BRIDGE.md)")
    return p


def main(argv):
    args = parser().parse_args(argv)
    if args.action is None:
        args.action = "status"
    try:
        if args.action == "bridge":
            from .bridge import main as bridge_main
            return bridge_main()
        if args.action in LAMP_CMDS:
            return cmd_lamp(args)
        if args.action == "setup":
            return cmd_setup_qr(args) if args.how == "qr" else cmd_setup_manual(args)
        return {"status": cmd_status, "devices": cmd_devices, "profiles": cmd_profiles,
                "probe": cmd_probe, "discover": cmd_discover, "remove": cmd_remove}[args.action](args)
    except SystemExit as e:
        if e.code == 3:
            return _err(args, "no lamps configured: run `omamood setup qr`", 3)
        raise
    except tuya.UnsupportedProtocol as e:
        return _err(args, str(e), 4)
    except KeyError_ as e:
        return _err(args, str(e), 2)
    except (lamps.CommandError, cloud.CloudError, profiles.ProfileError, tuya.TuyaError, OSError, TimeoutError, ValueError) as e:
        return _err(args, str(e))
    except KeyboardInterrupt:
        return 130
