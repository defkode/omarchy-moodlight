"""Colour for the lamp from the current Omarchy wallpaper or theme.

Extraction runs ImageMagick (shipped with Omarchy): shrink, quantise to eight
colours, and pick the cluster with the best count x vividness score, so a big
grey sky does not win over the coloured subject. Results are cached by path +
mtime, so a theme switch back to a known wallpaper costs no decode.

The lamp policy (`lamp_hsv`) keeps hue, lifts saturation to a floor so muted
palettes still read as colour on an LED, and turns greys (whose hue means
nothing) into plain white.
"""

import colorsys
import os
import re
import subprocess
import tomllib

from . import store

STATE = os.path.expanduser("~/.local/state/omarchy/current")
BACKGROUND_LINK = os.path.join(STATE, "background")
COLORS_TOML = os.path.join(STATE, "theme", "colors.toml")
CACHE = "colors.json"

_HIST = re.compile(r"^\s*(\d+):\s*\([^)]*\)\s*#([0-9A-Fa-f]{6})")


def parse_histogram(text):
    """ImageMagick `histogram:info:-` lines -> [(count, (r, g, b))]."""
    out = []
    for line in text.splitlines():
        m = _HIST.match(line)
        if m:
            h = m.group(2)
            out.append((int(m.group(1)), tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))))
    return out


def pick_colour(clusters):
    """Most prominent vivid colour of [(count, rgb)]."""
    best, best_score = (255, 255, 255), -1.0
    for count, rgb in clusters:
        _, s, v = colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))
        score = count * (0.15 + s) * min(1.0, v * 3)
        if score > best_score:
            best, best_score = rgb, score
    return best


def image_colour(path):
    """#rrggbb for an image file, cached."""
    path = os.path.realpath(path)
    st = os.stat(path)
    key = "%s:%d:%d" % (path, st.st_mtime_ns, st.st_size)
    cache = store.load_cache(CACHE)
    if key in cache:
        return cache[key]
    cmd = ["magick"]
    if path.lower().endswith((".jpg", ".jpeg")):
        cmd += ["-define", "jpeg:size=192x192"]
    cmd += [path, "-sample", "96x96", "+dither", "-colors", "8", "-format", "%c", "histogram:info:-"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if out.returncode != 0:
        raise RuntimeError("magick failed on %s: %s" % (path, out.stderr.strip()[:200]))
    r, g, b = pick_colour(parse_histogram(out.stdout))
    colour = "#%02x%02x%02x" % (r, g, b)
    cache = {k: v for k, v in cache.items() if not k.startswith(path + ":")}
    if len(cache) > 500:
        cache = dict(list(cache.items())[-400:])
    cache[key] = colour
    store.save_cache(CACHE, cache)
    return colour


def current_wallpaper():
    return os.path.realpath(BACKGROUND_LINK) if os.path.exists(BACKGROUND_LINK) else None


def theme_colour(name="accent"):
    with open(COLORS_TOML, "rb") as f:
        return tomllib.load(f)[name]


def lamp_hsv(hex_colour, saturation_floor=0.7, grey_below=0.08):
    """Theme/wallpaper colour -> (h, s) for the lamp. v is left to the caller
    (the lamp keeps its brightness)."""
    t = hex_colour.lstrip("#")
    r, g, b = (int(t[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    h, s, _ = colorsys.rgb_to_hsv(r, g, b)
    if s < grey_below:
        return 0.0, 0.0
    return h * 360.0, max(s, saturation_floor)
