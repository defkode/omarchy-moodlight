# The bridge contract

`omamood bridge` is the one process the shell runs (Service.qml). Anything else
that speaks this contract can drive the lamps too: an agent, a test, a script.

## Process

- Started as `<plugin dir>/omamood bridge`; Python 3.11+ standard library only.
- Arms `PR_SET_PDEATHSIG` so it dies with the shell; exits 0 when stdin closes.
- Exit 4 means "cannot start here" (e.g. Python too old): the shell does not retry.
  Any other exit is retried with backoff 2 s → ×2 → 5 min.
- Never writes into the plugin directory. Reads `~/.config/omamood/devices.json`,
  caches wallpaper colours in `~/.cache/omamood/colors.json`.

## stdin: one JSON object per line

Every command may carry `"id"`; the bridge then answers with a `result` line.
`"lamp"` selects a lamp by id or name; omitted or `"all"` means every lamp.

| command | fields | effect |
|:--|:--|:--|
| `power` | `value`: `on` \| `off` \| `toggle` | |
| `brightness` | `value`: `50`, `"+10"`, `"-10"` (1-100) | keeps the mode and colour |
| `white` | `value`?: percent, `temperature`?: 0-100 | white mode; turns the lamp on |
| `color` / `colour` | `value`: `#rrggbb` \| name \| `{h,s,v}`; `brightness`? | keeps current brightness unless given; turns on |
| `hsv` | `value`: `[h 0-360, s 0-1, v 0-1]` | v ignored unless `keepBrightness: false` |
| `timer` | `value`: minutes (0 cancels) | |
| `blink` | `color`?, `count`? (3), `period`? (0.5 s), `whenOff`? | instant flashes, then exact restore; skips lamps that are off |
| `wallpaper` | `path`?, `force`? | colour from the wallpaper; skips lamps that are off unless `force` |
| `accent` | `force`? | colour from the theme accent |
| `refresh` | | re-read the lamp's DPs |
| `settings` | `saturationFloor` (0-1), `sleepAction` (`off`/`none`) | |
| `reload` | | re-read devices.json |
| `discover` | | find IPs again, save, reload |
| `retry` | `lamp`?, `minInterval`? (30 s) | reconnect unreachable lamps now; search the LAN for a new IP unless a search ran within `minInterval` |
| `remove` | `lamp` (required, id or name) | forget the lamp: drop it and its key from devices.json |
| `state` | | emit a state line now |
| `setup.qr` | `userCode` | Smart Life QR login; progress in `setup` |
| `setup.cancel` | | stop a login; clear an error |

## stdout: one JSON object per line

```json
{"type": "state", "configured": 1, "setup": {"stage": "idle"},
 "lamps": [{"id": "...", "name": "LSC Moodlight", "online": true, "error": null, "ip": "192.168.1.23",
            "profile": "lsc-moodlight", "on": true, "mode": "colour", "brightness": 76,
            "hsv": [208, 0.99, 0.76], "color": "#0168c1", "temperature": null, "timer": 0,
            "capabilities": ["brightness", "colour", "mode", "power", "realtime", "timer"]}]}
{"type": "result", "id": 7, "ok": true, "lamps": ["LSC Moodlight"]}
{"type": "result", "id": 8, "ok": false, "error": "no lamp 'desk'"}
```

An unreachable lamp is retried with backoff (1 s doubling to 15 s); after 3 failures the
bridge searches the LAN for it (at most every 5 minutes), so a lamp that got a new IP from
DHCP is found and reconnected without user action. Commands sent while a lamp is
unreachable trigger one connection attempt and are then dropped, except the after-wake
`restore`, which is retried for 2 minutes.

A full `state` line follows every change (coalesced over ~30 ms). The lamp's own
buttons and other apps show up too: the bridge keeps a connection per lamp and
reads its STATUS pushes. `discovery` reports `discover`: `{"stage": "searching"}`, then
`{"stage": "done", "found": [{"name", "ip", "moved"}], "missing": [names], "seconds"}`
(or `"error"`), back to `{"stage": "idle"}` after 15 s. `setup.stage` walks `requesting → scan (with "qr": rows of
"0"/"1") → fetching → locating → done (added, skipped)`, or `error` (`error`).

## stderr

Human-readable lines prefixed `omamood:`. Never keys or tokens.
