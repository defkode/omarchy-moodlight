---
name: add-device
description: Add or verify OmaMood support for a user's Tuya Wi-Fi lamp - probe its data points, write a device profile and an owner pin, run tools/check. Use when a lamp shows "no profile matches", decodes wrongly, or the user wants their model marked tested.
---

# Add a device to OmaMood

The user owns the lamp; you can only learn what it answers. Never invent DPs.

1. **Is it configured?** `./omamood devices --json`. If not: `./omamood setup qr`
   (the user scans with Smart Life; you cannot do that step) or
   `./omamood setup manual --id ID [--ip IP] --name NAME` when they
   already have the local key: it prompts for the key, so let the user type it, or
   have them pipe it from their own file with `--key-stdin < FILE`. Never put a key
   on a command line, in chat, in files in the repo, or in commits.
2. **Probe.** `./omamood probe --lamp NAME --json`. Keep the output (minus `device.id`)
   for the pin. If `profile` is set and `state` matches what the lamp shows, it
   already works - ask the user whether they want it pinned as tested.
3. **Find the meaning of each DP.** Ask the user to change one thing on the lamp
   (power button, mode button, brightness in the app), probe again, diff. One
   change per probe. Record which DP moved and how its value is encoded.
4. **Write `devices/<id>.json`** following `docs/ADDING_DEVICES.md`. Prefer
   `"extends": "tuya-light-v2"` (or `-v1`) and override only what differs. Match by
   `product` (from `devices --json`) so you never change what another lamp gets.
5. **Pin it**: `tests/pins/<id>.json` from the probes - real statuses with the state
   they decode to, and the write plans for what you verified on the lamp
   (`./omamood color red`, `white 50`, `off`/`on`, `blink`; confirm with the user
   that the lamp did it). `"owner"` is the user's handle.
6. **Run `tools/check`** until it passes. Do not edit another profile's file or pin
   to make it pass.
7. **Try it live** (see AGENTS.md "Trying a change live"), then add the README row.

If the device speaks protocol 3.1/3.4/3.5, `probe` exits 4 with "not supported
yet": that is a protocol addition (docs/ADDING_DEVICES.md, last section), a bigger
change - tell the user before starting it.
