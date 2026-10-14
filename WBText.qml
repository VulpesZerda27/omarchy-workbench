import QtQuick
import QtQuick.Layouts
import qs.Commons
Text {
    Layout.minimumWidth: 0
    textFormat: Text.PlainText
    color: Color.foreground
    font.family: Style.font.family
    font.pixelSize: Style.font.body
    elide: Text.ElideRight
}
