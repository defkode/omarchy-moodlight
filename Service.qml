import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Hyprland
import Quickshell.Wayland
import qs.Commons
import "Model.js" as Model

// Everything OmaMood does exactly once per session. The bar widget (Panel.qml)
// exists once per monitor and only draws what this says; it finds this item
// with shell.serviceFor("io.github.defkode.omamood").
//
//   the bridge     `omamood bridge`, one Python process holding a connection
//                  to every lamp (BRIDGE.md). JSON commands in, state out.
//   the IPC        target "omamood": what keybindings, the theme hook, scripts
//                  and AI agents call (`omarchy-shell omamood toggle`).
//   theme follow   the theme-set hook calls themeChanged(); we ask the bridge
//                  for the wallpaper's or accent's colour.
//   alert blink    Hyprland marks a background terminal urgent when it rings
//                  its bell; matching terminals make the lamp flash.
//   idle           IdleMonitor; the bridge dims or switches off, then restores.
Item {
  id: root

  property var shell: null
  property var manifest: null
  property string omarchyPath: ""

  readonly property string pluginId: "io.github.defkode.omamood"
  readonly property string cliPath: Qt.resolvedUrl("omamood").toString().replace(/^file:\/\//, "")
  readonly property string hookSource: Qt.resolvedUrl("hooks/omamood").toString().replace(/^file:\/\//, "")
  readonly property string hookTarget: Quickshell.env("HOME") + "/.config/omarchy/hooks/theme-set.d/omamood"

  // ---- Settings, pushed in by Panel.qml with Binding. These defaults must
  //      match manifest.json's barWidget.defaults and Panel.qml's fallbacks.
  property string colorSource: "wallpaper"
  property int saturationFloor: 70
  property bool alertBlink: true
  property string alertClasses: Model.DEFAULT_ALERT_CLASSES
  property string alertColor: ""
  property string idleAction: "none"
  property int idleSeconds: 300
  property string sleepAction: "off"

  // ---- State from the bridge.
  property var lamps: []
  property var setup: ({ stage: "idle" })
  property bool bridgeUp: false
  property string bridgeError: ""
  property string lastError: ""
  property int restartDelay: 0
  property bool hookInstalled: false

  // Terminal alerts: when each window opened (Ghostty asks for attention on open).
  property var openedAt: ({})
  property real lastAlertAt: 0
  property string pendingUrgent: ""
  // The last urgent window considered, for `status` (debugging, agents).
  property var lastUrgent: null

  function send(obj) {
    if (!bridge.running) return false
    bridge.write(JSON.stringify(obj) + "\n")
    return true
  }

  function pushSettings() {
    send(Model.bridgeSettings({ saturationFloor: root.saturationFloor, idleAction: root.idleAction,
      sleepAction: root.sleepAction }))
  }

  onSaturationFloorChanged: pushSettings()
  onIdleActionChanged: pushSettings()
  onSleepActionChanged: pushSettings()

  function applyLine(line) {
    var obj = Model.parseLine(line)
    if (!obj) return
    if (obj.type === "state") {
      root.lamps = obj.lamps || []
      root.setup = obj.setup || { stage: "idle" }
      root.bridgeUp = true
      root.bridgeError = ""
      root.restartDelay = 0
    } else if (obj.type === "result" && !obj.ok) {
      root.lastError = String(obj.error || "")
    }
  }

  function themeChanged() {
    var cmd = Model.themeCommand(root.colorSource)
    if (cmd) send(cmd)
  }

  function blink() {
    var colour = root.alertColor !== "" ? root.alertColor : String(Color.urgent)
    send({ cmd: "blink", color: colour, count: 3 })
  }

  function installHook() {
    hookInstall.running = true
  }

  Process {
    id: bridge
    command: [root.cliPath, "bridge"]
    running: true
    stdinEnabled: true
    stdout: SplitParser { onRead: function(line) { root.applyLine(line) } }
    stderr: SplitParser { onRead: function(line) { root.lastError = String(line).replace(/^omamood: /, "") } }
    onStarted: root.pushSettings()
    onExited: function(exitCode, exitStatus) {
      root.bridgeUp = false
      if (exitCode === 4) {
        // Setup failure (e.g. Python too old): retrying will not help.
        root.bridgeError = root.lastError || "omamood bridge cannot start"
        return
      }
      root.restartDelay = Model.backoff(root.restartDelay)
      restartTimer.restart()
    }
  }

  Timer {
    id: restartTimer
    interval: root.restartDelay
    onTriggered: bridge.running = true
  }

  // ---- Theme hook presence and installation.
  Process {
    id: hookCheck
    command: ["test", "-f", root.hookTarget]
    onExited: function(exitCode) { root.hookInstalled = exitCode === 0 }
  }

  Process {
    id: hookInstall
    command: ["omarchy-hook-install", "theme-set", root.hookSource]
    onExited: hookCheck.running = true
  }

  Component.onCompleted: hookCheck.running = true

  // ---- Terminal alerts.
  Connections {
    target: Hyprland
    function onRawEvent(event) {
      if (!event || !event.name) return
      var name = String(event.name)
      if (name === "openwindow") {
        var opened = root.openedAt
        opened[Model.eventAddress(event.data)] = Date.now()
        root.openedAt = opened
      } else if (name === "closewindow") {
        var closed = root.openedAt
        delete closed[Model.eventAddress(event.data)]
        root.openedAt = closed
      } else if (name === "urgent" && root.alertBlink && root.lamps.length > 0 && !clients.running) {
        root.pendingUrgent = Model.eventAddress(event.data)
        clients.running = true
      }
    }
  }

  Process {
    id: clients
    command: ["hyprctl", "clients", "-j"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var cls = Model.classOf(text, root.pendingUrgent)
        var now = Date.now()
        var blinked = Model.shouldAlert({ enabled: root.alertBlink, cls: cls, classes: root.alertClasses,
            openedAt: root.openedAt[root.pendingUrgent] || 0, now: now, lastAlertAt: root.lastAlertAt })
        root.lastUrgent = { at: new Date(now).toISOString(), address: root.pendingUrgent, class: cls, blinked: blinked,
          openedMsAgo: root.openedAt[root.pendingUrgent] ? now - root.openedAt[root.pendingUrgent] : -1,
          clientsBytes: String(text || "").length }
        if (blinked) {
          root.lastAlertAt = now
          root.blink()
        }
      }
    }
  }

  // ---- Away.
  IdleMonitor {
    enabled: root.idleAction !== "none" && root.lamps.length > 0
    timeout: Math.max(30, root.idleSeconds)
    respectInhibitors: true
    onIsIdleChanged: root.send({ cmd: "idle", value: isIdle })
  }

  // ---- IPC: `omarchy-shell omamood <method> [args]`. Lamp verbs act on every
  //      lamp; `command` takes any bridge command as JSON (BRIDGE.md).
  IpcHandler {
    target: "omamood"

    function ping(): string { return "pong" }
    function status(): string {
      return JSON.stringify({ lamps: root.lamps, setup: root.setup, bridgeUp: root.bridgeUp,
        bridgeError: root.bridgeError, lastError: root.lastError, hookInstalled: root.hookInstalled,
        lastUrgent: root.lastUrgent,
        settings: { colorSource: root.colorSource, saturationFloor: root.saturationFloor,
          alertBlink: root.alertBlink, idleAction: root.idleAction, sleepAction: root.sleepAction } })
    }
    function command(json: string): string {
      var parsed = Model.parseCommand(json)
      if (!parsed.ok) return "error: " + parsed.error
      return root.send(parsed.cmd) ? "ok" : "error: bridge not running"
    }
    function toggle(): void { root.send({ cmd: "power", value: "toggle" }) }
    function on(): void { root.send({ cmd: "power", value: "on" }) }
    function off(): void { root.send({ cmd: "power", value: "off" }) }
    function brighter(): void { root.send({ cmd: "brightness", value: "+10" }) }
    function dimmer(): void { root.send({ cmd: "brightness", value: "-10" }) }
    function white(): void { root.send({ cmd: "white" }) }
    function color(hex: string): void { root.send({ cmd: "color", value: hex }) }
    function wallpaper(): void { root.send({ cmd: "wallpaper" }) }
    function accent(): void { root.send({ cmd: "accent" }) }
    function blink(): void { root.blink() }
    function themeChanged(theme: string): void { root.themeChanged() }
    function reload(): void { root.send({ cmd: "reload" }) }
    function discover(): void { root.send({ cmd: "discover" }) }
    function panel(): void { if (root.shell) root.shell.toggle(root.pluginId) }
    function installHook(): void { root.installHook() }
    function hookStatus(): string { return root.hookInstalled ? "installed" : "missing" }
  }
}
