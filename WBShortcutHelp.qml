import QtQuick
import QtQuick.Window
import QtQuick.Layouts
import QtQuick.Controls as QQC
import qs.Commons
import qs.Ui as Ui
QQC.Popup {
    id: help
    property var host
    property var returnFocus: null
    readonly property string shot: host ? host.screenshotKey : ""
    // Rows with an empty key (no screenshot key bound) are left out.
    readonly property var sections: [
        {title: "Everywhere", rows: [
            ["Tab · Shift+Tab", "Move between controls"],
            ["Arrows · Enter · Space", "Navigate lists · open or activate"],
            ["Ctrl+1 · 2 · 3", "Shelf · Capture · Issues"],
            ["Ctrl+Tab", "Next section"],
            ["Esc", "Back out of a menu, then close Workbench"]]},
        {title: "Shelf", rows: [
            ["Ctrl+F", "Search the shelf"],
            ["↓", "Move from search into the outputs"],
            ["Enter", "Open the selected output"],
            ["Ctrl+N", "Add an output"],
            ["Ctrl+O", "Browse for a file"]]},
        {title: "Capture", rows: [
            ["Ctrl+N", "Start a new capture"],
            ["Ctrl+T", "Send to an existing thread or start a new one"],
            ["Ctrl+Enter", "Send, queue, or start the thread"],
            [help.shot, "Add a screenshot while Capture is open"],
            ["Ctrl+V", "Paste text, or add a copied image"],
            ["Ctrl+O", "Attach a file"],
            ["Super+Ctrl+X", "Dictate into the note"]]},
        {title: "Issues", rows: [
            ["Ctrl+F · ↓", "Search, then move into the issues"],
            ["Enter", "Write instructions for the selected issue"],
            ["Ctrl+N", "Describe a new issue"],
            ["Ctrl+Enter", "Hand it to Claude or Codex"],
            ["Ctrl+R", "Refresh from GitHub"],
            [help.shot ? help.shot + " · Ctrl+O" : "Ctrl+O", help.shot ? "Add a screenshot or file" : "Add a file"]]},
        {title: "File picker", rows: [
            ["Ctrl+L", "Edit the path"],
            ["Alt+↑ · Backspace", "Open the parent folder"],
            ["Ctrl+H", "Show or hide dotfiles"],
            ["Ctrl+S · Ctrl+R", "Sort by the next column · reverse"],
            ["Home · End", "First or last entry"]]}
    ]
    function show() { returnFocus = parent.Window.window ? parent.Window.window.activeFocusItem : null; open() }
    width: Math.min(Style.space(940), parent.width - Style.space(64)); height: content.implicitHeight + topPadding + bottomPadding
    x: (parent.width - width) / 2; y: (parent.height - height) / 2
    modal: true; focus: true; padding: Style.space(24); closePolicy: QQC.Popup.CloseOnEscape
    onClosed: Qt.callLater(function() { if (help.host.opened && help.host.windowActive && help.returnFocus) help.returnFocus.forceActiveFocus() })
    background: Ui.BorderSurface { color: Color.popups.background; radius: Style.cornerRadius; borderSpec: help.host.surfaceBorder }
    QQC.Overlay.modal: Rectangle { color: Color.menu.scrim }
    contentItem: ColumnLayout {
        id: content; spacing: Style.space(20)
        RowLayout {
            Layout.fillWidth: true; spacing: Style.space(12)
            Text { textFormat: Text.PlainText; text: "󰌌"; color: Color.accent; font.family: Style.font.family; font.pixelSize: Style.font.iconLarge + 4 }
            WBText { Layout.fillWidth: true; text: "Keyboard shortcuts"; font.pixelSize: Style.font.heading; font.bold: true }
            WBKeyHints { model: [["F1", "show again"], ["Esc", "return"]] }
        }
        GridLayout {
            Layout.fillWidth: true; columns: 2; columnSpacing: Style.space(36); rowSpacing: Style.space(20)
            Repeater {
                model: help.sections
                delegate: ColumnLayout {
                    required property var modelData
                    Layout.fillWidth: true; Layout.alignment: Qt.AlignTop; Layout.preferredWidth: 1; spacing: Style.space(8)
                    Ui.PanelSectionHeader { text: modelData.title.toUpperCase(); foreground: Color.foreground }
                    Repeater {
                        model: modelData.rows.filter(function(row) { return row[0] !== "" })
                        delegate: RowLayout {
                            required property var modelData
                            Layout.fillWidth: true; spacing: Style.space(14)
                            WBText { Layout.preferredWidth: Style.space(150); text: modelData[0]; color: Color.accent; font.bold: true; font.pixelSize: Style.font.bodySmall; wrapMode: Text.Wrap; elide: Text.ElideNone }
                            WBText { Layout.fillWidth: true; text: modelData[1]; font.pixelSize: Style.font.bodySmall; wrapMode: Text.Wrap; elide: Text.ElideNone }
                        }
                    }
                }
            }
        }
    }
}
