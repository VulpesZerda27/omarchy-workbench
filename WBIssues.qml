import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC
import qs.Commons
import qs.Ui as Ui
// Issues: a project's GitHub issues on the left; on the right the selected issue,
// or a new one, with the request handed to Claude or Codex in a fresh Herdr workspace.
RowLayout {
    id: issues
    property var host
    // The project and agent are chosen in the panel's context row.
    property var projects: []
    property bool projectsLoaded: false
    property string dir: ""
    property string agent: "claude"
    property string stateFilter: "open"
    property string query: ""
    property var listing: ({issues: [], repo: "", error: "", fetched: 0})
    property bool loading: false
    // "" for nothing, "new" for a new issue, otherwise the selected issue's number.
    property string selected: ""
    property var detail: ({})
    // The request for the selection, saved as an issue draft once there is something in it.
    property string draftId: ""
    property string text: ""
    property var attachments: []
    property string draftError: ""
    property bool dirty: false
    property bool loadingDraft: false
    property bool handing: false
    // Unsaved typing per issue, so switching back and forth never shows an older save.
    property var local: ({})

    readonly property var project: projects.find(function(p) { return p.value === issues.dir }) || ({})
    readonly property string repo: project.repo || ""
    readonly property bool creating: selected === "new"
    readonly property bool composing: selected !== "" && repo !== ""
    readonly property int number: creating || !selected ? 0 : Number(selected)
    readonly property var current: number ? (listing.issues.find(function(i) { return i.number === issues.number }) || detail) : ({})
    readonly property var visibleIssues: listing.issues.filter(function(i) {
        if (!issues.query) return true
        var q = issues.query.toLowerCase().replace(/^#/, "")
        return (i.number + " " + i.title + " " + i.author + " " + i.labels.map(function(l) { return l.name }).join(" ")).toLowerCase().indexOf(q) !== -1
    })
    // Requests already handed to an agent for this project, newest first.
    readonly property var handoffs: host.state.drafts.filter(function(d) {
        return d.issue && d.issue.dir === issues.dir && d.status !== "draft"
    }).sort(function(a, b) { return (b.queued || b.updated) - (a.queued || a.updated) })
    // New issues an agent is still writing: queued, or its thread is open and recent.
    readonly property var writing: handoffs.filter(function(d) { return !d.issue.number && issues.active(d) })
    readonly property bool hasContent: text.trim() !== "" || attachments.length > 0
    // An issue an agent is already on needs new instructions, so a repeated Ctrl+Enter never starts a duplicate.
    readonly property bool busyIssue: !!number && !!handoffFor(number)
    readonly property bool canHandOver: composing && !handing && (hasContent || (!creating && !busyIssue))

    function key() { return dir + "#" + number }
    function target() { return {dir: dir, number: number, title: number ? (current.title || "") : ""} }
    function active(d) {
        var t = host.findThread(d.thread)
        if (d.status === "queued" || d.status === "sending") return true
        return !!t.live && Date.now() / 1000 - (d.sent || d.queued || 0) < 86400
    }
    // What an agent handed a request is doing, in Herdr's terms.
    function progress(d, working) {
        var t = host.findThread(d.thread)
        if (d.status === "queued" || d.status === "sending") return "starting"
        if (!t.live) return "thread closed"
        return t.status === "working" ? working : t.status === "blocked" ? "needs your approval" : (t.status === "idle" || t.status === "done") ? "waiting for you" : t.status
    }
    function handoffFor(n) { return handoffs.find(function(d) { return d.issue.number === n && issues.active(d) }) }
    function ago(ts) {
        var s = Date.now() / 1000 - ts
        if (!ts) return ""
        if (s < 3600) return Math.max(1, Math.round(s / 60)) + " min ago"
        if (s < 86400) return Math.round(s / 3600) + " h ago"
        if (s < 172800) return "yesterday"
        if (s < 604800) return Math.round(s / 86400) + " days ago"
        return host.shortDate(ts)
    }
    function stateGlyph(i) { return !i || i.state !== "closed" ? "\uf41b" : i.reason === "not_planned" ? "\uf52e" : "\uf41d" }
    function stateColor(i) { return !i || i.state !== "closed" ? host.green : i.reason === "not_planned" ? Color.muted : (host.tones.magenta || Color.accent) }
    function stateLabel(i) { return !i || i.state !== "closed" ? "Open" : i.reason === "not_planned" ? "Closed as not planned" : "Closed" }

    // Entering the view: projects first, then the chosen project's issues.
    function start() {
        if (!projects.length) loadProjects()
        else if (dir) load(false)
    }
    function loadProjects() {
        host.invoke({op: "issue-projects"}, function(rows) {
            issues.projects = rows; issues.projectsLoaded = true
            if (!rows.find(function(p) { return p.value === issues.dir })) issues.chooseProject(issues.defaultProject())
            else issues.load(false)
        })
    }
    // The thread you came from decides when its project is on GitHub, then the last project used,
    // then the most recently active one on GitHub.
    function defaultProject() {
        var cwd = String(host.findThread(host.threadKey).cwd || ""), saved = (host.state.settings || {}).issue_project
        var match = projects.filter(function(p) { return cwd === p.value || cwd.indexOf(p.value + "/") === 0 })[0]
        if (match && match.repo) return match.value
        if (projects.find(function(p) { return p.value === saved })) return saved
        var linked = projects.find(function(p) { return !!p.repo })
        return linked ? linked.value : projects.length ? projects[0].value : ""
    }
    // Only a project picked by hand is remembered as the last one used.
    function chooseProject(value, picked) {
        if (picked && value) host.invoke({op: "settings", value: {issue_project: value}})
        if (!value || value === dir) return
        saveDraft()
        dir = value; selected = ""; detail = {}; query = ""
        listing = {issues: [], repo: "", error: "", fetched: 0}
        loadComposer()
        load(false)
    }
    function setState(value) {
        if (value === stateFilter) return
        stateFilter = value; listing = {issues: [], repo: repo, error: "", fetched: 0}
        load(false)
    }
    // A cached list shows at once and refreshes from GitHub when it's over a minute old.
    function load(refresh) {
        if (!dir) return
        var wanted = dir, st = stateFilter
        loading = true
        host.invoke({op: "issues", dir: wanted, state: st, refresh: refresh}, function(data) {
            if (issues.dir !== wanted || issues.stateFilter !== st) return
            issues.loading = false; issues.listing = data
            if (!refresh && data.repo && Date.now() / 1000 - data.fetched > 60) { issues.load(true); return }
            var fresh = issues.number && data.issues.find(function(i) { return i.number === issues.number })
            if (fresh && fresh.updated !== issues.detail.updated) issues.loadDetail()
        }, function() { issues.loading = false })
    }
    function refreshAll() { load(true) }
    function select(value) {
        if (value === selected) return
        saveDraft()
        selected = value; detail = {}
        loadComposer()
        if (number) loadDetail()
    }
    function loadDetail() {
        var n = number, wanted = dir, updated = current.updated || 0
        host.invoke({op: "issue", dir: wanted, number: n, updated: updated}, function(data) {
            if (issues.number === n && issues.dir === wanted) issues.detail = data
        })
    }
    function newIssue() { select("new"); focusComposer() }
    function loadComposer() {
        var k = key(), saved = local[k]
        if (!saved) {
            var d = host.state.drafts.find(function(d) { return d.status === "draft" && d.issue && d.issue.dir === issues.dir && d.issue.number === issues.number })
            saved = d ? {id: d.id, text: d.text, attachments: host.draftFiles(d), error: d.queue_error || ""} : {id: "", text: "", attachments: [], error: ""}
        }
        loadingDraft = true
        draftId = saved.id; text = saved.text; attachments = saved.attachments; draftError = saved.error || ""; dirty = false
        loadingDraft = false
    }
    function remember() { local[key()] = {id: draftId, text: text, attachments: attachments, error: draftError} }
    function changed() {
        if (loadingDraft || !composing) return
        dirty = true; remember(); saveTimer.restart()
    }
    function saveDraft(callback) {
        saveTimer.stop()
        if (!dirty || !composing) { if (callback && draftId) callback(draftId); return }
        dirty = false
        var k = key()
        host.invoke({op: "issue-draft-save", issue: target(), text: text, attachments: attachments}, function(d) {
            host.rememberDraft(d)
            if (issues.local[k]) issues.local[k].id = d.id
            if (issues.key() === k) issues.draftId = d.id
            if (callback) callback(d.id)
        })
    }
    // Attachments need the draft to exist: save first, then act on it.
    function withDraft(action) {
        if (!draftId) dirty = true
        saveDraft(action)
    }
    function applyDraft(d) {
        if (!d || !d.issue) return
        host.rememberDraft(d)
        var k = d.issue.dir + "#" + d.issue.number, mine = k === key()
        local[k] = {id: d.id, text: mine && dirty ? text : d.text, attachments: host.draftFiles(d), error: d.queue_error || ""}
        if (!mine) return
        loadingDraft = true
        draftId = d.id; attachments = host.draftFiles(d); draftError = d.queue_error || ""
        if (!dirty) text = d.text
        loadingDraft = false
    }
    // After a screenshot (or anything else that opens an issue draft from outside).
    function openDraft(id) {
        host.invoke({op: "draft-get", id: id}, function(d) {
            if (!d.issue) return
            if (d.issue.dir !== issues.dir && issues.projects.find(function(p) { return p.value === d.issue.dir })) issues.chooseProject(d.issue.dir)
            issues.select(d.issue.number ? String(d.issue.number) : "new")
            issues.applyDraft(d); issues.focusComposer()
        })
    }
    function attach(path) {
        withDraft(function(id) { host.invoke({op: "draft-attach", id: id, target: path}, function(d) { issues.applyDraft(d) }) })
    }
    function pasteImage() {
        withDraft(function(id) {
            host.invoke({op: "draft-paste-image", id: id}, function(d) {
                if (d && d.id) { issues.applyDraft(d); host.message = "Image added from the clipboard" }
            })
        })
    }
    function removeAttachment(index) { var next = attachments.slice(); next.splice(index, 1); attachments = next; changed() }
    function discard() {
        saveTimer.stop()
        var id = draftId, wasNew = creating
        delete local[key()]
        loadingDraft = true; draftId = ""; text = ""; attachments = []; draftError = ""; dirty = false; loadingDraft = false
        if (id) host.invoke({op: "remove", kind: "draft", id: id}, function() { host.refresh(null, false) })
        host.message = wasNew ? "New issue discarded" : "Instructions cleared"
    }
    function handOver() {
        if (!canHandOver) return
        saveTimer.stop(); handing = true; draftError = ""
        var k = key(), n = number, name = project.label, who = host.agentLabel(agent)
        host.invoke({op: "issue-handoff", issue: target(), agent: agent, text: text, attachments: attachments}, function(result) {
            issues.handing = false
            delete issues.local[k]
            host.rememberDraft(result.draft)
            if (issues.key() === k) { issues.loadingDraft = true; issues.draftId = ""; issues.text = ""; issues.attachments = []; issues.dirty = false; issues.loadingDraft = false }
            host.message = n ? "Handed #" + n + " to " + who + " in " + name : who + " is writing the issue in " + name
            host.refresh(null, false)
        }, function() { issues.handing = false })
    }
    // The Print key: a screenshot joins the request being written here.
    function printTarget() {
        if (!composing) return null
        saveDraft()
        return {open: true, draft: draftId, thread: "", issue: target()}
    }
    function statusText() {
        var who = host.agentLabel(agent), name = project.label || "the project"
        if (draftError) return draftError
        if (handing) return "Starting " + who + " in a new " + name + " workspace…"
        if (creating) return who + " starts in a new " + name + " workspace, reads the code, then files the issue."
        if (busyIssue) return hasContent ? "Starts another " + who + " thread for #" + number + " with these instructions." : "An agent is already on #" + number + ". Add instructions to start another."
        return who + " starts in a new " + name + " workspace and implements #" + number + "."
    }
    function focusSearch() { search.forceActiveFocus(); search.selectAll() }
    function focusComposer() {
        Qt.callLater(function() { if (host.keyboardReady && host.view === "issues" && issues.composing) composer.forceActiveFocus() })
    }
    function focusPrimary() { if (creating) focusComposer(); else search.forceActiveFocus() }
    function focusList() {
        if (!list.count) return
        list.forceActiveFocus()
        if (list.currentIndex < 0) list.currentIndex = 0
        select(String(visibleIssues[list.currentIndex].number))
    }

    Timer { id: saveTimer; interval: 550; onTriggered: issues.saveDraft() }
    // New issues from agents and changes on GitHub show up while the view is open.
    Timer {
        interval: 60000; repeat: true
        running: issues.visible && issues.host.opened && !issues.host.backgrounded && issues.repo !== ""
        onTriggered: if (!issues.host.activeRequest) issues.load(true)
    }
    // When an agent stops writing a new issue, fetch the list so the issue appears.
    property int writingNow: writing.filter(function(d) { return issues.progress(d, "writing") === "writing" || d.status === "queued" }).length
    onWritingNowChanged: if (visible && repo) Qt.callLater(function() { issues.load(true) })

    spacing: Style.space(18)

    // No repository: one explanation instead of two empty columns.
    Item {
        visible: issues.projectsLoaded && issues.repo === ""
        Layout.fillWidth: true; Layout.fillHeight: true
        WBEmptyState {
            anchors.centerIn: parent; width: Math.min(Style.space(420), parent.width - Style.space(30))
            glyph: "\uf09b"; glyphColor: Color.muted
            title: issues.project.label ? issues.project.label + " isn’t on GitHub" : "No projects yet"
            body: issues.project.label ? "It has no GitHub remote. Push it to a GitHub repository, then choose it again to see its issues."
                : "Each folder in ~/Projects appears here, most recently active first."
        }
    }

    // Left: the project's issues.
    ColumnLayout {
        visible: issues.repo !== ""
        Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumWidth: Style.space(340); Layout.preferredWidth: Style.space(480)
        spacing: Style.space(10)
        RowLayout {
            Layout.fillWidth: true; spacing: Style.spacing.controlGap
            Ui.TextField {
                id: search; placeholderTextColor: Color.muted; Layout.fillWidth: true; Layout.minimumWidth: Style.space(150)
                placeholderText: "Search issues"; text: issues.query; onTextChanged: issues.query = text
                leftPadding: Style.spacing.controlPaddingX + Style.space(24)
                Keys.onDownPressed: issues.focusList()
                Text { anchors.left: parent.left; anchors.leftMargin: Style.spacing.controlPaddingX + 1; anchors.verticalCenter: parent.verticalCenter; textFormat: Text.PlainText; text: "󰍉"; color: search.activeFocus ? Color.accent : Color.muted; font.family: Style.font.family; font.pixelSize: Style.font.icon }
            }
            Ui.ButtonGroup {
                value: issues.stateFilter
                options: [{value: "open", label: "Open"}, {value: "closed", label: "Closed"}]
                onChanged: function(v) { issues.setState(v) }
            }
            WBButton { iconText: "󰑐"; iconSpinning: issues.loading; tooltipText: "Refresh from GitHub · Ctrl+R"; onClicked: issues.refreshAll() }
            // On a small screen the button keeps only its icon.
            WBButton { iconText: "󰐕"; text: issues.width > Style.space(900) ? "New issue" : ""; selected: issues.creating; tooltipText: "New issue: describe it and the agent writes it up · Ctrl+N"; onClicked: issues.newIssue() }
        }
        // A list kept from earlier stays visible when GitHub can't be reached.
        WBText {
            visible: issues.listing.error !== "" && issues.listing.issues.length > 0
            Layout.fillWidth: true; wrapMode: Text.Wrap; elide: Text.ElideNone
            text: issues.listing.error + " Showing the list from " + issues.host.shortDate(issues.listing.fetched) + "."
            color: Color.urgent; font.pixelSize: Style.font.caption
        }
        // New issues still being written by an agent.
        Repeater {
            model: issues.writing.slice(0, 3)
            delegate: Ui.BorderSurface {
                id: pending
                required property var modelData
                readonly property var thread: issues.host.findThread(modelData.thread)
                Layout.fillWidth: true; implicitHeight: pendingRow.implicitHeight + Style.space(14)
                radius: Style.cornerRadius; color: Util.alpha(Color.accent, 0.05); borderSpec: Border.flat(Util.alpha(Color.accent, 0.35), 1)
                RowLayout {
                    id: pendingRow
                    anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: Style.space(10); anchors.rightMargin: Style.space(6); spacing: Style.space(10)
                    Text { textFormat: Text.PlainText; text: issues.host.agentGlyph(pending.modelData.started_thread); color: Color.accent; font.family: Style.font.family; font.pixelSize: Style.font.icon }
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: Style.space(2)
                        WBText { Layout.fillWidth: true; text: (pending.modelData.text || "New issue").split("\n")[0]; font.bold: true }
                        RowLayout {
                            Layout.fillWidth: true; spacing: Style.space(6)
                            Text { textFormat: Text.PlainText; text: issues.host.statusGlyph(pending.thread.status || ""); color: issues.host.statusColor(pending.thread.status || ""); font.family: Style.font.family; font.pixelSize: Style.font.caption }
                            WBText {
                                Layout.fillWidth: true; color: Color.muted; font.pixelSize: Style.font.caption
                                text: "New issue · " + issues.host.agentLabel(pending.modelData.started_thread) + " · " + issues.progress(pending.modelData, "writing the issue")
                            }
                        }
                    }
                    WBButton { text: "Go to thread ↗"; visible: !!pending.modelData.thread; onClicked: issues.host.invoke({op: "focus", id: pending.modelData.thread}, function() { issues.host.dismiss() }) }
                }
            }
        }
        Item {
            Layout.fillWidth: true; Layout.fillHeight: true
            ListView {
                id: list
                readonly property int gap: Style.space(4)
                anchors.fill: parent; clip: true; model: issues.visibleIssues
                keyNavigationEnabled: true; activeFocusOnTab: true; boundsBehavior: Flickable.StopAtBounds
                highlightFollowsCurrentItem: false
                QQC.ScrollBar.vertical: QQC.ScrollBar {}
                onCurrentIndexChanged: if (activeFocus && currentIndex >= 0 && currentIndex < issues.visibleIssues.length) issues.select(String(issues.visibleIssues[currentIndex].number))
                onActiveFocusChanged: if (activeFocus && currentIndex >= 0 && currentIndex < issues.visibleIssues.length) issues.select(String(issues.visibleIssues[currentIndex].number))
                Keys.onReturnPressed: issues.focusComposer()
                Keys.onEnterPressed: issues.focusComposer()
                Keys.onPressed: function(event) {
                    if (event.key === Qt.Key_Home) { currentIndex = 0; event.accepted = true }
                    else if (event.key === Qt.Key_End) { currentIndex = count - 1; event.accepted = true }
                }
                delegate: Item {
                    id: row
                    required property var modelData
                    required property int index
                    readonly property bool chosen: issues.selected === String(modelData.number)
                    readonly property bool cursor: list.activeFocus && list.currentIndex === index
                    readonly property var handoff: issues.handoffFor(modelData.number)
                    readonly property var handoffThread: handoff ? issues.host.findThread(handoff.thread) : ({})
                    width: list.width; height: rowBody.implicitHeight + Style.space(16) + list.gap
                    Ui.BorderSurface {
                        anchors.fill: parent; anchors.bottomMargin: list.gap; anchors.rightMargin: Style.space(10)
                        radius: Style.cornerRadius
                        color: row.chosen ? Util.alpha(Color.accent, 0.08) : rowMouse.containsMouse ? Style.hoverFill : "transparent"
                        borderSpec: Border.flat(row.chosen || row.cursor ? Color.accent : "transparent", row.cursor ? 2 : 1)
                        Behavior on color { ColorAnimation { duration: 120 } }
                        RowLayout {
                            id: rowBody
                            anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: Style.space(10); anchors.rightMargin: Style.space(10); spacing: Style.space(10)
                            Text { Layout.alignment: Qt.AlignTop; textFormat: Text.PlainText; text: issues.stateGlyph(row.modelData); color: issues.stateColor(row.modelData); font.family: Style.font.family; font.pixelSize: Style.font.icon }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: Style.space(3)
                                WBText { Layout.fillWidth: true; text: row.modelData.title; font.bold: true }
                                RowLayout {
                                    Layout.fillWidth: true; spacing: Style.space(10)
                                    WBText {
                                        text: "#" + row.modelData.number + " · " + (row.modelData.state === "closed" ? "closed " + issues.ago(row.modelData.closed) : "updated " + issues.ago(row.modelData.updated))
                                        color: Color.muted; font.pixelSize: Style.font.caption
                                    }
                                    Item {
                                        Layout.fillWidth: true; implicitHeight: labelRow.implicitHeight; clip: true
                                        Row {
                                            id: labelRow; spacing: Style.space(10)
                                            Repeater {
                                                model: row.modelData.labels
                                                delegate: Row {
                                                    required property var modelData
                                                    spacing: Style.space(4)
                                                    Text { textFormat: Text.PlainText; text: "●"; color: modelData.color; font.family: Style.font.family; font.pixelSize: Style.font.caption }
                                                    Text { textFormat: Text.PlainText; text: modelData.name; color: Color.muted; font.family: Style.font.family; font.pixelSize: Style.font.caption }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                            // An agent is working on it: its glyph and Herdr status dot.
                            Row {
                                visible: !!row.handoff; spacing: Style.space(5); Layout.alignment: Qt.AlignVCenter
                                Text { textFormat: Text.PlainText; text: row.handoff ? issues.host.agentGlyph(row.handoff.started_thread) : ""; color: Color.muted; font.family: Style.font.family; font.pixelSize: Style.font.body }
                                WBStatus { host: issues.host; state: row.handoffThread.status || ""; label: "" }
                            }
                        }
                    }
                    MouseArea {
                        id: rowMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                        onClicked: { list.currentIndex = row.index; list.forceActiveFocus(); issues.select(String(row.modelData.number)) }
                        onDoubleClicked: issues.focusComposer()
                    }
                }
            }
            Rectangle {
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; height: Style.space(24)
                opacity: list.contentHeight > list.height ? Math.max(0, Math.min(1, (list.contentY - list.originY) / height)) : 0; visible: opacity > 0
                gradient: Gradient { GradientStop { position: 0; color: Color.popups.background } GradientStop { position: 1; color: Util.alpha(Color.popups.background, 0) } }
            }
            Rectangle {
                anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: Style.space(24)
                opacity: list.contentHeight > list.height ? Math.max(0, Math.min(1, (list.originY + list.contentHeight - list.height - list.contentY) / height)) : 0; visible: opacity > 0
                gradient: Gradient { GradientStop { position: 0; color: Util.alpha(Color.popups.background, 0) } GradientStop { position: 1; color: Color.popups.background } }
            }
            Column {
                anchors.centerIn: parent; width: Math.min(Style.space(340), parent.width - Style.space(30)); spacing: Style.space(16)
                visible: issues.visibleIssues.length === 0
                readonly property bool failed: issues.listing.error !== "" && issues.listing.issues.length === 0
                readonly property bool waiting: issues.loading && issues.listing.issues.length === 0 && !failed
                WBEmptyState {
                    width: parent.width
                    glyph: parent.failed ? "󰗖" : parent.waiting ? "󰔟" : issues.listing.issues.length ? "󰍉" : "\uf41b"
                    glyphColor: parent.failed ? Color.urgent : parent.waiting ? Color.muted : Color.accent
                    title: parent.failed ? "Couldn’t load the issues" : parent.waiting ? "Loading issues…" : issues.listing.issues.length ? "No matching issues"
                        : issues.stateFilter === "open" ? "No open issues" : "No closed issues"
                    body: parent.failed ? issues.listing.error : parent.waiting ? "" : issues.listing.issues.length ? "Try another search."
                        : issues.stateFilter === "open" ? "Describe one in your own words and " + issues.host.agentLabel(issues.agent) + " writes it up." : "Closed issues appear here."
                }
                WBButton {
                    anchors.horizontalCenter: parent.horizontalCenter; visible: !parent.waiting && issues.stateFilter === "open" && !issues.listing.issues.length
                    iconText: "󰐕"; text: "New issue"; onClicked: issues.newIssue()
                }
            }
        }
    }
    Rectangle { visible: issues.repo !== ""; Layout.fillHeight: true; width: 1; color: issues.host.line }

    // Right: the selected issue, or a new one, and what to hand over.
    ColumnLayout {
        id: pane
        visible: issues.repo !== ""
        readonly property bool loaded: !!issues.number && issues.detail.number === issues.number
        readonly property var handoff: issues.number ? issues.handoffFor(issues.number) : null
        Layout.preferredWidth: Style.space(540); Layout.minimumWidth: Style.space(380); Layout.maximumWidth: Style.space(600); Layout.fillHeight: true
        spacing: Style.space(12)

        Item {
            visible: !issues.selected
            Layout.fillWidth: true; Layout.fillHeight: true
            WBEmptyState {
                anchors.centerIn: parent; width: Math.min(Style.space(340), parent.width - Style.space(30))
                glyph: "\uf41b"; glyphColor: Color.muted
                title: "Select an issue"
                body: "Read it here, then hand it to " + issues.host.agentLabel(issues.agent) + " with any extra instructions. Ctrl+N describes a new one."
            }
        }
        // Heading: the issue, or the new issue being described.
        RowLayout {
            visible: !!issues.selected; Layout.fillWidth: true; spacing: Style.space(12)
            Text {
                Layout.alignment: Qt.AlignTop; textFormat: Text.PlainText
                text: issues.creating ? "󰐕" : issues.stateGlyph(issues.current)
                color: issues.creating ? Color.accent : issues.stateColor(issues.current)
                font.family: Style.font.family; font.pixelSize: Style.font.display
            }
            ColumnLayout {
                Layout.fillWidth: true; spacing: Style.space(3)
                WBText {
                    Layout.fillWidth: true; wrapMode: Text.Wrap; maximumLineCount: 2; font.pixelSize: Style.font.heading; font.bold: true
                    text: issues.creating ? "New issue in " + issues.project.label : (issues.current.title || "")
                }
                WBText {
                    Layout.fillWidth: true; color: Color.muted; font.pixelSize: Style.font.caption
                    text: issues.creating ? issues.repo + " · " + issues.host.agentLabel(issues.agent) + " writes it from your description"
                        : ["#" + issues.number, issues.stateLabel(issues.current), issues.current.author ? "by " + issues.current.author : "",
                           "updated " + issues.ago(issues.current.updated), pane.loaded && issues.detail.comments ? issues.host.plural(issues.detail.comments, "comment", "comments") : ""]
                           .filter(function(p) { return !!p }).join(" · ")
                }
            }
            Ui.PanelActionButton { visible: !!issues.number; Layout.alignment: Qt.AlignTop; focusable: true; iconText: "󰏌"; tooltipText: "Open on GitHub"; onClicked: issues.host.openOutside(issues.current.url) }
            Ui.PanelActionButton { visible: !!issues.number; Layout.alignment: Qt.AlignTop; focusable: true; iconText: "󰆏"; tooltipText: "Copy link"; onClicked: issues.host.invoke({op: "copy", text: issues.current.url}, function() { issues.host.message = "Link copied" }) }
        }
        Flow {
            visible: !!issues.number && (issues.current.labels || []).length > 0
            Layout.fillWidth: true; spacing: Style.space(6)
            Repeater {
                model: issues.current.labels || []
                delegate: Ui.BorderSurface {
                    required property var modelData
                    implicitWidth: chip.implicitWidth + Style.space(14); implicitHeight: chip.implicitHeight + Style.space(4)
                    radius: height / 2; color: Util.alpha(modelData.color, 0.14); borderSpec: Border.flat(Util.alpha(modelData.color, 0.55), 1)
                    WBText { id: chip; anchors.centerIn: parent; text: modelData.name; font.pixelSize: Style.font.caption }
                }
            }
        }
        // The issue itself, as GitHub shows it (images become links).
        Ui.BorderSurface {
            visible: !!issues.number
            Layout.fillWidth: true; Layout.fillHeight: true; radius: Style.cornerRadius; clip: true
            color: Style.normalFill; borderSpec: Border.flat(issues.host.line, 1)
            QQC.ScrollView {
                id: bodyScroll
                anchors.fill: parent; anchors.margins: Style.space(14); visible: pane.loaded; clip: true
                contentWidth: availableWidth
                TextEdit {
                    width: bodyScroll.availableWidth; text: pane.loaded ? issues.detail.markdown : ""; textFormat: TextEdit.MarkdownText
                    readOnly: true; selectByMouse: true; activeFocusOnTab: true; wrapMode: TextEdit.Wrap
                    color: Color.foreground; selectionColor: Style.selectionFillFor(Color.foreground, Color.accent); selectedTextColor: Color.foreground
                    font.family: Style.font.family; font.pixelSize: Style.font.body
                    onLinkActivated: function(link) { issues.host.openOutside(link) }
                }
            }
            WBEmptyState {
                anchors.centerIn: parent; width: Math.min(Style.space(300), parent.width - Style.space(32)); visible: !pane.loaded
                glyph: "󰔟"; glyphColor: Color.muted; title: "Loading the issue…"; body: ""
            }
        }
        WBText {
            visible: pane.loaded && !!issues.detail.error; Layout.fillWidth: true; wrapMode: Text.Wrap; elide: Text.ElideNone
            text: (issues.detail.error || "") + " Showing the copy from earlier."; color: Color.urgent; font.pixelSize: Style.font.caption
        }
        // An agent already on this issue.
        RowLayout {
            visible: !!pane.handoff; Layout.fillWidth: true; spacing: Style.space(8)
            readonly property var thread: pane.handoff ? issues.host.findThread(pane.handoff.thread) : ({})
            Text { textFormat: Text.PlainText; text: pane.handoff ? issues.host.agentGlyph(pane.handoff.started_thread) : ""; color: Color.accent; font.family: Style.font.family; font.pixelSize: Style.font.body }
            Text { textFormat: Text.PlainText; text: issues.host.statusGlyph(parent.thread.status || ""); color: issues.host.statusColor(parent.thread.status || ""); font.family: Style.font.family; font.pixelSize: Style.font.bodySmall }
            WBText {
                Layout.fillWidth: true; color: Color.muted; font.pixelSize: Style.font.bodySmall
                text: pane.handoff ? issues.host.agentLabel(pane.handoff.started_thread) + " · " + issues.progress(pane.handoff, "working on it") + " · handed over " + issues.ago(pane.handoff.queued) : ""
            }
            WBButton { text: "Go to thread ↗"; visible: !!pane.handoff && !!pane.handoff.thread; onClicked: issues.host.invoke({op: "focus", id: pane.handoff.thread}, function() { issues.host.dismiss() }) }
        }
        // What to hand over: optional instructions for an issue, or the whole description of a new one.
        QQC.ScrollView {
            visible: issues.composing
            Layout.fillWidth: true; clip: true; contentWidth: availableWidth
            Layout.fillHeight: issues.creating; Layout.preferredHeight: issues.creating ? -1 : Style.space(104)
            WBEditor {
                id: composer
                readOnly: issues.handing; text: issues.text
                placeholderText: issues.creating
                    ? "What should the issue be about?\n\nDescribe the problem or idea in your own words. " + issues.host.agentLabel(issues.agent)
                      + " reads the code, then writes a complete issue with context and acceptance criteria."
                      + (issues.host.screenshotKey ? "\n\nPress " + issues.host.screenshotKey + " to add a screenshot." : "")
                    : "Anything to add? (optional)\nA preferred approach, limits, what to test…"
                onPasted: issues.pasteImage()
                onTextChanged: if (issues.text !== text) { issues.text = text; issues.changed() }
            }
        }
        Flow {
            visible: issues.composing && issues.attachments.length > 0
            Layout.fillWidth: true; spacing: Style.space(6)
            Repeater {
                model: issues.attachments
                delegate: Ui.BorderSurface {
                    id: attached
                    required property string modelData
                    required property int index
                    implicitWidth: attachedRow.implicitWidth + Style.space(12); implicitHeight: attachedRow.implicitHeight + Style.space(4)
                    radius: Style.cornerRadius; color: Util.alpha(Color.foreground, 0.04); borderSpec: Border.flat(issues.host.line, 1)
                    Row {
                        id: attachedRow; anchors.centerIn: parent; spacing: Style.space(4)
                        Text { anchors.verticalCenter: parent.verticalCenter; textFormat: Text.PlainText; text: issues.host.fileGlyph(attached.modelData); color: Color.accent; font.family: Style.font.family; font.pixelSize: Style.font.body }
                        WBText { anchors.verticalCenter: parent.verticalCenter; width: Math.min(implicitWidth, Style.space(170)); text: attached.modelData.split("/").pop(); elide: Text.ElideMiddle; font.pixelSize: Style.font.bodySmall }
                        Ui.PanelActionButton { focusable: true; iconText: "󰏌"; tooltipText: "Open"; onClicked: issues.host.openOutside(attached.modelData) }
                        Ui.PanelActionButton { focusable: true; iconText: "󰆴"; hoverColor: Color.urgent; tooltipText: "Remove"; enabled: !issues.handing; onClicked: issues.removeAttachment(attached.index) }
                    }
                }
            }
        }
        WBText { visible: issues.composing; Layout.fillWidth: true; text: issues.statusText(); color: issues.draftError ? Color.urgent : Color.muted; font.pixelSize: Style.font.bodySmall; wrapMode: Text.Wrap; elide: Text.ElideNone }
        RowLayout {
            visible: issues.composing; Layout.fillWidth: true; spacing: Style.space(12)
            Item { Layout.fillWidth: true }
            WBButton {
                visible: issues.text.trim() !== "" || issues.attachments.length > 0; enabled: !issues.handing
                iconText: "󰆴"; text: issues.creating ? "Discard" : "Clear"; destructive: true; onClicked: issues.discard()
            }
            WBButton {
                primary: true; enabled: issues.canHandOver; tooltipText: "Ctrl+Enter"
                iconText: issues.host.agentGlyph(issues.agent)
                text: issues.handing ? "Starting…" : (issues.creating ? "Write issue with " : issues.busyIssue ? "Start another " : "Implement with ") + issues.host.agentLabel(issues.agent)
                onClicked: issues.handOver()
            }
        }
    }
}
