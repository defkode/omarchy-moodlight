// Deno tests for Model.js. Run: deno test --allow-read tests/model.test.js
import { assertEquals } from "jsr:@std/assert@1"

// Model.js is a QML JavaScript library (.pragma library); load it as plain JS.
const src = await Deno.readTextFile(new URL("../Model.js", import.meta.url))
const M = {}
new Function("exports", src.replace(/^\.pragma library$/m, "") + `
  for (const k of ["parseLine","anyOn","glyph","tint","hsvToHex","lampLine","summary","tooltip",
    "stageLabel","discoveryLabel","themeTitle","headline","parseColorsToml","swatches",
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
  assertEquals(M.summary([lamp()], null), "")                          // one lamp: the card says it
  assertEquals(M.summary([lamp({ on: false })], null), "")
  assertEquals(M.summary([lamp({ online: false })], null), "Offline")
  assertEquals(M.summary([lamp(), lamp({ on: false })], null), "1 of 2 on")
  assertEquals(M.summary([lamp()], { stage: "scan" }), "Scan with Smart Life, then tap Confirm login")
  assertEquals(M.lampLine(lamp()), "")                                 // on: nothing to add
  assertEquals(M.lampLine(lamp({ on: false })), "Off")
  assertEquals(M.lampLine(lamp({ online: false, error: "timed out" })), "Can't reach it. Is it plugged in and on Wi‑Fi?")
  assertEquals(M.lampLine(lamp({ online: false }), true), "Looking for it on your network…")
  assertEquals(M.lampLine(lamp({ timer: 600 })), "Off in 10 min")
  assertEquals(M.tooltip([lamp(), lamp({ name: "Desk", on: false })]), "Moodlight · 76%\nDesk · off")
})

Deno.test("discovery label", () => {
  assertEquals(M.discoveryLabel({ stage: "idle" }), "")
  assertEquals(M.discoveryLabel(undefined), "")
  assertEquals(M.discoveryLabel({ stage: "searching" }), "Searching your network…")
  assertEquals(M.discoveryLabel({ stage: "done", found: [{ name: "Moodlight", ip: "10.0.0.5", moved: false }], missing: [] }),
    "Moodlight at 10.0.0.5")
  assertEquals(M.discoveryLabel({ stage: "done", found: [{ name: "Moodlight", ip: "10.0.0.9", moved: true }], missing: ["Desk"] }),
    "Moodlight moved to 10.0.0.9 · Desk not found")
  assertEquals(M.discoveryLabel({ stage: "done", found: [], missing: [], error: "boom" }), "Search failed: boom")
})

Deno.test("headline", () => {
  assertEquals(M.themeTitle("matte-black"), "Matte Black")
  assertEquals(M.themeTitle("p-bloom\n"), "P Bloom")
  assertEquals(M.themeTitle(""), "")
  const idle = { stage: "idle" }
  assertEquals(M.headline([lamp()], idle, "accent", "tokyo-night"), "Matching Tokyo Night")
  assertEquals(M.headline([lamp()], idle, "wallpaper", "tokyo-night"), "Wearing Tokyo Night")
  assertEquals(M.headline([lamp()], idle, "off", "tokyo-night"), "Your colours, your rules")
  assertEquals(M.headline([lamp({ on: false })], idle, "accent", "tokyo-night"), "Lights out")
  assertEquals(M.headline([lamp({ online: false })], idle, "accent", "x"), "Lamp offline")
  assertEquals(M.headline([lamp({ online: false }), lamp({ online: false })], idle, "accent", "x"), "Lamps offline")
  assertEquals(M.headline([], idle, "accent", "x"), "No lamps yet")
  assertEquals(M.headline([lamp()], { stage: "scan" }, "accent", "x"), "Scan with Smart Life, then tap Confirm login")
  assertEquals(M.headline([lamp()], idle, "accent", ""), "Matching the theme")
})

Deno.test("palette", () => {
  const p = M.parseColorsToml('accent = "#B59790"\nred = "#c38b7b"\nblue = "#b59790"\nfoo = 3\n')
  assertEquals(p, { accent: "#b59790", red: "#c38b7b", blue: "#b59790" })
  assertEquals(M.swatches(p).map((s) => s.key), ["white", "accent", "red"])   // white first, duplicates dropped
  assertEquals(M.swatches(p)[0].white, true)
})

Deno.test("commands and settings", () => {
  assertEquals(M.parseCommand('{"cmd":"power","value":"on"}').ok, true)
  assertEquals(M.parseCommand('{"cmd":"rm -rf"}').ok, false)
  assertEquals(M.parseCommand("nope").ok, false)
  assertEquals(M.themeCommand("wallpaper"), { cmd: "wallpaper" })
  assertEquals(M.themeCommand("off"), null)
  assertEquals(M.bridgeSettings({ saturationFloor: 70, sleepAction: "off" }),
    { cmd: "settings", saturationFloor: 0.7, sleepAction: "off" })
  assertEquals(M.backoff(0), 2000)
  assertEquals(M.backoff(200000), 300000)
})
