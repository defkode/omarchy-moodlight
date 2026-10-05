# Working on OmaMood

Read this before changing anything. It is short; the files it names hold the detail.

## What this is

An Omarchy shell plugin for Tuya-based Wi-Fi lamps (LSC/Action, Lidl Livarno,
Nedis, Gosund, no-name), controlled over the LAN:

- `omamood` — one executable: the CLI people and agents use, and `omamood bridge`,
  the long-running helper the shell starts. Python **standard library only**.
- `lib/omamood/` — the Python package (see the table below).
- `devices/*.json` — **device profiles**: which Tuya data point (DP) means what. Adding a
  lamp is usually one JSON file and one pin; see `docs/ADDING_DEVICES.md`.
- `Service.qml` (once per session: bridge process, IPC, theme hook),
  `Panel.qml` (bar icon + panel, once per monitor), `Model.js` (pure logic, Deno-tested).

## Driving it (no code changes needed)

Everything a person can do, an agent can do from a shell. Prefer `--json`.

```bash
P=~/.config/omarchy/plugins/io.github.defkode.omamood/omamood   # or the repo checkout
$P status --json                       # every lamp: on, mode, brightness, color, online, error
$P on | off | toggle [--lamp NAME]
$P brightness 40 | +10 | -10
$P color '#ff8800' [--brightness 60]   # names: red orange yellow green cyan blue purple pink white
$P white [PERCENT]
$P wallpaper [--force]                 # colour from the current wallpaper (skips lamps that are off)
$P blink --color '#ff0000' --count 3   # flash, then restore exactly
$P timer 30                            # off in 30 min (0 cancels)
$P devices --json | profiles --json | discover | probe --lamp NAME --json
$P remove NAME                         # forget a lamp and its key (ask the user first)
```

When the shell is running the CLI goes through it (`"via": "shell"`), so the bar
stays in sync; otherwise it talks to the lamps directly (`"via": "direct"`).
The shell side is also reachable on its own: `omarchy-shell omamood status`,
`omarchy-shell omamood command '<json>'` with any command from `BRIDGE.md`,
and verbs `toggle on off brighter dimmer white color wallpaper accent blink
reload discover panel installHook hookStatus ping`.

Exit codes: 0 ok, 1 failed, 2 usage, 3 no lamps configured, 4 environment
(missing tool, unsupported protocol).

## The facts that shape everything

1. **Nobody owns more than their own lamps.** A profile is only "tested" with a
   pin: `tests/pins/<profile>.json`, the frozen statuses and write plans from its
   owner's device. **A new device may not change what an existing one is sent**:
   add a profile file and a pin; do not edit another owner's profile or pin. Where
   behaviour must be shared, put it behind a profile field (`write.modeLast`, a new
   `format`) whose default keeps today's behaviour.
2. **Ship only what a device was seen to do.** No DPs copied from a vendor table
   your lamp never answered. `omamood probe --json` output is the evidence.
3. **No dependencies Omarchy does not ship.** No pip, no venv, no install commands
   in code or docs. That is why `lib/omamood/aes.py` exists. Tools we may call:
   `python3` (3.11+), `magick`, `qrencode`, `gdbus`, `ip`, `hyprctl`, `omarchy-*`.
4. **Never write into the installed plugin directory while the shell runs it**:
   every file written there reloads the plugin, and Python must not leave
   bytecode (`sys.dont_write_bytecode` is set). Work in a checkout; device keys live
   in `~/.config/omamood/devices.json` (0600), caches in `~/.cache/omamood/`.
5. **Secrets.** Local keys and cloud tokens never go to stdout, logs, pins,
   commits or chat. `omamood devices` hides keys unless `--show-keys`.

## One command

```bash
tools/check
```

Python syntax and unit tests (stdlib only, against a fake Tuya device), every
profile valid, every pin replayed, Model.js under Deno, the no-install-words
grep, manifest defaults vs schema vs Service.qml, `qmllint`, and
`omarchy plugin validate` where the shell exists. CI runs the same script.

## Trying a change live

```bash
rsync -a --delete --exclude .git --exclude __pycache__ ./ ~/.config/omarchy/plugins/io.github.defkode.omamood/
omarchy restart shell        # needed: the service is keepLoaded, hot reload keeps old code
journalctl -t omarchy-shell --since -1min -o cat | grep -i omamood
omarchy-shell omamood status | python3 -m json.tool
```

Before testing a theme change, note `omarchy theme current` and the wallpaper
(`readlink ~/.local/state/omarchy/current/background`) and restore both after.

## Where things are

| | |
|:--|:--|
| `lib/omamood/tuya.py` | LAN protocol 3.3: frames, AES-ECB payloads, query/control/heartbeat, broadcast decoding |
| `lib/omamood/aes.py` | AES-128 ECB + GCM in pure Python (FIPS-197 / NIST vectors in tests) |
| `lib/omamood/profiles.py` | loads `devices/*.json`, picks a profile, DPs ⇄ state, write plans |
| `lib/omamood/lamps.py` | one lamp: connect, commands → writes, blink, restore |
| `lib/omamood/bridge.py` | the shell's helper: worker thread per lamp, QR setup, suspend handling |
| `lib/omamood/cloud.py` | Smart Life QR login and device list (keys), then logout |
| `lib/omamood/discovery.py` | find IPs: UDP broadcasts, else TCP 6668 sweep + key match |
| `lib/omamood/wallpaper.py` | wallpaper/accent → lamp colour (ImageMagick, cached) |
| `lib/omamood/cli.py` | the `omamood` command |
| `Service.qml` / `Panel.qml` / `Model.js` | shell side; Model.js is tested in `tests/model.test.js` |
| `devices/` | profiles; `tests/pins/` their evidence |
| `BRIDGE.md` | the bridge's stdin/stdout contract |
| `docs/ADDING_DEVICES.md` | adding a lamp, a DP layout, or a protocol version |
| `hooks/omamood` | theme-set hook (installed from the panel or `omarchy-shell omamood installHook`) |

For a guided device addition use the skill in `.agents/skills/add-device/SKILL.md`
(Claude Code: `/add-device`).
