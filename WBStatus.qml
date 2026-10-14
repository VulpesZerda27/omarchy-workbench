import QtQuick
import qs.Commons
// Herdr's colour-dot agent status: the same glyph and terminal colour as its sidebar.
Row {
    id: status
    property var host
    property string state: ""
    property string label: state
    property real fontSize: Style.font.bodySmall
    spacing: Style.space(6)
    Text {
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: status.host.statusGlyph(status.state)
        color: status.host.statusColor(status.state)
        font.family: Style.font.family; font.pixelSize: status.fontSize
    }
    Text {
        visible: text !== ""
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: status.label
        color: Color.muted
        font.family: Style.font.family; font.pixelSize: status.fontSize
    }
}
