// OmaMood decisions, with no QML in them: Service.qml and Panel.qml call these,
// and tests/model.test.js runs them under Deno.
.pragma library

// Nerd Font glyphs, built from code points: editing tools can mangle raw
// private-use characters in source files.
var GLYPH_ON = String.fromCodePoint(0xF06E8)     // nf-md-lightbulb_on
var GLYPH_OFF = String.fromCodePoint(0xF0336)    // nf-md-lightbulb_outline
var GLYPH_NONE = String.fromCodePoint(0xF0335)   // nf-md-lightbulb
var GLYPH_SETTINGS = String.fromCodePoint(0xF0493) // nf-md-cog

var PALETTE_KEYS = ["accent", "red", "yellow", "green", "cyan", "blue", "magenta"]

// One stdout line from the bridge -> object, or null if it is not ours.
function parseLine(line) {
  var text = String(line || "").trim()
  if (text === "" || text[0] !== "{") return null
  try {
    var obj = JSON.parse(text)
    return obj && typeof obj.type === "string" ? obj : null
  } catch (e) {
    return null
  }
}

function anyOn(lamps) {
  for (var i = 0; i < (lamps || []).length; i++)
    if (lamps[i].online && lamps[i].on) return true
  return false
}

function onlineCount(lamps) {
  var n = 0
  for (var i = 0; i < (lamps || []).length; i++) if (lamps[i].online) n++
  return n
}

function glyph(lamps) {
  if (!lamps || lamps.length === 0) return GLYPH_NONE
  return anyOn(lamps) ? GLYPH_ON : GLYPH_OFF
}

// The colour the bar icon is tinted with: the first lit lamp's colour, or null
// (use the bar's foreground). White mode tints nothing.
function tint(lamps) {
  for (var i = 0; i < (lamps || []).length; i++) {
    var l = lamps[i]
    if (l.online && l.on && l.mode === "colour" && l.hsv) {
      // Full value: the bar should show the hue, not the lamp's dimness.
      return hsvToHex(l.hsv[0], Math.max(0.35, l.hsv[1]), 1)
    }
  }
  return null
}

function hsvToHex(h, s, v) {
  h = ((h % 360) + 360) % 360
  var c = v * s, x = c * (1 - Math.abs((h / 60) % 2 - 1)), m = v - c
  var rgb = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x]
    : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x]
  return "#" + rgb.map(function(n) {
    var s2 = Math.round((n + m) * 255).toString(16)
    return s2.length < 2 ? "0" + s2 : s2
  }).join("")
}

// The line under a lamp's name: only news. "" for a lamp that is simply on
// (the switch and the brightness label already say so).
function lampLine(l, searching) {
  if (!l.online) return searching ? "Looking for it on your network…"
    : "Can't reach it. Is it plugged in and on Wi‑Fi?"
  if (!l.on) return "Off"
  if (l.timer) return "Off in " + Math.ceil(l.timer / 60) + " min"
  return ""
}

function summary(lamps, setup) {
  if (setup && ["requesting", "scan", "fetching", "locating", "error"].indexOf(setup.stage) >= 0) return stageLabel(setup)
  if (!lamps || lamps.length === 0) return "No lamps yet"
  var on = 0, online = onlineCount(lamps)
  for (var i = 0; i < lamps.length; i++) if (lamps[i].online && lamps[i].on) on++
  if (online === 0) return lamps.length === 1 ? "Offline" : "All offline"
  // One lamp: its card says it all. Several: how many are on.
  if (lamps.length === 1) return ""
  return on + " of " + lamps.length + " on"
}

// "matte-black" -> "Matte Black", the way `omarchy theme current` shows it.
function themeTitle(slug) {
  return String(slug || "").trim().split("-").filter(function(w) { return w !== "" })
    .map(function(w) { return w.charAt(0).toUpperCase() + w.slice(1) }).join(" ")
}

