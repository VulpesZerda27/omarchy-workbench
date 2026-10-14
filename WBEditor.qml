import QtQuick
import QtQuick.Controls as QQC
import qs.Commons
import qs.Ui as Ui
QQC.TextArea {
    id: editor
    // Emitted before the editor's own paste, which still runs.
    signal pasted()
    color: Color.foreground
    font.family: Style.font.family
    font.pixelSize: Style.font.body + 1
    wrapMode: TextEdit.Wrap
    selectByMouse: true
    activeFocusOnTab: true
    Keys.priority: Keys.BeforeItem
    Keys.onPressed: function(event) {
        if (event.matches(StandardKey.Paste)) editor.pasted()
        if ((event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) && !(event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))) {
            nextItemInFocusChain(event.key !== Qt.Key_Backtab && !(event.modifiers & Qt.ShiftModifier)).forceActiveFocus(Qt.TabFocusReason)
            event.accepted = true
        }
    }
    // Qt's placeholder elides instead of wrapping; long hints should wrap like the text does.
    Component.onCompleted: {
        for (var i = 0; i < children.length; i++)
            if (String(children[i]).indexOf("PlaceholderText") >= 0) { children[i].wrapMode = Text.Wrap; children[i].elide = Text.ElideNone }
    }
    persistentSelection: true
    selectionColor: Style.selectionFillFor(Color.foreground, Color.accent)
    selectedTextColor: Color.foreground
    placeholderTextColor: Color.muted
    padding: Style.space(16)
    // The same focus > hover > normal chrome as Omarchy's text fields.
    background: Ui.BorderSurface {
        color: Style.controlFill(editor.activeFocus, editor.hovered, Color.foreground, Color.accent)
        borderSpec: Border.controlSpec(editor.activeFocus ? "focus" : (editor.hovered ? "hover-cursor" : "normal"), Color.foreground, Color.accent)
        radius: Style.cornerRadius
    }
}
