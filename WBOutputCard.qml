import QtQuick
import qs.Commons
import qs.Ui as Ui
// One output on the shelf. The selected card carries the accent border, like
// the selected slice in Omarchy's image picker; the keyboard cursor thickens it.
Ui.BorderSurface {
    id: card
    property var host
    property var item: ({})
    property bool selected: false
    property bool cursor: false
    readonly property bool missing: item.exists === false
    readonly property bool hot: mouse.containsMouse
    signal activated()
    signal opened()
    radius: Style.cornerRadius
    color: selected ? Util.alpha(Color.accent, 0.08) : hot ? Style.hoverFill : Style.normalFill
    borderSpec: Border.flat(selected || cursor ? Color.accent : hot ? Style.hoverBorderColor : host.line, cursor ? 2 : 1)
    Behavior on color { ColorAnimation { duration: 120 } }

    Column {
        anchors.fill: parent; anchors.margins: Style.space(10); spacing: Style.space(8)
        Rectangle {
            width: parent.width; height: card.host.thumbHeight
            radius: Math.max(0, Style.cornerRadius - 2); clip: true
            color: Util.alpha(Color.foreground, 0.04)
            Image {
                anchors.fill: parent; visible: !!card.item.thumbnail
                source: card.item.thumbnail || ""; asynchronous: true
                fillMode: Image.PreserveAspectCrop; verticalAlignment: Image.AlignTop
                sourceSize.width: 480; opacity: card.missing ? 0.35 : 1
            }
            Text {
                anchors.centerIn: parent; visible: !card.item.thumbnail
                textFormat: Text.PlainText; text: card.host.kindGlyph(card.item)
                color: card.selected ? Color.accent : Color.muted; opacity: card.missing ? 0.5 : 1
                font.family: Style.font.family; font.pixelSize: Style.font.display
            }
            Ui.BorderSurface {
                visible: card.missing
                anchors.left: parent.left; anchors.top: parent.top; anchors.margins: Style.space(6)
                implicitWidth: missingLabel.implicitWidth + Style.space(12); implicitHeight: missingLabel.implicitHeight + Style.space(4)
                radius: Style.cornerRadius
                color: Util.alpha(Color.urgent, 0.16); borderSpec: Border.flat(Util.alpha(Color.urgent, 0.5), 1)
                Text {
                    id: missingLabel; anchors.centerIn: parent
                    textFormat: Text.PlainText; text: "Missing"
                    color: Color.urgent; font.family: Style.font.family; font.pixelSize: Style.font.caption
                }
            }
        }
        WBText {
            width: parent.width; height: card.host.cardTitleHeight
            text: card.item.title || ""; font.bold: true
            wrapMode: Text.Wrap; maximumLineCount: 2; verticalAlignment: Text.AlignTop
            color: card.missing ? Color.muted : Color.foreground
        }
        WBText {
            width: parent.width
            text: [card.item.kind, card.item.project, card.host.shortDate(card.item.updated)].filter(function(p) { return !!p }).join(" · ")
            color: Color.muted; font.pixelSize: Style.font.caption
        }
    }
    MouseArea {
        id: mouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
        onClicked: card.activated()
        onDoubleClicked: card.opened()
    }
}
