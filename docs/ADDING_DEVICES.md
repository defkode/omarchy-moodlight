# Adding a device

Most Tuya lights already work: OmaMood picks a profile by the data points (DPs)
the lamp reports, and the two generic profiles cover the two standard layouts.
You only add a file when your lamp needs a quirk, or to mark it **tested**.

## 1. Look at what your lamp says

Add it first (`omamood setup qr`, or `setup manual --id --key [--ip]`), then:

```bash
omamood probe --lamp "Desk lamp" --json
```

You get its raw `dps`, the `profile` picked (or `null`), the decoded `state`,
and — when nothing matches — a `draft_profile` to start from. For a device you
have not added: `omamood probe --id ID --key KEY --ip IP`.

Change the lamp with its own button or the Smart Life app and probe again to
see which DP moved. Note the product name from `omamood devices --json`.

## 2. Write `devices/<your-id>.json`

```json
{
  "id": "acme-bulb",                       // = file name
  "name": "ACME A60 RGBW bulb",
  "description": "What is special about it.",
  "extends": "tuya-light-v2",              // inherit a standard layout, override below
  "priority": 100,                         // product profiles beat generic ones
  "match": {
    "product": ["ACME A60"],               // cloud product name or id, exact
    "dps": ["20", "21", "22"]              // and/or: DPs that must all be present
  },
  "dps": {
    "power":       { "dp": "20", "type": "bool" },
    "mode":        { "dp": "21", "type": "enum", "values": { "white": "white", "colour": "colour" } },
    "brightness":  { "dp": "22", "type": "int", "min": 10, "max": 1000 },
    "temperature": { "dp": "23", "type": "int", "min": 0, "max": 1000 },   // null removes an inherited one
    "colour":      { "dp": "24", "type": "colour", "format": "hsv16" },   // or "rgbhsv" (v1 lamps)
    "timer":       { "dp": "26", "type": "int", "unit": "s", "max": 86400 },
    "realtime":    { "dp": "28", "type": "realtime", "format": "control_v2" }  // instant changes (blink)
  },
  "write": { "onePerMessage": true, "modeLast": true },
  "notes": ["Anything you learned the hard way."],
  "tested": [{ "owner": "your-github", "model": "exact model", "protocol": "3.3",
               "date": "YYYY-MM-DD", "pin": "tests/pins/acme-bulb.json" }]
}
```

(Comments are for this page only; JSON has none.)

Capabilities are exactly: `power` (required), `mode`, `brightness`,
`temperature`, `colour`, `timer`, `realtime`. Colour formats: `hsv16` =
`HHHHSSSSVVVV` hex (h 0-360, s/v 0-1000); `rgbhsv` = `RRGGBBHHHHSSVV`.
`write.onePerMessage` sends one DP per CONTROL (many lamps ignore a mode change
bundled with other DPs); `write.modeLast` writes the mode after the colour.

## 3. Pin it: `tests/pins/<your-id>.json`

Copy `tests/pins/lsc-moodlight.json`. `statuses` are real `dps` from your probe
with the `state` they must decode to; `writes` are calls on the profile
(`plan_power`, `plan_colour`, `plan_white`, `plan_timer`, `realtime`,
`plan_restore`) with the exact DP writes expected. Only what you saw your lamp
accept. No ids, no keys.

## 4. Check and try

```bash
tools/check
omamood status && omamood color blue && omamood blink && omamood wallpaper
```

Then a README row in "Lamps" with your model and what you tested.

## A new data-point layout or value format

Add the format to `encode_colour` / `decode_colour` in `lib/omamood/profiles.py`
and to `validate()`, with tests in `tests/test_core.py`. Existing formats must
keep producing the same bytes: their pins say so.

## A new protocol version (3.1, 3.4, 3.5)

Only 3.3 is implemented: it is what has been tested on hardware. 3.4 and 3.5
negotiate a session key and add HMAC (3.4) or AES-GCM (3.5) per frame; 3.1
sends plaintext queries. Extend `Codec` in `lib/omamood/tuya.py` behind
`SUPPORTED_VERSIONS`, add a fake-device test in `tests/test_core.py`, and a pin
from your real device. `aes.gcm_encrypt/gcm_decrypt` already exist.
