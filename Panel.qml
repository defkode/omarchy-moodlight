import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

// The bar widget: a bulb tinted with the lamp's colour, and a panel with every
// lamp's power, brightness, white/colour and theme swatches, the options, and
// QR setup. One instance per monitor; all state lives in Service.qml.
Panel {
  id: root
  moduleName: "io.github.defkode.omamood"
  // The service owns the "omamood" IPC target; the shell's own
  // `shell toggle <id>` opens and closes this panel.
  manageIpc: false
  // The bar sizes a widget from its implicit size.
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  readonly property var service: bar && bar.shell ? bar.shell.serviceFor("io.github.defkode.omamood") : null
  readonly property var lamps: service ? service.lamps : []
  readonly property var setupState: service ? service.setup : ({ stage: "idle" })
  readonly property var discoveryState: service ? service.discovery : ({ stage: "idle" })
  readonly property bool searching: discoveryState.stage === "searching"
  // Manual colour picks only make sense when theme changes leave the lamp alone.
  readonly property string colorSource: String(setting("colorSource", "wallpaper"))
  readonly property bool manualColour: colorSource === "off"
  readonly property bool settingUp: ["requesting", "scan", "fetching", "locating"].indexOf(setupState.stage) >= 0
  readonly property bool showSetup: lamps.length === 0 || setupOpen || settingUp || setupState.stage === "error"
  property bool setupOpen: false
  // Removing the last lamp hides the footer with "Done": leave manage mode then.
  onLampsChanged: if (lamps.length === 0) setupOpen = false
  property var palette: ({})
  readonly property color fg: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(fg, 1.5)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  function cmd(obj) { if (root.service) root.service.send(obj) }

  function setSetting(key, value) {
    var s = Object.assign({}, root.settings || {})
    s[key] = value
    if (root.bar && root.bar.shell) root.bar.shell.updateEntryInline(root.moduleName, s)
  }

  // ---- Settings into the service. Fallbacks = manifest.json defaults.
  Binding { target: root.service; when: root.service !== null; property: "colorSource"; value: String(root.setting("colorSource", "wallpaper")) }
  Binding { target: root.service; when: root.service !== null; property: "saturationFloor"; value: Number(root.setting("saturationFloor", 70)) }
  Binding { target: root.service; when: root.service !== null; property: "sleepAction"; value: String(root.setting("sleepAction", "off")) }

  // ---- Theme palette for the swatches; re-read when the theme changes.
  FileView {
    id: colorsFile
    path: Quickshell.env("HOME") + "/.local/state/omarchy/current/theme/colors.toml"
    onLoaded: root.palette = Model.parseColorsToml(text())
  }
  Connections {
    target: Color
    function onAccentChanged() { colorsFile.reload() }
  }

  // "Match lamp to": one setting for every lamp. With one lamp it sits inside
  // its card, between the brightness and the colour picker it governs; with
  // several, once above the list.
  Component {
    id: followSection
    Column {
    width: parent ? parent.width : 0
    spacing: Style.space(8)

    PanelSectionHeader { text: "Match lamp to"; foreground: root.dim }

    ButtonGroup {
      width: parent.width
      options: [{ value: "wallpaper", label: "Wallpaper", tooltip: "Main colour of the wallpaper, on every theme change" },
        { value: "accent", label: "Theme accent", tooltip: "The theme's accent colour, on every theme change" },
        { value: "off", label: "Custom", tooltip: "Pick the colour yourself; theme changes leave the lamp alone" }]
      value: root.colorSource
      foreground: root.fg
      fontFamily: root.fontFamily
      onChanged: function(v) { root.setSetting("colorSource", v) }
    }

    Item {
      width: parent.width
      visible: root.service && !root.service.hookInstalled && root.setting("colorSource", "wallpaper") !== "off"
      implicitHeight: Math.max(hookText.implicitHeight, hookButton.implicitHeight)

      Text {
        id: hookText
        anchors.left: parent.left
        anchors.right: hookButton.left
        anchors.rightMargin: Style.space(8)
        anchors.verticalCenter: parent.verticalCenter
        text: "Theme hook not installed: the lamp can't follow theme changes yet."
        wrapMode: Text.Wrap
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }
      Button {
        id: hookButton
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        text: "Install"
        bordered: true
        foreground: root.fg
        fontFamily: root.fontFamily
        onClicked: root.service.installHook()
      }
    }
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: Model.glyph(root.lamps)
    foreground: {
      var t = root.setting("tintIcon", true) !== false ? Model.tint(root.lamps) : null
      return t ? t : (root.bar ? root.bar.barForeground : Color.foreground)
    }
    dimmed: root.lamps.length > 0 && Model.onlineCount(root.lamps) === 0
    tooltipText: root.opened ? "" : Model.tooltip(root.lamps)
    onPressed: function(b) {
      if (b === Qt.RightButton) root.cmd({ cmd: "power", value: "toggle" })
      else root.toggle()
    }
    onWheelMoved: function(delta) { root.cmd({ cmd: "brightness", value: delta > 0 ? "+10" : "-10" }) }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(360))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onActivateRequested: root.cmd({ cmd: "power", value: "toggle" })
      onMoveRequested: function(dx, dy) {
        if (dx !== 0) root.cmd({ cmd: "brightness", value: dx > 0 ? "+10" : "-10" })
      }

      Column {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Style.space(14)

        // ---------- Hero
        Item {
          width: parent.width
          implicitHeight: Math.max(heroIcon.implicitHeight, heroText.implicitHeight)

          Text {
            id: heroIcon
            text: Model.glyph(root.lamps)
            color: Model.tint(root.lamps) || root.fg
            font.family: root.fontFamily
            font.pixelSize: Style.font.display
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
          }

          Column {
            id: heroText
            anchors.left: heroIcon.right
            anchors.leftMargin: Style.space(14)
            anchors.right: gearButton.left
            anchors.rightMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(2)

            Text {
              text: "OmaMood"
              color: root.fg
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
            }
            Text {
              textFormat: Text.PlainText
              width: parent.width
              text: root.service && root.service.bridgeError !== "" ? root.service.bridgeError
                : Model.summary(root.lamps, root.setupState).toUpperCase()
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1
              elide: Text.ElideRight
            }
          }

          // Lamp management (add, remove, find) lives behind this icon so the
          // everyday panel stays minimal.
          Button {
            id: gearButton
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            visible: root.lamps.length > 0
            iconText: Model.GLYPH_SETTINGS
            tooltipText: root.setupOpen ? "Done" : "Manage lamps"
            selected: root.setupOpen
            foreground: root.fg
            fontFamily: root.fontFamily
            onClicked: root.setupOpen = !root.setupOpen
          }
        }

        Loader {
          width: parent.width
          active: root.lamps.length > 1 && !root.showSetup
          visible: active
          sourceComponent: followSection
        }

        // ---------- Lamps
        Repeater {
          model: root.lamps

          Column {
            id: lampCard
            required property var modelData
            // In manage mode a lamp shows only its name and a Remove button.
            readonly property bool usable: modelData.online && modelData.on && !root.setupOpen
            property bool confirming: false
            width: column.width
            spacing: Style.space(8)

            Item {
              width: parent.width
              implicitHeight: Math.max(lampName.implicitHeight + lampLine.implicitHeight, power.implicitHeight)
              readonly property Item side: root.setupOpen ? removeButton : power

              Text {
                id: lampName
                textFormat: Text.PlainText
                anchors.left: parent.left
                anchors.right: parent.side.left
                anchors.top: parent.top
                text: lampCard.modelData.name
                color: root.fg
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                font.bold: true
                elide: Text.ElideRight
              }
              Text {
                id: lampLine
                textFormat: Text.PlainText
                anchors.left: parent.left
                anchors.right: parent.side.left
                anchors.top: lampName.bottom
                text: Model.lampLine(lampCard.modelData)
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
              }
              ToggleSwitch {
                id: power
                visible: !root.setupOpen
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                checked: lampCard.modelData.on === true
                interactive: lampCard.modelData.online
                foreground: root.fg
                onToggled: root.cmd({ cmd: "power", lamp: lampCard.modelData.id, value: checked ? "off" : "on" })
              }
              // Two clicks: "Remove", then "Confirm?" within 3 s. Forgets the lamp
              // and its key; adding it back takes a QR scan.
              Button {
                id: removeButton
                visible: root.setupOpen
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: lampCard.confirming ? "Confirm?" : "Remove"
                bordered: true
                foreground: lampCard.confirming ? Color.urgent : root.fg
                fontFamily: root.fontFamily
                fontSize: Style.font.caption
                onClicked: {
                  if (!lampCard.confirming) { lampCard.confirming = true; confirmTimer.restart(); return }
                  lampCard.confirming = false
                  root.cmd({ cmd: "remove", lamp: lampCard.modelData.id })
                }
              }
              Timer {
                id: confirmTimer
                interval: 3000
                onTriggered: lampCard.confirming = false
              }
            }

            PanelSectionHeader {
              visible: lampCard.usable
              text: "Brightness"
              foreground: root.dim
            }

            PanelSlider {
              width: parent.width
              visible: lampCard.usable
              bar: root.bar
              minimum: 1
              maximum: 100
              step: 1
              integer: true
              value: lampCard.modelData.brightness || 1
              onReleased: function(v) { root.cmd({ cmd: "brightness", lamp: lampCard.modelData.id, value: Math.round(v) }) }
            }

            Loader {
              width: parent.width
              active: root.lamps.length === 1 && !root.showSetup
              visible: active
              sourceComponent: followSection
            }

            // Custom colour (swatches) only when theme changes leave the lamp
            // alone: otherwise the next switch would undo it.
            Row {
              visible: lampCard.usable && root.manualColour && (lampCard.modelData.capabilities || []).indexOf("colour") >= 0
              spacing: Style.space(8)
              // Same breathing room as between the panel's other sections.
              topPadding: Style.space(6)

              Repeater {
                model: Model.swatches(root.palette)

                Rectangle {
                  required property var modelData
                  width: Style.space(22)
                  height: width
                  radius: width / 2
                  color: modelData.color
                  border.width: 1
                  border.color: Qt.darker(root.fg, 2)

                  MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.cmd(parent.modelData.white
                      ? { cmd: "white", lamp: lampCard.modelData.id }
                      : { cmd: "color", lamp: lampCard.modelData.id, value: parent.modelData.color })
                  }
                }
              }

              Button {
                text: "Wallpaper"
                foreground: root.fg
                fontFamily: root.fontFamily
                fontSize: Style.font.caption
                onClicked: root.cmd({ cmd: "wallpaper", lamp: lampCard.modelData.id, force: true })
              }
            }
          }
        }

        // ---------- Setup
        Column {
          width: parent.width
          visible: root.showSetup
          spacing: Style.space(10)

          PanelSectionHeader { text: "Add lamps"; foreground: root.dim }

          Text {
            width: parent.width
            visible: root.setupState.stage === "idle" || root.setupState.stage === "error" || root.setupState.stage === "done"
            text: "In the Smart Life app: Me → Settings → Account and Security → User Code. Enter it, then scan the QR code with the app (+ → Scan) and tap Confirm login. Keys stay on this computer."
            wrapMode: Text.Wrap
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          Item {
            width: parent.width
            visible: !root.settingUp
            implicitHeight: Math.max(userCode.implicitHeight, qrButton.implicitHeight)

            TextField {
              id: userCode
              anchors.left: parent.left
              anchors.right: qrButton.left
              anchors.rightMargin: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              placeholderText: "User Code"
              font.family: root.fontFamily
              onAccepted: qrButton.clicked()
            }
            Button {
              id: qrButton
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: "Show QR"
              bordered: true
              foreground: root.fg
              fontFamily: root.fontFamily
              onClicked: if (userCode.text.trim() !== "") root.cmd({ cmd: "setup.qr", userCode: userCode.text.trim() })
            }
          }

          Rectangle {
            id: qrCanvas
            readonly property var rows: root.setupState.qr || []
            readonly property int size: rows.length
            readonly property int moduleSize: size > 0 ? Math.max(3, Math.floor(Style.space(220) / size)) : 0
            visible: root.setupState.stage === "scan" && size > 0
            anchors.horizontalCenter: parent.horizontalCenter
            width: size * moduleSize
            height: width
            color: "white"
            radius: Style.cornerRadius

            Grid {
              anchors.fill: parent
              columns: qrCanvas.size

              Repeater {
                model: qrCanvas.size * qrCanvas.size

                Rectangle {
                  required property int index
                  width: qrCanvas.moduleSize
                  height: qrCanvas.moduleSize
                  color: qrCanvas.rows[Math.floor(index / qrCanvas.size)].charAt(index % qrCanvas.size) === "1" ? "#111111" : "transparent"
                }
              }
            }
          }

          Text {
            width: parent.width
            visible: root.setupState.stage !== "idle"
            text: Model.stageLabel(root.setupState)
            wrapMode: Text.Wrap
            color: root.setupState.stage === "error" ? Color.urgent : root.fg
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
          }

          Button {
            visible: root.settingUp || root.setupState.stage === "error"
            text: root.settingUp ? "Cancel" : "Dismiss"
            foreground: root.fg
            fontFamily: root.fontFamily
            onClicked: root.cmd({ cmd: "setup.cancel" })
          }
        }

        // ---------- Footer
        Row {
          spacing: Style.space(8)
          visible: root.lamps.length > 0 && root.setupOpen

          Button {
            text: root.searching ? "Searching…" : "Find lamps"
            foreground: root.searching ? root.dim : root.fg
            fontFamily: root.fontFamily
            fontSize: Style.font.caption
            onClicked: if (!root.searching) root.cmd({ cmd: "discover" })
          }
        }

        // "Find lamps" progress and result (the bridge clears it after ~15 s).
        Text {
          width: parent.width
          visible: text !== "" && root.setupOpen
          textFormat: Text.PlainText
          text: Model.discoveryLabel(root.discoveryState)
          wrapMode: Text.Wrap
          color: (root.discoveryState.missing || []).length || root.discoveryState.error ? Color.urgent : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }
      }
    }
  }
}
