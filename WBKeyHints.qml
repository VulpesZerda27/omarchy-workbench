import QtQuick
import qs.Commons
// Footer hints in the companion's style: the key in the text colour, its label muted.
Row {
    id: hints
    property var model: []
    spacing: Style.space(14)
    Repeater {
        model: hints.model
        delegate: Row {
            required property var modelData
            spacing: Style.space(5)
            Text {
                textFormat: Text.PlainText; text: modelData[0]
                color: Color.foreground; font.family: Style.font.family; font.pixelSize: Style.font.caption; font.bold: true
            }
            Text {
                textFormat: Text.PlainText; text: modelData[1]
                color: Color.muted; font.family: Style.font.family; font.pixelSize: Style.font.caption
            }
        }
    }
}