// Subtitle under "OmaMood": setup and trouble first, then what the lamp is doing.
function headline(lamps, setup, colorSource, themeSlug) {
  var s = summary(lamps, setup)
  if (s !== "" && (!lamps || lamps.length === 0 || (setup && setup.stage !== "idle" && setup.stage !== "done")))
    return s
  var online = onlineCount(lamps)
  if (online === 0) return lamps.length === 1 ? "Lamp offline" : "Lamps offline"
  if (!anyOn(lamps)) return "Lights out"
  var theme = themeTitle(themeSlug)
  if (colorSource === "off") return "Your colours, your rules"
  if (!theme) return colorSource === "wallpaper" ? "Matching the wallpaper" : "Matching the theme"
  return colorSource === "wallpaper" ? "Wearing " + theme : "Matching " + theme
}

function tooltip(lamps) {
  if (!lamps || lamps.length === 0) return "OmaMood: add a lamp"
  return lamps.map(function(l) {
    var state = !l.online ? "offline" : !l.on ? "off" : (l.brightness !== null && l.brightness !== undefined ? l.brightness + "%" : "on")
    return l.name + " · " + state
  }).join("\n")
}

function stageLabel(setup) {
  switch (setup && setup.stage) {
    case "requesting": return "Asking Tuya for a QR code…"
    case "scan": return "Scan with Smart Life, then tap Confirm login"
    case "fetching": return "Reading your devices…"
    case "locating": return "Finding lamps on your network…"
    case "done": return setup.added && setup.added.length
      ? "Added " + setup.added.join(", ") : "No new lamps found"
    case "error": return setup.error || "Setup failed"
    default: return ""
  }
}

// "Find lamps" progress line; "" when there is nothing to say.
function discoveryLabel(d) {
  if (!d || d.stage === "idle" || !d.stage) return ""
  if (d.stage === "searching") return "Searching your network…"
  if (d.error) return "Search failed: " + d.error
  var parts = (d.found || []).map(function(f) {
    return f.name + (f.moved ? " moved to " : " at ") + f.ip
  })
  if ((d.missing || []).length) parts.push(d.missing.join(", ") + " not found")
  return parts.length ? parts.join(" · ") : "No lamps configured"
}

// colors.toml -> {accent: "#..", red: "#..", ...}; tolerant line parser.
function parseColorsToml(text) {
  var out = {}
  String(text || "").split("\n").forEach(function(line) {
    var m = line.match(/^\s*([a-z_]+)\s*=\s*"(#[0-9a-fA-F]{6})"/)
    if (m) out[m[1]] = m[2].toLowerCase()
  })
  return out
}

// White first (the lamp's white mode, not RGB white), then the theme's colours.
function swatches(palette) {
  var out = [{ key: "white", color: "#ffffff", white: true }]
  var seen = { "#ffffff": true }
  PALETTE_KEYS.forEach(function(k) {
    var c = palette && palette[k]
    if (c && !seen[c]) { seen[c] = true; out.push({ key: k, color: c }) }
  })
  return out
}

// Settings pushed to the bridge (it applies the saturation and sleep policy).
function bridgeSettings(s) {
  return {
    cmd: "settings",
    saturationFloor: Math.max(0, Math.min(100, Number(s.saturationFloor))) / 100,
    sleepAction: s.sleepAction === "none" ? "none" : "off"
  }
}

// The command a theme change sends, or null when following is off.
function themeCommand(colorSource) {
  if (colorSource === "wallpaper") return { cmd: "wallpaper" }
  if (colorSource === "accent") return { cmd: "accent" }
  return null
}

// Validate a JSON command string from IPC. Returns {ok, cmd|error}.
var COMMANDS = ["power", "brightness", "white", "color", "colour", "hsv", "timer", "blink", "refresh",
  "wallpaper", "accent", "reload", "state", "discover", "retry", "check", "remove", "setup.qr", "setup.cancel"]

function parseCommand(json) {
  var cmd
  try { cmd = JSON.parse(json) } catch (e) { return { ok: false, error: "not JSON: " + e } }
  if (!cmd || typeof cmd !== "object" || COMMANDS.indexOf(cmd.cmd) < 0)
    return { ok: false, error: "unknown cmd; one of " + COMMANDS.join(", ") }
  return { ok: true, cmd: cmd }
}

// Exponential restart backoff for the bridge: 2s, 4s, ... 5 min.
function backoff(previousMs) {
  return previousMs ? Math.min(300000, previousMs * 2) : 2000
}
