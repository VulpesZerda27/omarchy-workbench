import QtQuick
import qs.Commons
import qs.Ui
BarIconButton {
    property var shell: null
    property var manifest: null
    text: "󰆧"
    foreground: Color.accent
    onPressed: if (shell) shell.summon("io.github.vulpeszerda27.workbench", '{"view":"shelf"}')
}
