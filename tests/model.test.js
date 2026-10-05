// Deno tests for Model.js. Run: deno test --allow-read tests/model.test.js
import { assertEquals } from "jsr:@std/assert@1"

// Model.js is a QML JavaScript library (.pragma library); load it as plain JS.
const src = await Deno.readTextFile(new URL("../Model.js", import.meta.url))
const M = {}
new Function("exports", src.replace(/^\.pragma library$/m, "") + `
  for (const k of ["parseLine","anyOn","glyph","tint","hsvToHex","lampLine","summary","tooltip",
    "stageLabel","isAlertClass","shouldAlert","eventAddress","classOf","parseColorsToml","swatches",
    "bridgeSettings","themeCommand","parseCommand","backoff","GLYPH_ON","GLYPH_OFF","GLYPH_NONE"])
    exports[k] = eval(k)`)(M)

const lamp = (o) => Object.assign({ name: "Moodlight", online: true, on: true, mode: "colour",
  brightness: 76, hsv: [208, 0.99, 0.76], timer: 0 }, o)

Deno.test("parseLine", () => {
  assertEquals(M.parseLine('{"type":"state","lamps":[]}').type, "state")
  assertEquals(M.parseLine("omamood: log line"), null)
  assertEquals(M.parseLine('{"no":"type"}'), null)
  assertEquals(M.parseLine("{broken"), null)
})

Deno.test("glyph and tint", () => {
  assertEquals(M.glyph([]), M.GLYPH_NONE)
  assertEquals(M.glyph([lamp()]), M.GLYPH_ON)
  assertEquals(M.glyph([lamp({ on: false })]), M.GLYPH_OFF)
  assertEquals(M.glyph([lamp({ online: false })]), M.GLYPH_OFF)
  assertEquals(M.tint([lamp({ hsv: [0, 1, 0.2] })]), "#ff0000")      // full value, not the lamp's dimness
  assertEquals(M.tint([lamp({ mode: "white" })]), null)
  assertEquals(M.tint([lamp({ on: false })]), null)
})

Deno.test("hsvToHex", () => {
  assertEquals(M.hsvToHex(0, 1, 1), "#ff0000")
  assertEquals(M.hsvToHex(120, 1, 1), "#00ff00")
  assertEquals(M.hsvToHex(240, 1, 0.5), "#000080")
  assertEquals(M.hsvToHex(0, 0, 1), "#ffffff")
})

Deno.test("summary lines", () => {
  assertEquals(M.summary([], null), "No lamps yet")
  assertEquals(M.summary([lamp()], null), "Colour · 76%")
  assertEquals(M.summary([lamp({ on: false })], null), "Off")
  assertEquals(M.summary([lamp({ online: false })], null), "Offline")
  assertEquals(M.summary([lamp(), lamp({ on: false })], null), "1 of 2 on")
  assertEquals(M.summary([lamp()], { stage: "scan" }), "Scan with Smart Life, then tap Confirm login")
  assertEquals(M.lampLine(lamp({ mode: "white", brightness: 40, timer: 600 })), "White · 40% · off in 10 min")
})

Deno.test("terminal alert filtering", () => {
  const base = { enabled: true, cls: "com.mitchellh.ghostty", classes: "", openedAt: 0, now: 100000, lastAlertAt: 0 }
  assertEquals(M.shouldAlert(base), true)
  assertEquals(M.shouldAlert({ ...base, enabled: false }), false)
  assertEquals(M.shouldAlert({ ...base, cls: "firefox" }), false)
  assertEquals(M.shouldAlert({ ...base, cls: "firefox", classes: "*" }), true)
  // Ghostty's own attention request when its window opens:
  assertEquals(M.shouldAlert({ ...base, openedAt: 99000 }), false)
  // Cooldown between alerts:
  assertEquals(M.shouldAlert({ ...base, lastAlertAt: 97000 }), false)
  assertEquals(M.shouldAlert({ ...base, lastAlertAt: 90000 }), true)
})

Deno.test("hyprland helpers", () => {
  assertEquals(M.eventAddress("5c6400f94a00"), "0x5c6400f94a00")
  assertEquals(M.eventAddress("5c6400f94a00,2,com.mitchellh.ghostty,title"), "0x5c6400f94a00")
  const clients = JSON.stringify([{ address: "0x1", class: "firefox" }, { address: "0x5c6400f94a00", class: "com.mitchellh.ghostty" }])
  assertEquals(M.classOf(clients, "0x5c6400f94a00"), "com.mitchellh.ghostty")
  assertEquals(M.classOf("not json", "0x1"), "")
})

Deno.test("palette", () => {
  const p = M.parseColorsToml('accent = "#B59790"\nred = "#c38b7b"\nblue = "#b59790"\nfoo = 3\n')
  assertEquals(p, { accent: "#b59790", red: "#c38b7b", blue: "#b59790" })
  assertEquals(M.swatches(p).map((s) => s.key), ["accent", "red"])   // duplicates dropped
})

Deno.test("commands and settings", () => {
  assertEquals(M.parseCommand('{"cmd":"power","value":"on"}').ok, true)
  assertEquals(M.parseCommand('{"cmd":"rm -rf"}').ok, false)
  assertEquals(M.parseCommand("nope").ok, false)
  assertEquals(M.themeCommand("wallpaper"), { cmd: "wallpaper" })
  assertEquals(M.themeCommand("off"), null)
  assertEquals(M.bridgeSettings({ saturationFloor: 70, idleAction: "dim", sleepAction: "off" }),
    { cmd: "settings", saturationFloor: 0.7, idleAction: "dim", idleBrightness: 10, sleepAction: "off" })
  assertEquals(M.backoff(0), 2000)
  assertEquals(M.backoff(200000), 300000)
})
