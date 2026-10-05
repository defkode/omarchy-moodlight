// OmaMood decisions, with no QML in them: Service.qml and Panel.qml call these,
// and tests/model.test.js runs them under Deno.
.pragma library

// Nerd Font glyphs, built from code points: editing tools can mangle raw
// private-use characters in source files.
var GLYPH_ON = String.fromCodePoint(0xF06E8)     // nf-md-lightbulb_on
var GLYPH_OFF = String.fromCodePoint(0xF0336)    // nf-md-lightbulb_outline
var GLYPH_NONE = String.fromCodePoint(0xF0335)   // nf-md-lightbulb

var DEFAULT_ALERT_CLASSES = "com.mitchellh.ghostty,Alacritty,kitty,foot,org.wezfurlong.wezterm"
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

function lampLine(l) {
  if (!l.online) return l.error ? "Offline · " + l.error : "Offline"
  if (!l.on) return "Off"
  var parts = [l.mode === "colour" ? "Colour" : l.mode === "white" ? "White" : String(l.mode || "On")]
  if (l.brightness !== null && l.brightness !== undefined) parts.push(l.brightness + "%")
  if (l.timer) parts.push("off in " + Math.ceil(l.timer / 60) + " min")
  return parts.join(" · ")
}

function summary(lamps, setup) {
  if (setup && ["requesting", "scan", "fetching", "locating", "error"].indexOf(setup.stage) >= 0) return stageLabel(setup)
  if (!lamps || lamps.length === 0) return "No lamps yet"
  var on = 0, online = onlineCount(lamps)
  for (var i = 0; i < lamps.length; i++) if (lamps[i].online && lamps[i].on) on++
  if (online === 0) return lamps.length === 1 ? "Offline" : "All offline"
  if (lamps.length === 1) return lampLine(lamps[0])
  return on + " of " + lamps.length + " on"
}

function tooltip(lamps) {
  if (!lamps || lamps.length === 0) return "OmaMood: add a lamp"
  return lamps.map(function(l) { return l.name + ": " + lampLine(l) }).join("\n")
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

// Terminal-alert filtering. `cls` is the urgent window's class; `classes` the
// comma-separated setting ("*" = any window). Ghostty also asks for attention
// when a window first opens: urgency within `graceMs` of opening is ignored.
function isAlertClass(cls, classes) {
  var list = String(classes || DEFAULT_ALERT_CLASSES).split(",").map(function(s) { return s.trim() })
  if (list.indexOf("*") >= 0) return true
  return list.indexOf(String(cls || "")) >= 0
}

function shouldAlert(opts) {
  // opts: {enabled, cls, classes, openedAt, now, lastAlertAt, graceMs, cooldownMs}
  if (!opts.enabled) return false
  if (!isAlertClass(opts.cls, opts.classes)) return false
  var now = opts.now
  if (opts.openedAt && now - opts.openedAt < (opts.graceMs || 2500)) return false
  if (opts.lastAlertAt && now - opts.lastAlertAt < (opts.cooldownMs || 5000)) return false
  return true
}

// Hyprland raw event data "ADDRESS" or "ADDRESS,..." -> "0xADDRESS".
function eventAddress(data) {
  var a = String(data || "").split(",")[0].trim()
  if (a === "") return ""
  return a.indexOf("0x") === 0 ? a : "0x" + a
}

// `hyprctl clients -j` output -> class of the window with this address.
function classOf(clientsJson, address) {
  try {
    var list = JSON.parse(clientsJson)
    for (var i = 0; i < list.length; i++) if (list[i].address === address) return list[i]["class"] || ""
  } catch (e) {}
  return ""
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

function swatches(palette) {
  var out = []
  var seen = {}
  PALETTE_KEYS.forEach(function(k) {
    var c = palette && palette[k]
    if (c && !seen[c]) { seen[c] = true; out.push({ key: k, color: c }) }
  })
  return out
}

// Settings pushed to the bridge (it applies saturation/idle/sleep policy).
function bridgeSettings(s) {
  return {
    cmd: "settings",
    saturationFloor: Math.max(0, Math.min(100, Number(s.saturationFloor))) / 100,
    idleAction: ["none", "dim", "off"].indexOf(s.idleAction) >= 0 ? s.idleAction : "none",
    idleBrightness: 10,
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
  "wallpaper", "accent", "idle", "reload", "state", "discover", "setup.qr", "setup.cancel"]

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
