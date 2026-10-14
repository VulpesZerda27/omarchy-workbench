import qs.Commons
import qs.Ui as Ui
// Omarchy's bordered button. `primary` carries the accent for the one action a
// view leads to; `destructive` turns urgent only while hovered or focused.
Ui.Button {
    property bool primary: false
    property bool destructive: false
    focusable: true; bordered: true; opacity: enabled ? 1 : 0.45
    foreground: destructive && (hot || activeFocus) ? Color.urgent : primary && enabled ? Color.accent : Color.foreground
    background: primary && enabled ? Util.alpha(Color.accent, 0.10) : "transparent"
}
