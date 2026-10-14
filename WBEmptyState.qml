import QtQuick
import qs.Commons
// The Omarchy menu's empty state: a large accent glyph over a short explanation.
Column {
    id: empty
    property string glyph: "󰆧"
    property color glyphColor: Color.accent
    property string title: ""
    property string body: ""
    spacing: Style.space(8)
    Text {
        width: parent.width; horizontalAlignment: Text.AlignHCenter
        textFormat: Text.PlainText; text: empty.glyph
        color: empty.glyphColor; opacity: 0.8
        font.family: Style.font.family; font.pixelSize: Style.font.displayLarge
    }
    Text {
        width: parent.width; horizontalAlignment: Text.AlignHCenter; visible: text !== ""
        textFormat: Text.PlainText; text: empty.title; wrapMode: Text.Wrap
        color: Color.foreground; opacity: 0.85
        font.family: Style.font.family; font.pixelSize: Style.font.title
    }
    Text {
        width: parent.width; horizontalAlignment: Text.AlignHCenter; visible: text !== ""
        textFormat: Text.PlainText; text: empty.body; wrapMode: Text.Wrap
        color: Color.muted
        font.family: Style.font.family; font.pixelSize: Style.font.bodySmall
    }
}
