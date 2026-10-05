"""Where OmaMood keeps things. Never inside the plugin directory: every file
written there makes the shell reload the plugin.

  $XDG_CONFIG_HOME/omamood/devices.json   lamps: id, name, local key, ip, profile (0600)
  $XDG_CACHE_HOME/omamood/colors.json     wallpaper -> colour cache
"""

import json
import os
import tempfile


def _read_json(path):
    with open(path) as f:
        return json.load(f)


def config_dir():
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "omamood")


def cache_dir():
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "omamood")


def devices_path():
    return os.environ.get("OMAMOOD_DEVICES") or os.path.join(config_dir(), "devices.json")


def _write_private(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


DEVICE_FIELDS = ("id", "name", "key", "ip", "version", "profile", "product", "category", "mac")


def load_devices():
    """Return a list of device dicts (may be empty)."""
    try:
        data = _read_json(devices_path())
    except FileNotFoundError:
        return []
    except ValueError as e:
        raise ValueError("%s is not valid JSON: %s" % (devices_path(), e))
    return [d for d in data.get("devices", []) if d.get("id") and d.get("key")]


def save_devices(devices):
    clean = [{k: d[k] for k in DEVICE_FIELDS if d.get(k) not in (None, "")} for d in devices]
    _write_private(devices_path(), {"version": 1, "devices": clean})


def upsert_devices(new):
    """Merge by id: new fields win, fields the new record lacks (e.g. ip) are kept."""
    by_id = {d["id"]: d for d in load_devices()}
    for d in new:
        by_id[d["id"]] = dict(by_id.get(d["id"], {}), **{k: v for k, v in d.items() if v not in (None, "")})
    save_devices(list(by_id.values()))
    return list(by_id.values())


def load_cache(name):
    try:
        return _read_json(os.path.join(cache_dir(), name))
    except (FileNotFoundError, ValueError):
        return {}


def save_cache(name, data):
    os.makedirs(cache_dir(), exist_ok=True)
    path = os.path.join(cache_dir(), name)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)
