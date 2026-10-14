import QtQuick
import QtQuick.Window
import QtQuick.Layouts
import QtQuick.Controls as QQC
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Hyprland
import qs.Commons
import qs.Ui as Ui

Item {
    id: root
    property var shell: null
    property var manifest: null
    property string omarchyPath: ""
    readonly property string helper: Qt.resolvedUrl("workbench.py").toString().replace("file://", "")
    property bool opened: false
    // Keyboard focus stays with the panel until a click elsewhere; see focusGrab.
    property bool holdFocus: false
    property bool focusPending: false
    // A layer surface always draws over windows, so while an opened output is on screen the
    // panel steps aside, keeping its state, and returns when that output's windows close.
    property bool backgrounded: false
    property bool watchingLaunch: false
    property var launchedWindows: []
    property string view: "shelf"
    property string machine: "local"
    property string threadKey: ""
    property var state: ({threads: [], items: [], drafts: [], cards: [], machines: []})
    property string error: ""
    property string message: ""
    property bool onlyThread: false
    property string kindFilter: "All types"
    property string query: ""
    property string selectedItem: ""
    property var detail: ({})
    property bool adding: false
    property string draftId: ""
    property string draftThread: ""
    property string draftText: ""
    property var attachments: []
    property string draftStatus: "draft"
    property string draftError: ""
    property bool recording: false
    property bool transcribing: false
    property string pendingDraft: ""
    property bool loadingDraft: false
    property bool draftDirty: false
    property var queue: []
    property var activeRequest: null
    property bool gotResponse: false
    property string stderrText: ""
    property var screenChoice: null
    // A capture can also start a new thread: Claude or Codex in a fresh Herdr workspace for a
    // project, with the note as its first message (a project from ~/Projects).
    property string destMode: "existing"
    property string newProject: ""
    property string newAgent: "claude"
    property var projects: []
    property bool starting: false
    property string startedAgent: ""
    readonly property bool newThread: view === "capture" && destMode === "new"
    readonly property bool canStartHere: machine === "local" || machine.indexOf("local:") === 0
    readonly property bool dropdownOpen: machinePicker.popupOpen || threadPicker.popupOpen || typePicker.popupOpen || draftPicker.popupOpen || projectPicker.popupOpen || issueProjectPicker.popupOpen
    readonly property bool windowActive: window.contentItem.Window.active
    readonly property bool keyboardReady: opened && windowActive && !filePicker.opened && !shortcutHelp.opened && !dropdownOpen
    // Shared surface tokens: Omarchy's separator tint and popup border, so the
    // panel follows the theme's [popups] and [controls] values.
    readonly property color line: Util.alpha(Color.foreground, 0.12)
    readonly property var surfaceBorder: Border.surfaceSpec("popups", "border", Color.popups.border, Math.max(1, Style.space(2)))
    // The key bound to Workbench's screenshot (Print, or Super+Alt+Print from setup); empty when none is bound.
    readonly property string screenshotKey: state.screenshot_key || ""
    readonly property int thumbHeight: Style.space(92)
    readonly property real cardTitleHeight: Math.ceil(titleMetrics.lineSpacing * 2) + Style.space(4)
    readonly property real cardHeight: thumbHeight + Style.space(36) + cardTitleHeight + captionMetrics.lineSpacing
    // Herdr draws agent status in the terminal palette, which is this theme's colors.toml.
    property var tones: ({})
    readonly property color green: tones.green || Color.accent
    readonly property color yellow: tones.yellow || Color.accent
    readonly property var thread: findThread(view === "capture" ? draftThread : threadKey)
    readonly property var threadOptions: state.threads.filter(function(t) { return t.machine === root.machine }).map(function(t) {
        return {value:t.id, label:t.title + (t.live ? "" : " · closed"), description:t.project + " · " + t.agent + " · " + t.status}
    })
    readonly property var visibleItems: state.items.filter(function(i) {
        return !i.reference && (!root.onlyThread || i.thread === root.threadKey)
            && (root.kindFilter === "All types" || i.kind === root.kindFilter)
            && (!root.query || (i.title + " " + i.project + " " + i.thread_title).toLowerCase().indexOf(root.query.toLowerCase()) !== -1)
    })
    // Issue requests are drafted and followed in the Issues view.
    readonly property var pendingDrafts: state.drafts.filter(function(d) { return d.status !== "sent" && !d.issue }).map(function(d) {
        var t = root.findThread(d.thread), files = root.draftFiles(d)
        var target = d.new_thread ? "New " + root.agentLabel(d.new_thread.agent) + " thread · " + root.projectLabel(d.new_thread.dir) : (t.title || "Choose a thread")
        return {value:d.id,label:(d.text ? d.text.split("\n")[0].slice(0,48) : files.length ? root.plural(files.length,"attachment","attachments") : "New note"),description:target + (d.status === "draft" ? "" : d.status === "queued" ? " · queued" : d.status === "sending" ? " · sending" : " · check delivery")}
    })

    function findThread(key) { return state.threads.find(function(t) { return t.id === key }) || ({}) }
    function draftFiles(d) { return d.attachments || (d.attachment ? [d.attachment] : []) }
    function agentLabel(agent) { return agent === "codex" ? "Codex" : "Claude" }
    function agentGlyph(agent) { return agent === "codex" ? "󰚩" : "󰛄" }
    function projectLabel(dir) { var row = projects.find(function(p) { return p.value === dir }); return row ? row.label : String(dir || "").split("/").pop() }
    // The thread you're capturing from decides the project; otherwise the most recently active project.
    function projectFor(cwd) {
        var home = projects.length ? projects[0].value : Quickshell.env("HOME")
        var match = projects.slice(1).filter(function(p) { return cwd === p.value || String(cwd || "").indexOf(p.value + "/") === 0 })
            .sort(function(a, b) { return b.value.length - a.value.length })[0]
        return match ? match.value : cwd === home ? home : projects.length > 1 ? projects[1].value : home
    }
    function statusColor(s) { return s === "working" ? yellow : s === "blocked" ? Color.urgent : (s === "idle" || s === "done") ? green : Color.muted }
    function statusGlyph(s) { return (s === "working" || s === "blocked" || s === "done") ? "●" : (s === "idle" || s === "closed") ? "○" : "·" }
    function fileGlyph(name) {
        var m = String(name || "").toLowerCase().match(/\.([a-z0-9]+)$/), e = m ? m[1] : ""
        if (/^(png|jpe?g|webp|gif|svg|bmp)$/.test(e)) return "󰋩"
        if (e === "pdf") return "󰈦"
        if (e === "md" || e === "markdown") return "󰍔"
        if (/^(html?|xhtml)$/.test(e)) return "󰌝"
        if (/^(zip|tar|gz|tgz|xz|bz2|7z|rar|zst)$/.test(e)) return "󰗄"
        if (e === "json") return "󰘦"
        if (/^(docx?|odt|rtf)$/.test(e)) return "󰈬"
        if (/^(xlsx?|ods|csv|tsv)$/.test(e)) return "󰈛"
        if (/^(txt|rst|log)$/.test(e)) return "󰈙"
        return "󰈔"
    }
    // A path under the home folder reads as ~/…; "Copy path" still copies the full path.
    function displayPath(path) {
        var home = String(Quickshell.env("HOME") || ""), text = String(path || "")
        return home && text.indexOf(home + "/") === 0 ? "~" + text.slice(home.length) : text
    }
    function kindGlyph(item) { return !item || !item.kind ? "󰈔" : item.kind === "Link" ? "󰌹" : item.kind === "Page" ? "󰖟" : fileGlyph(item.target) }
    function shortDate(ts) {
        if (!ts) return ""
        var d = new Date(ts * 1000), now = new Date(), yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1)
        if (d.toDateString() === now.toDateString()) return "Today " + Qt.formatTime(d, "HH:mm")
        if (d.toDateString() === yesterday.toDateString()) return "Yesterday"
        return Qt.formatDate(d, d.getFullYear() === now.getFullYear() ? "d MMM" : "d MMM yyyy")
    }
    function plural(n, one, many) { return n + " " + (n === 1 ? one : many) }
    function parseTones(raw) {
        var out = {}
        String(raw || "").split("\n").forEach(function(line) {
            var m = line.match(/^\s*([A-Za-z0-9_]+)\s*=\s*["']?(#[0-9A-Fa-f]{6})/)
            if (m) out[m[1]] = m[2]
        })
        return out
    }
    function chooseScreen() {
        var name = Hyprland.focusedMonitor ? Hyprland.focusedMonitor.name : ""
        return Quickshell.screens.find(function(s) { return s.name === name }) || Quickshell.screens.find(function(s) { return s.name !== "MONITOR-SAFETY" })
    }
    function invoke(request, callback, failed) {
        queue.push({request:request, callback:callback, failed:failed}); pump()
    }
    function pump() {
        if (worker.running || activeRequest || queue.length === 0) return
        activeRequest = queue.shift(); gotResponse = false; stderrText = ""
        worker.command = ["python3", helper, "rpc", JSON.stringify(activeRequest.request)]
        worker.running = true
    }
    function receive(text) {
        gotResponse = true
        try {
            var response = JSON.parse(text)
            if (!response.ok) {
                if(activeRequest && activeRequest.request.op==="browse")filePicker.error=response.error
                else error=response.error
                if(activeRequest && activeRequest.failed)activeRequest.failed()
            }
            else if (activeRequest && activeRequest.callback) activeRequest.callback(response.data)
        } catch(e) { error = "Could not read the Workbench response: " + String(e) }
    }
    function refresh(callback, live) {
        invoke({op:"snapshot",machine:machine,refresh:live !== false}, function(data) {
            state = data
            var current=data.drafts.find(function(d){return d.id===draftId})
            if(current && !draftDirty && draftStatus!=="draft") {
                var wasQueued=draftStatus==="queued"
                setDraft(current)
                if(wasQueued && current.status==="sent")message="Capture sent to the thread"
            }
            if (data.connection_error) error = data.connection_error
            if (!threadKey) {
                var focused = data.threads.find(function(t) { return t.machine === root.machine && t.focused && t.live })
                if (focused) threadKey = focused.id
            }
            if (callback) callback()
        })
    }
    function open(payloadJson) {
        var p = {}; try { p = JSON.parse(payloadJson || "{}") } catch(e) {}
        themeColors.reload()
        saveDraft()
        comeBack()
        // Re-arming a fresh grab also brings focus back when summoned while already open.
        holdFocus=false;focusPending=true
        screenChoice = chooseScreen(); opened = true
        holdFocus=true
        view = p.view === "capture" || p.view === "issues" ? p.view : "shelf"
        error = ""; message = ""; pendingDraft = p.draft || ""
        threadKey = p.thread || ""
        onlyThread = !!p.thread && view === "shelf"
        machine = p.machine || "local"
        if (p.item) { query="";kindFilter="All types";detail={} }
        refresh(function() {
            if (state.voice) {recording=true;pendingDraft=state.voice.draft}
            if (root.view === "capture") {
                if (pendingDraft) { loadDraft(pendingDraft); pendingDraft = "" }
                else {
                    var saved = state.drafts.find(function(d) {return (d.status === "draft" || d.status === "queued") && d.thread === threadKey && !d.issue})
                    if (saved) setDraft(saved)
                    else newDraft(threadKey)
                }
            }
            if (root.view === "issues") {
                issuesView.start()
                if (pendingDraft) { issuesView.openDraft(pendingDraft); pendingDraft = "" }
            }
            if (root.view === "shelf" && p.item) {
                root.selectItem(p.item)
                Qt.callLater(function() {
                    if (!root.opened || root.view !== "shelf" || root.selectedItem !== p.item) return
                    var index=root.visibleItems.findIndex(function(item){return item.id===p.item})
                    if (index>=0) {grid.currentIndex=index;grid.positionViewAtIndex(index,GridView.Contain)}
                })
            }
            root.focusPrimary()
        })
    }
    function close() {
        saveDraft();issuesView.saveDraft()
        if (recording) stopVoice()
        closeMenus();filePicker.close();shortcutHelp.close()
        opened = false
        holdFocus=false;focusPending=false
        comeBack()
    }
    function dismiss() { close(); if (shell) shell.hide("io.github.vulpeszerda27.workbench") }
    function openOutside(target) {
        // Release the grab first so the new window can take keyboard focus.
        holdFocus=false;watchingLaunch=true;launchedWindows=[];launchWatch.restart()
        invoke({op:"open",target:target},function(){root.stepAside()},function(){root.watchingLaunch=false;root.holdFocus=true})
    }
    function stepAside() { if(opened && !backgrounded){closeMenus();backgrounded=true} }
    function comeBack() { launchWatch.stop();watchingLaunch=false;launchedWindows=[];backgrounded=false }
    function switchView(next) {
        closeMenus();saveDraft();issuesView.saveDraft(); view = next; error = ""; message = ""
        if (next === "capture" && (!draftId || draftStatus === "sent")) newDraft(threadKey)
        if (next === "issues") issuesView.start()
        focusPrimary()
    }
    readonly property var views: ["shelf","capture","issues"]
    function cycleView(step) { switchView(views[(views.indexOf(view)+step+views.length)%views.length]) }
    function closeMenus() { machinePicker.close();threadPicker.close();typePicker.close();draftPicker.close();issueProjectPicker.close() }
    function focusPrimary() {
        var expected=view
        Qt.callLater(function() { if(root.keyboardReady && root.view===expected) {root.focusPending=false;if(expected==="shelf")search.forceActiveFocus();else if(expected==="issues")issuesView.focusPrimary();else note.forceActiveFocus()} })
    }
    // Keeps a draft saved from another view in the snapshot until the next refresh.
    function rememberDraft(d) {
        var next=Object.assign({},state),found=false
        next.drafts=state.drafts.map(function(old){if(old.id===d.id){found=true;return d}return old})
        if(!found)next.drafts=[d].concat(next.drafts)
        state=next
    }
    function focusOutputs() {
        if(!grid.count)return
        grid.forceActiveFocus()
        if(grid.currentIndex<0)grid.currentIndex=0
        selectItem(visibleItems[grid.currentIndex].id)
    }
    function toggleAdd() {
        adding=!adding
        if(adding)Qt.callLater(function(){if(root.keyboardReady && root.view==="shelf" && root.adding)addTarget.forceActiveFocus()})
        else focusPrimary()
    }
    function createNew() {
        if(view==="shelf") {if(!adding)toggleAdd();else addTarget.forceActiveFocus()}
        else if(view==="issues") {if(issuesView.repo)issuesView.newIssue()}
        else if(!recording && !transcribing)newDraft(draftThread || threadKey)
    }
    function selectItem(key) {
        selectedItem = key
        invoke({op:"preview",id:key},function(data) { if (selectedItem === key) detail = data })
    }
    function addOutput(target) {
        invoke({op:"add",target:target,title:addTitle.text,thread:threadKey},function() {
            adding=false; addTarget.text=""; addTitle.text=""; message="Added to your shelf"; refresh(null,false)
            focusPrimary()
        })
    }
    function newDraft(key) {
        saveDraft()
        invoke({op:"draft-new",thread:key || threadKey},function(d) { setDraft(d); refresh(null,false);if(root.view==="capture")focusPrimary() })
    }
    function setDraft(d) {
        if (!d) return
        loadingDraft=true;draftId=d.id;draftThread=d.thread;draftText=d.text;attachments=draftFiles(d);draftStatus=d.status;draftError=d.queue_error || "";draftDirty=false
        startedAgent=d.started_thread || ""
        if(d.new_thread && d.status==="draft"){destMode="new";newProject=d.new_thread.dir;newAgent=d.new_thread.agent;if(!projects.length)loadProjects()}
        else destMode="existing"
        var t=findThread(d.thread);if(t.machine)machine=t.machine
        loadingDraft=false
    }
    function loadDraft(key) { saveDraft();invoke({op:"draft-get",id:key},function(d){setDraft(d);if(root.view==="capture")focusPrimary()}) }
    function discardDraft() {
        saveTimer.stop();draftDirty=false
        invoke({op:"remove",kind:"draft",id:draftId},function() {
            draftId="";draftText="";attachments=[]
            newDraft(draftThread);message="Draft discarded"
        })
    }
    function saveDraft(callback) {
        saveTimer.stop()
        if (!draftId || draftStatus !== "draft" || loadingDraft) { if(callback) callback(); return }
        var id=draftId
        if (!draftDirty && !callback) return
        var request={op:"draft-save",id:id,thread:newThread ? "" : draftThread,text:draftText,attachments:attachments,new_thread:newThread ? {dir:newProject,agent:newAgent} : null}
        draftDirty=false
        invoke(request,function(d) {
            var next=Object.assign({},state)
            next.drafts=state.drafts.map(function(old){return old.id===d.id ? d : old})
            state=next;message="Draft saved";if(callback)callback()
        })
    }
    function changedDraft() { if(!loadingDraft && draftStatus === "draft") {draftDirty=true;saveTimer.restart()} }
    function sendDraft() {
        if(recording || transcribing)return
        if(newThread){startThread();return}
        saveTimer.stop()
        var busy=thread.status!=="idle" && thread.status!=="done"
        invoke({op:busy ? "draft-queue" : "draft-send",id:draftId,thread:draftThread,text:draftText,attachments:attachments},function(d) {
            setDraft(d);message=(d.status==="queued" ? "Queued for " : "Sent to ") + (findThread(d.thread).title || "thread");refresh(null,false)
        })
    }
    function loadProjects(callback) { invoke({op:"projects"},function(rows){root.projects=rows;if(callback)callback()}) }
    function setDestination(mode) {
        if(mode===destMode || draftStatus!=="draft" || starting)return
        if(mode==="new" && !canStartHere){message="New threads start on this computer";return}
        destMode=mode
        if(mode==="new"){
            newAgent="claude"
            var cwd=findThread(draftThread || threadKey).cwd
            if(projects.length)newProject=projectFor(cwd)
            else loadProjects(function(){if(root.newThread && !root.newProject)root.newProject=root.projectFor(cwd);root.changedDraft()})
        }
        changedDraft()
    }
    function startThread() {
        if(starting || !newProject)return
        saveTimer.stop();starting=true;draftError="";message=""
        invoke({op:"draft-start-thread",id:draftId,text:draftText,attachments:attachments,new_thread:{dir:newProject,agent:newAgent},machine:machine},function(result) {
            root.starting=false;root.setDraft(result.draft)
            root.message="Started "+root.agentLabel(result.draft.started_thread)+" in "+(result.thread.project || root.projectLabel(root.newProject))
            root.refresh(null,false)
        },function(){root.starting=false})
    }
    function captureStatus() {
        var agent=agentLabel(newThread ? newAgent : startedAgent)
        if(draftError)return draftError
        if(newThread)return !canStartHere ? "New threads start on this computer." : starting ? "Starting "+agent+" in "+projectLabel(newProject)+"…"
            : agent+" starts in a new "+(newProject ? projectLabel(newProject) : "project")+" workspace in Herdr and gets this note as its first message."
        if(draftStatus==="sent")return startedAgent ? "Sent as the first message. Herdr names the new tab from it." : "Sent to the thread"
        if(draftStatus==="queued")return startedAgent ? "Queued — sends as the first message once "+agent+" is ready. You can close this panel." : "Queued — sends when the agent is ready. You can close this panel."
        if(draftStatus==="sending")return "Sending…"
        if(draftStatus!=="draft")return "Check the thread before sending again"
        if(thread.status==="working" || thread.status==="blocked" || thread.status==="unknown")return "Agent busy — queue this capture for when it is ready."
        return "Drafts save automatically. Send when you’re ready."
    }
    function removeAttachment(index) { var next=attachments.slice();next.splice(index,1);attachments=next;changedDraft() }
    function cancelQueue() {
        invoke({op:"draft-unqueue",id:draftId},function(d){setDraft(d);message="Queue cancelled. Your draft is saved and editable.";refresh(null,false)})
    }
    // The Print key asks this through `workbench print`: an open capture takes the screenshot.
    function printTarget(arg) {
        if(opened && !backgrounded && view==="issues")return JSON.stringify(issuesView.printTarget() || {open:false})
        if(!opened || backgrounded || view!=="capture")return JSON.stringify({open:false})
        saveDraft()
        return JSON.stringify({open:true,draft:draftStatus==="draft" ? draftId : "",thread:newThread ? "" : (draftThread || threadKey)})
    }
    // The editor pastes text itself; a copied image becomes an attachment.
    function pasteImage() {
        if(draftStatus!=="draft")return
        invoke({op:"draft-paste-image",id:draftId},function(d){if(d && d.id===root.draftId){root.attachments=root.draftFiles(d);root.message="Image added from the clipboard"}})
    }
    function attachFile(path) { var id=draftId;saveDraft(function() {
        invoke({op:"draft-attach",id:id,target:path},function(d) {if(draftId===id)setDraft(d);refresh(null,false)})
    }) }
    function stopVoice() {
        recording=false;transcribing=true;message="Transcribing…"
        saveDraft(function() { invoke({op:"voice-stop"},function(data) {
            transcribing=false;setDraft(data.draft);message="Dictation added to your draft";refresh(null,false)
        }) })
    }
    Process {
        id:worker
        stdout: StdioCollector { onStreamFinished: root.receive(text) }
        stderr: StdioCollector { onStreamFinished: root.stderrText = text }
        onExited: function(code) {
            if (!root.gotResponse && code !== 0) {
                root.error=root.stderrText || "Workbench could not complete this action."
                if (root.activeRequest && root.activeRequest.failed) root.activeRequest.failed()
            }
            if (root.activeRequest && root.activeRequest.request.op === "voice-stop") root.transcribing=false
            root.activeRequest=null;Qt.callLater(root.pump)
        }
    }
    Timer {id:saveTimer;interval:550;onTriggered:root.saveDraft()}
    // On-demand focus keeps other windows clickable, but with focus-follows-mouse Hyprland
    // hands it to whichever window the pointer rests on. The grab holds focus here until a
    // click elsewhere, which still reaches that window; clicking back into the panel re-arms it.
    HyprlandFocusGrab {id:focusGrab;windows:[window];active:root.opened && root.holdFocus;onCleared:root.holdFocus=false}
    onWindowActiveChanged:if(windowActive && opened){holdFocus=true;if(focusPending)focusPrimary()}
    // Windows that open soon after a launch belong to it (a splash and the main window, say).
    Timer {id:launchWatch;interval:10000;onTriggered:root.watchingLaunch=false}
    Connections {
        target:Hyprland
        function onRawEvent(event) {
            if (event.name === "openwindow" && root.watchingLaunch) {
                root.launchedWindows = root.launchedWindows.concat([event.data.split(",")[0]])
                root.stepAside()
            } else if (event.name === "closewindow" && root.backgrounded && root.launchedWindows.indexOf(event.data) !== -1) {
                root.launchedWindows = root.launchedWindows.filter(function(address) { return address !== event.data })
                if (!root.launchedWindows.length) root.comeBack()
            }
        }
    }
    Timer {interval:6000;repeat:true;running:root.opened && !root.backgrounded;onTriggered:if(!worker.running && !root.activeRequest && !root.recording && !root.transcribing)root.refresh(null,true)}
    // Theme switches rewrite this file; open() also reloads it in case the directory was replaced.
    FileView {
        id:themeColors
        path:Quickshell.env("HOME")+"/.local/state/omarchy/current/theme/colors.toml"
        watchChanges:true;printErrors:false
        onLoaded:root.tones=root.parseTones(text())
        onFileChanged:reload()
    }
    // Omarchy's searchable dropdowns size their search box from popupRowHeight and clip a
    // text field taller than that (13px text and up). Fit the row to a real field instead.
    Ui.TextField {id:fieldMetrics;visible:false}
    readonly property int searchRowHeight: Math.max(Style.spacing.popupRowHeight, Math.ceil(fieldMetrics.implicitHeight) + 2*Style.spacing.md - Style.spacing.controlPaddingX)
    FontMetrics {id:titleMetrics;font.family:Style.font.family;font.pixelSize:Style.font.body;font.bold:true}
    FontMetrics {id:captionMetrics;font.family:Style.font.family;font.pixelSize:Style.font.caption}
    function chooseFile(){filePicker.start(view==="issues" ? (issuesView.dir || "~") : (root.thread.cwd || "~"))}
    function rebind(picker, value) { picker.value = Qt.binding(value) }

    PanelWindow {
        id:window
        visible:root.opened && !root.backgrounded
        screen:root.screenChoice
        implicitWidth:Math.min(Style.space(1120), (screen ? screen.width : 1440)-Style.space(80))
        implicitHeight:Math.min(Style.space(760), (screen ? screen.height : 1000)-Style.space(100))
        color:"transparent"
        exclusionMode:ExclusionMode.Ignore
        WlrLayershell.namespace:"omarchy-workbench"
        WlrLayershell.layer:WlrLayer.Overlay
        WlrLayershell.keyboardFocus:root.opened ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.None
        WBFilePicker{id:filePicker;host:root;parent:window.contentItem;onChosen:function(path){if(root.view==="shelf")root.addOutput(path);else if(root.view==="issues")issuesView.attach(path);else root.attachFile(path)}}
        WBShortcutHelp{id:shortcutHelp;host:root;parent:window.contentItem}
        Ui.BorderSurface {
            id:card
            anchors.fill:parent;radius:Style.cornerRadius;color:Color.popups.background
            borderSpec:root.surfaceBorder
            FocusScope {
                anchors.fill:parent;focus:true
                anchors.leftMargin:card.borderLeft+Style.space(22);anchors.rightMargin:card.borderRight+Style.space(22)
                anchors.topMargin:card.borderTop+Style.space(18);anchors.bottomMargin:card.borderBottom+Style.space(14)
                Shortcut{sequence:"Ctrl+1";enabled:root.keyboardReady;onActivated:root.switchView("shelf")}
                Shortcut{sequence:"Ctrl+2";enabled:root.keyboardReady;onActivated:root.switchView("capture")}
                Shortcut{sequence:"Ctrl+3";enabled:root.keyboardReady;onActivated:root.switchView("issues")}
                Shortcut{sequence:"Ctrl+Tab";enabled:root.keyboardReady;onActivated:root.cycleView(1)}
                Shortcut{sequences:["Ctrl+Shift+Tab","Ctrl+Backtab"];enabled:root.keyboardReady;onActivated:root.cycleView(-1)}
                Shortcut{sequence:"Ctrl+F";enabled:root.keyboardReady;onActivated:{if(root.view==="issues"){if(issuesView.repo)issuesView.focusSearch();return}if(root.view!=="shelf")root.switchView("shelf");search.forceActiveFocus();search.selectAll()}}
                Shortcut{sequence:"Ctrl+N";enabled:root.keyboardReady;onActivated:root.createNew()}
                Shortcut{sequence:"Ctrl+O";enabled:root.keyboardReady;onActivated:if(root.view==="issues" ? issuesView.composing && !issuesView.handing : !root.recording && !root.transcribing && (root.view==="shelf" || root.draftStatus==="draft"))root.chooseFile()}
                Shortcut{sequences:["Ctrl+Return","Ctrl+Enter"];enabled:root.keyboardReady && root.view!=="shelf";onActivated:if(root.view==="issues")issuesView.handOver();else if(send.enabled)root.sendDraft()}
                Shortcut{sequence:"Ctrl+R";enabled:root.keyboardReady && root.view==="issues";onActivated:issuesView.refreshAll()}
                Shortcut{sequence:"Ctrl+T";enabled:root.keyboardReady && root.view==="capture";onActivated:root.setDestination(root.destMode==="new" ? "existing" : "new")}
                Shortcut{sequence:"F1";enabled:root.keyboardReady;onActivated:shortcutHelp.show()}
                Keys.priority:Keys.AfterItem
                Keys.onPressed:function(event) {
                    if(event.key===Qt.Key_Escape) {
                        if(root.dropdownOpen)root.closeMenus()
                        else if(root.view==="shelf" && root.adding){root.adding=false;root.focusPrimary()}
                        else root.dismiss()
                        event.accepted=true
                    }
                }
                ColumnLayout {
                    anchors.fill:parent;spacing:Style.space(14)
                    // Header: the Omarchy panel hero, with Shelf and Capture as a fixed segmented control.
                    Ui.PanelHero {
                        Layout.fillWidth:true
                        title:root.view==="shelf" ? "Output shelf" : root.view==="issues" ? "GitHub issues" : "Capture for a thread"
                        meta:"Workbench · "+(root.view==="shelf"
                            ? root.plural(root.visibleItems.length,"output","outputs")+(root.onlyThread ? " in this thread" : "")
                            : root.view==="issues"
                            ? (issuesView.project.label || "Choose a project")+(issuesView.repo ? " · "+root.plural(issuesView.listing.issues.length,issuesView.stateFilter+" issue",issuesView.stateFilter+" issues") : "")
                            : ({draft:"Draft",queued:"Queued",sending:"Sending",sent:"Sent"}[root.draftStatus] || "Check delivery")+" · "+root.plural(root.pendingDrafts.length,"saved draft","saved drafts"))
                        iconComponent:Component{Text{textFormat:Text.PlainText;text:"󰆧";color:Color.accent;font.family:Style.font.family;font.pixelSize:Style.font.display}}
                        trailingControl:Component{Ui.ButtonGroup{
                            value:root.view
                            options:[{value:"shelf",label:"Shelf",tooltip:"Ctrl+1"},{value:"capture",label:"Capture",tooltip:"Ctrl+2"},{value:"issues",label:"Issues",tooltip:"Ctrl+3"}]
                            onChanged:function(v){if(v!==root.view)root.switchView(v)}
                        }}
                    }
                    // Context: where outputs come from and where captures go, with Herdr's status dot.
                    RowLayout {
                        Layout.fillWidth:true;spacing:Style.spacing.controlGap
                        Ui.ButtonGroup {
                            visible:root.view==="capture";Layout.rightMargin:Style.space(6)
                            enabled:root.draftStatus==="draft" && !root.starting;opacity:enabled ? 1 : 0.55
                            value:root.destMode
                            options:[{value:"existing",label:"Existing thread"},{value:"new",label:"New thread",tooltip:root.canStartHere ? "Start a thread in a project · Ctrl+T" : "New threads start on this computer"}]
                            onChanged:function(v){root.setDestination(v)}
                        }
                        Ui.SearchableDropdown {
                            id:projectPicker
                            popupRowHeight:root.searchRowHeight
                            visible:root.newThread;Layout.preferredWidth:Style.space(300)
                            enabled:root.draftStatus==="draft" && !root.starting;opacity:enabled ? 1 : 0.55
                            value:root.newProject;options:root.projects;showLabel:false;placeholderText:"Find a project"
                            triggerLabel:root.projects.length ? "Choose a project" : "Loading projects…"
                            onChanged:function(v){root.newProject=v;root.changedDraft();root.rebind(projectPicker,function(){return root.newProject})}
                        }
                        Ui.ButtonGroup {
                            visible:root.newThread
                            enabled:root.draftStatus==="draft" && !root.starting;opacity:enabled ? 1 : 0.55
                            value:root.newAgent
                            options:[{value:"claude",label:"Claude",icon:"󰛄"},{value:"codex",label:"Codex",icon:"󰚩"}]
                            onChanged:function(v){root.newAgent=v;root.changedDraft()}
                        }
                        Item{visible:root.newThread;Layout.fillWidth:true}
                        WBText{visible:root.newThread;text:"New Herdr workspace · this computer";color:Color.muted;font.pixelSize:Style.font.bodySmall}
                        Ui.SearchableDropdown {
                            visible:!root.newThread && root.view!=="issues"
                            id:machinePicker
                            popupRowHeight:root.searchRowHeight
                            Layout.preferredWidth:Style.space(190);value:root.machine;options:root.state.machines;showLabel:false
                            enabled:root.view!=="capture" || root.draftStatus==="draft";opacity:enabled ? 1 : 0.55
                            onChanged:function(v){root.machine=v;root.threadKey="";if(root.view==="capture"){root.draftThread="";root.changedDraft()}root.rebind(machinePicker,function(){return root.machine});root.refresh(null,true)}
                        }
                        Ui.SearchableDropdown {
                            id:threadPicker
                            popupRowHeight:root.searchRowHeight
                            visible:!root.newThread && root.view!=="issues";Layout.fillWidth:true;value:root.view==="capture" ? root.draftThread : root.threadKey;options:root.threadOptions
                            enabled:root.view!=="capture" || root.draftStatus==="draft";opacity:enabled ? 1 : 0.55
                            triggerLabel:root.thread.title || "Choose a thread";placeholderText:"Find a thread by topic";showLabel:false
                            onChanged:function(v){if(root.view==="capture"){root.draftThread=v;root.changedDraft()}else root.threadKey=v;root.rebind(threadPicker,function(){return root.view==="capture" ? root.draftThread : root.threadKey})}
                        }
                        WBStatus {
                            host:root;visible:!!root.thread.id && !root.newThread && root.view!=="issues";Layout.leftMargin:Style.space(6)
                            state:root.thread.status || "";label:[root.thread.project,root.thread.agent,root.thread.status].filter(function(p){return !!p}).join(" · ")
                        }
                        // Issues: the projects in ~/Projects, each showing its repository's issues.
                        Ui.SearchableDropdown {
                            id:issueProjectPicker
                            popupRowHeight:root.searchRowHeight
                            visible:root.view==="issues";Layout.preferredWidth:Style.space(300)
                            value:issuesView.dir;options:issuesView.projects;showLabel:false;placeholderText:"Find a project"
                            triggerLabel:issuesView.projects.length ? "Choose a project" : issuesView.projectsLoaded ? "No projects in ~/Projects" : "Loading projects…"
                            onChanged:function(v){issuesView.chooseProject(v,true);root.rebind(issueProjectPicker,function(){return issuesView.dir})}
                        }
                        WBText{visible:root.view==="issues" && issuesView.repo!=="";Layout.leftMargin:Style.space(6);text:"\uf09b  "+issuesView.repo;color:Color.muted;font.pixelSize:Style.font.bodySmall}
                        Item{visible:root.view==="issues";Layout.fillWidth:true}
                        WBText{visible:root.view==="issues";text:"Hand to";color:Color.muted;font.pixelSize:Style.font.bodySmall}
                        Ui.ButtonGroup {
                            visible:root.view==="issues";enabled:!issuesView.handing;opacity:enabled ? 1 : 0.55
                            value:issuesView.agent
                            options:[{value:"claude",label:"Claude",icon:"󰛄"},{value:"codex",label:"Codex",icon:"󰚩"}]
                            onChanged:function(v){issuesView.agent=v}
                        }
                    }
                    Ui.PanelSeparator {Layout.fillWidth:true}
                    Ui.BorderSurface {
                        visible:root.error!=="";Layout.fillWidth:true;implicitHeight:errorRow.implicitHeight+Style.space(18)
                        radius:Style.cornerRadius;color:Util.alpha(Color.urgent,0.10);borderSpec:Border.flat(Util.alpha(Color.urgent,0.35),1)
                        MouseArea{anchors.fill:parent;onClicked:root.error=""}
                        RowLayout {
                            id:errorRow;anchors.left:parent.left;anchors.right:parent.right;anchors.verticalCenter:parent.verticalCenter
                            anchors.leftMargin:Style.space(12);anchors.rightMargin:Style.space(6);spacing:Style.space(10)
                            Text{Layout.alignment:Qt.AlignTop;textFormat:Text.PlainText;text:"󰗖";color:Color.urgent;font.family:Style.font.family;font.pixelSize:Style.font.icon}
                            WBText{id:errorText;Layout.fillWidth:true;elide:Text.ElideNone;wrapMode:Text.Wrap;text:root.error}
                            Ui.PanelActionButton{Layout.alignment:Qt.AlignTop;iconText:"󰅖";hoverColor:Color.urgent;tooltipText:"Dismiss";onClicked:root.error=""}
                        }
                    }
                    // Shelf: an index on the left, a useful preview on the right.
                    ColumnLayout {
                        visible:root.view==="shelf";Layout.fillWidth:true;Layout.fillHeight:true;spacing:Style.space(12)
                        RowLayout {
                            Layout.fillWidth:true;spacing:Style.spacing.controlGap
                            Ui.TextField {
                                id:search;placeholderTextColor:Color.muted;Layout.fillWidth:true;placeholderText:"Search outputs, projects or thread topics";text:root.query;onTextChanged:root.query=text
                                leftPadding:Style.spacing.controlPaddingX+Style.space(24)
                                Keys.onDownPressed:root.focusOutputs()
                                Text{anchors.left:parent.left;anchors.leftMargin:Style.spacing.controlPaddingX+1;anchors.verticalCenter:parent.verticalCenter;textFormat:Text.PlainText;text:"󰍉";color:search.activeFocus ? Color.accent : Color.muted;font.family:Style.font.family;font.pixelSize:Style.font.icon}
                            }
                            Ui.ButtonGroup {
                                value:root.onlyThread ? "thread" : "all"
                                options:[{value:"all",label:"All outputs"},{value:"thread",label:"This thread",tooltip:root.threadKey ? (root.findThread(root.threadKey).title || "The selected thread") : "Choose a thread above"}]
                                onChanged:function(v){root.onlyThread=v==="thread"}
                            }
                            Ui.Dropdown {id:typePicker;Layout.preferredWidth:Style.space(150);showLabel:false;value:root.kindFilter;options:["All types","Image","Document","PDF","Page","Link","File"];onChanged:function(v){root.kindFilter=v;root.rebind(typePicker,function(){return root.kindFilter})}}
                            WBButton{iconText:"󰐕";text:"Add";selected:root.adding;onClicked:root.toggleAdd();tooltipText:"Add a file or link · Ctrl+N"}
                        }
                        Ui.BorderSurface {
                            visible:root.adding;Layout.fillWidth:true;implicitHeight:addRow.implicitHeight+Style.space(20)
                            radius:Style.cornerRadius;color:Style.normalFill;borderSpec:Border.flat(root.line,1)
                            RowLayout {
                                id:addRow;anchors.left:parent.left;anchors.right:parent.right;anchors.verticalCenter:parent.verticalCenter;anchors.margins:Style.space(10);spacing:Style.spacing.controlGap
                                Ui.TextField{id:addTarget;placeholderTextColor:Color.muted;Layout.fillWidth:true;placeholderText:"Paste a URL or file path";onAccepted:if(text.trim())root.addOutput(text)}
                                Ui.TextField{id:addTitle;placeholderTextColor:Color.muted;Layout.preferredWidth:Style.space(200);placeholderText:"Title (optional)";onAccepted:if(addTarget.text.trim())root.addOutput(addTarget.text)}
                                WBButton{iconText:"󰉋";text:"Browse…";onClicked:root.chooseFile();tooltipText:"Ctrl+O"}
                                WBButton{primary:true;text:"Add to shelf";enabled:addTarget.text.trim()!=="";onClicked:root.addOutput(addTarget.text)}
                            }
                        }
                        RowLayout {
                            Layout.fillWidth:true;Layout.fillHeight:true;spacing:Style.space(18)
                            Item {
                                Layout.fillHeight:true;Layout.fillWidth:true;Layout.minimumWidth:Style.space(360);Layout.preferredWidth:Style.space(620)
                                GridView {
                                    id:grid;anchors.fill:parent;clip:true;model:root.visibleItems
                                    readonly property int gap:Style.space(12)
                                    cellWidth:Math.floor(width/Math.max(1,Math.floor(width/Style.space(200))));cellHeight:root.cardHeight+gap
                                    keyNavigationEnabled:true;activeFocusOnTab:true;boundsBehavior:Flickable.StopAtBounds
                                    QQC.ScrollBar.vertical:QQC.ScrollBar{}
                                    onCurrentIndexChanged:if(activeFocus && currentIndex>=0 && currentIndex<root.visibleItems.length)root.selectItem(root.visibleItems[currentIndex].id)
                                    onActiveFocusChanged:if(activeFocus && currentIndex>=0 && currentIndex<root.visibleItems.length)root.selectItem(root.visibleItems[currentIndex].id)
                                    Keys.onReturnPressed:if(currentIndex>=0 && currentIndex<root.visibleItems.length)root.openOutside(root.visibleItems[currentIndex].target)
                                    Keys.onEnterPressed:if(currentIndex>=0 && currentIndex<root.visibleItems.length)root.openOutside(root.visibleItems[currentIndex].target)
                                    Keys.onPressed:function(event){
                                        if(event.key===Qt.Key_Home){currentIndex=0;event.accepted=true}
                                        else if(event.key===Qt.Key_End){currentIndex=count-1;event.accepted=true}
                                    }
                                    delegate:Item {
                                        required property var modelData
                                        required property int index
                                        width:grid.cellWidth;height:grid.cellHeight
                                        WBOutputCard {
                                            anchors.fill:parent;anchors.rightMargin:grid.gap;anchors.bottomMargin:grid.gap
                                            host:root;item:modelData
                                            selected:root.selectedItem===modelData.id
                                            cursor:grid.activeFocus && grid.currentIndex===index
                                            onActivated:{grid.currentIndex=index;grid.forceActiveFocus();root.selectItem(modelData.id)}
                                            onOpened:root.openOutside(modelData.target)
                                        }
                                    }
                                }
                                // Scroll edges fade like the Omarchy menu once content hides past them.
                                Rectangle {
                                    anchors.left:parent.left;anchors.right:parent.right;anchors.top:parent.top;height:Style.space(24)
                                    opacity:grid.contentHeight>grid.height ? Math.max(0,Math.min(1,(grid.contentY-grid.originY)/height)) : 0;visible:opacity>0
                                    gradient:Gradient{GradientStop{position:0;color:Color.popups.background}GradientStop{position:1;color:Util.alpha(Color.popups.background,0)}}
                                }
                                Rectangle {
                                    anchors.left:parent.left;anchors.right:parent.right;anchors.bottom:parent.bottom;height:Style.space(24)
                                    opacity:grid.contentHeight>grid.height ? Math.max(0,Math.min(1,(grid.originY+grid.contentHeight-grid.height-grid.contentY)/height)) : 0;visible:opacity>0
                                    gradient:Gradient{GradientStop{position:0;color:Util.alpha(Color.popups.background,0)}GradientStop{position:1;color:Color.popups.background}}
                                }
                                Column {
                                    anchors.centerIn:parent;width:Math.min(Style.space(340),parent.width-Style.space(30));spacing:Style.space(16);visible:root.visibleItems.length===0
                                    WBEmptyState{
                                        width:parent.width;glyph:root.state.items.length ? "󰍉" : "󰆧"
                                        title:root.state.items.length ? "No matching outputs" : "A home for finished work"
                                        body:root.state.items.length ? "Try another search or filter, or switch to all outputs." : "Add a file or link. Agent-created documents and previews can land here too."
                                    }
                                    WBButton{anchors.horizontalCenter:parent.horizontalCenter;iconText:"󰐕";text:"Add a file";onClicked:root.chooseFile()}
                                }
                            }
                            Rectangle{Layout.fillHeight:true;width:1;color:root.line}
                            ColumnLayout {
                                id:previewPane
                                readonly property var detailThread:root.findThread(root.detail.thread)
                                readonly property bool loading:!!root.selectedItem && root.detail.id!==root.selectedItem
                                readonly property bool missing:!!root.detail.id && root.detail.exists===false
                                readonly property bool link:root.detail.kind==="Link" && (root.detail.text || "")===root.detail.target
                                readonly property bool page:root.detail.kind==="Page"
                                readonly property bool readable:!!root.detail.id && !root.detail.thumbnail && !!root.detail.text && !missing && !link && !page
                                Layout.preferredWidth:Style.space(400);Layout.minimumWidth:Style.space(300);Layout.maximumWidth:Style.space(440);Layout.fillHeight:true;spacing:Style.space(12)
                                RowLayout {
                                    Layout.fillWidth:true;spacing:Style.space(12);visible:!!root.detail.id
                                    Text{Layout.alignment:Qt.AlignTop;textFormat:Text.PlainText;text:root.kindGlyph(root.detail);color:Color.accent;font.family:Style.font.family;font.pixelSize:Style.font.display}
                                    ColumnLayout {
                                        Layout.fillWidth:true;spacing:Style.space(3)
                                        WBText{Layout.fillWidth:true;text:root.detail.title || "";font.pixelSize:Style.font.heading;font.bold:true;wrapMode:Text.Wrap;maximumLineCount:2}
                                        WBText{Layout.fillWidth:true;text:[root.detail.kind,root.detail.project,root.shortDate(root.detail.updated)].filter(function(p){return !!p}).join(" · ");color:Color.muted;font.pixelSize:Style.font.caption}
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth:true;spacing:Style.space(8);visible:!!root.detail.id
                                    WBStatus{host:root;visible:!!previewPane.detailThread.id;state:previewPane.detailThread.status || "";label:""}
                                    WBText{Layout.fillWidth:true;text:root.detail.thread ? (root.detail.thread_title || "Thread") : "Not assigned to a thread";color:Color.muted;font.pixelSize:Style.font.bodySmall}
                                }
                                Ui.BorderSurface {
                                    Layout.fillWidth:true;Layout.fillHeight:true;radius:Style.cornerRadius;clip:true
                                    color:Style.normalFill;borderSpec:Border.flat(root.line,1)
                                    Image{anchors.fill:parent;anchors.margins:Style.space(12);source:root.detail.id && !previewPane.loading ? (root.detail.thumbnail || "") : "";visible:source.toString()!=="";fillMode:Image.PreserveAspectFit;asynchronous:true;sourceSize.width:850;sourceSize.height:1000}
                                    QQC.ScrollView {
                                        id:previewScroll
                                        anchors.fill:parent;anchors.margins:Style.space(14);visible:previewPane.readable && !previewPane.loading;clip:true
                                        contentWidth:availableWidth
                                        TextEdit{width:previewScroll.availableWidth;text:previewPane.readable ? root.detail.text : "";textFormat:root.detail.format==="markdown" ? TextEdit.MarkdownText : TextEdit.PlainText;readOnly:true;selectByMouse:true;activeFocusOnTab:true;KeyNavigation.priority:KeyNavigation.BeforeItem;KeyNavigation.tab:openOutput;KeyNavigation.backtab:grid;wrapMode:TextEdit.Wrap;color:Color.foreground;selectionColor:Style.selectionFillFor(Color.foreground,Color.accent);selectedTextColor:Color.foreground;font.family:Style.font.family;font.pixelSize:Style.font.body;onLinkActivated:function(link){root.openOutside(link)}}
                                    }
                                    WBEmptyState {
                                        anchors.centerIn:parent;width:Math.min(Style.space(300),parent.width-Style.space(32))
                                        visible:!root.selectedItem || previewPane.loading || (!!root.detail.id && !root.detail.thumbnail && !previewPane.readable)
                                        glyph:!root.selectedItem ? "󰈈" : previewPane.loading ? "󰔟" : previewPane.missing ? "󰗖" : root.kindGlyph(root.detail)
                                        glyphColor:previewPane.missing ? Color.urgent : !root.selectedItem || previewPane.loading ? Color.muted : Color.accent
                                        title:!root.selectedItem ? "Select an output" : previewPane.loading ? "Loading preview…" : previewPane.missing ? "The original has moved or was removed" : previewPane.link ? root.detail.target.replace(/^https?:\/\//,"").split("/")[0] : previewPane.page ? "Web page" : "No preview for this file type"
                                        body:!root.selectedItem ? "Preview it here, then open it when you need more room." : previewPane.loading ? "" : previewPane.missing ? "Remove it from the shelf, or add it again from its new location." : previewPane.link ? "Open the link to view it in your browser." : previewPane.page ? "Open it to view it in your browser." : "Open it in its default app."
                                    }
                                }
                                WBText{Layout.fillWidth:true;visible:!!root.detail.id;text:root.detail.kind==="Link" ? (root.detail.target || "") : root.displayPath(root.detail.target);elide:Text.ElideMiddle;color:Color.muted;font.pixelSize:Style.font.caption}
                                Flow {
                                    Layout.fillWidth:true;spacing:Style.spacing.controlGap;visible:!!root.detail.id
                                    WBButton{id:openOutput;primary:true;iconText:"󰏌";text:root.detail.kind==="Link" ? "Open link" : "Open";enabled:root.detail.exists!==false;tooltipText:"Enter";onClicked:root.openOutside(root.detail.target)}
                                    WBButton{iconText:"󰆏";text:root.detail.kind==="Link" ? "Copy link" : "Copy path";onClicked:root.invoke({op:"copy",text:root.detail.target},function(){root.message="Copied"})}
                                    WBButton{text:"Go to thread ↗";visible:!!root.detail.thread;onClicked:root.invoke({op:"focus",id:root.detail.thread},function(){root.dismiss()})}
                                    WBButton{iconText:"󰐃";text:"Keep with thread";enabled:!!root.threadKey;tooltipText:root.threadKey ? "Pin it in the companion for "+(root.findThread(root.threadKey).title || "this thread") : "Choose a thread above first";onClicked:root.invoke({op:"add",target:root.detail.target,title:root.detail.title,thread:root.threadKey,reference:true},function(){root.message="Added to the companion";root.refresh(null,false)})}
                                    WBButton{iconText:"󰆴";text:"Remove from shelf";destructive:true;tooltipText:"The original file is untouched";onClicked:root.invoke({op:"remove",kind:"item",id:root.detail.id},function(){root.detail={};root.selectedItem="";root.refresh(null,false);root.message="Removed from shelf; the original file is untouched"})}
                                }
                            }
                        }
                    }
                    // Capture: the destination is always visible, submission is explicit.
                    ColumnLayout {
                        visible:root.view==="capture";Layout.fillWidth:true;Layout.fillHeight:true;spacing:Style.space(12)
                        RowLayout {
                            Layout.fillWidth:true;spacing:Style.spacing.controlGap
                            Ui.SearchableDropdown {id:draftPicker;popupRowHeight:root.searchRowHeight;Layout.preferredWidth:Style.space(300);options:root.pendingDrafts;value:root.draftId;showLabel:false;enabled:!root.recording && !root.transcribing;opacity:enabled ? 1 : 0.55;triggerLabel:root.draftStatus==="sent" ? "Sent capture" : "Saved drafts ("+root.pendingDrafts.length+")";placeholderText:"Find a saved draft";onChanged:function(v){root.loadDraft(v);root.rebind(draftPicker,function(){return root.draftId})}}
                            WBButton{iconText:"󰐕";text:"New draft";enabled:!root.recording && !root.transcribing;onClicked:root.newDraft(root.draftThread || root.threadKey);tooltipText:"Ctrl+N"}
                            Item{Layout.fillWidth:true}
                            WBKeyHints{visible:root.draftStatus==="draft";model:(root.screenshotKey ? [[root.screenshotKey,"screenshot"]] : []).concat([["Ctrl+O","attach a file"]])}
                        }
                        RowLayout {
                            Layout.fillWidth:true;Layout.fillHeight:true;spacing:Style.space(16)
                            QQC.ScrollView {
                                Layout.fillWidth:true;Layout.fillHeight:true;clip:true
                                WBEditor {id:note;text:root.draftText;readOnly:root.draftStatus!=="draft" || root.transcribing;placeholderText:"What should the agent look at?\n\nWrite a note or paste a link."+(root.screenshotKey ? "\nPress "+root.screenshotKey+" to add a screenshot when showing is easier." : "");onPasted:root.pasteImage();onTextChanged:{if(root.draftText!==text){root.draftText=text;root.changedDraft()}}}
                            }
                                Ui.BorderSurface {
                                    id:attachmentCard
                                    visible:root.attachments.length>0;Layout.preferredWidth:Style.space(320);Layout.fillHeight:true
                                    radius:Style.cornerRadius;color:Style.normalFill;borderSpec:Border.flat(root.line,1)
                                    ColumnLayout {
                                        anchors.fill:parent;anchors.margins:Style.space(14);spacing:Style.space(10)
                                        Ui.PanelSectionHeader{text:root.attachments.length===1 ? "ATTACHMENT" : root.attachments.length+" ATTACHMENTS";foreground:Color.foreground}
                                        QQC.ScrollView {
                                            id:attachmentScroll
                                            Layout.fillWidth:true;Layout.fillHeight:true;clip:true;contentWidth:availableWidth
                                            Column {
                                                width:attachmentScroll.availableWidth;spacing:Style.space(14)
                                                Repeater {
                                                    model:root.attachments
                                                    delegate:Column {
                                                        id:attached
                                                        required property string modelData
                                                        required property int index
                                                        readonly property bool image:/\.(png|jpg|jpeg|webp|gif)$/i.test(modelData)
                                                        width:parent.width;spacing:Style.space(6)
                                                        Ui.BorderSurface {
                                                            width:parent.width
                                                            height:attached.image && preview.implicitWidth>0 ? Math.min(Style.space(220),Math.round((width-Style.space(12))*preview.implicitHeight/preview.implicitWidth))+Style.space(12) : Style.space(72)
                                                            radius:Math.max(0,Style.cornerRadius-2);color:Util.alpha(Color.foreground,0.04);borderSpec:Border.flat(root.line,1)
                                                            Image{id:preview;anchors.fill:parent;anchors.margins:Style.space(6);visible:attached.image;source:attached.image ? Util.fileUrl(attached.modelData) : "";fillMode:Image.PreserveAspectFit;asynchronous:true;sourceSize.width:900;sourceSize.height:1000}
                                                            Text{anchors.centerIn:parent;visible:!attached.image;textFormat:Text.PlainText;text:root.fileGlyph(attached.modelData);color:Color.accent;font.family:Style.font.family;font.pixelSize:Style.font.display}
                                                        }
                                                        RowLayout {
                                                            width:parent.width;spacing:Style.space(4)
                                                            WBText{Layout.fillWidth:true;text:attached.modelData.split("/").pop();font.bold:true;elide:Text.ElideMiddle}
                                                            Ui.PanelActionButton{focusable:true;iconText:"󰏌";tooltipText:"Open";onClicked:root.openOutside(attached.modelData)}
                                                            Ui.PanelActionButton{focusable:true;iconText:"󰆴";hoverColor:Color.urgent;tooltipText:"Remove from this capture";enabled:root.draftStatus==="draft";onClicked:root.removeAttachment(attached.index)}
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                        WBText{Layout.fillWidth:true;visible:root.draftStatus==="draft";text:root.screenshotKey ? root.screenshotKey+" and Ctrl+O add more." : "Ctrl+O adds more.";color:Color.muted;font.pixelSize:Style.font.caption;wrapMode:Text.Wrap;elide:Text.ElideNone}
                                    }
                                }
                        }
                        RowLayout {
                            Layout.fillWidth:true;spacing:Style.space(12)
                            ColumnLayout {
                                Layout.fillWidth:true;spacing:Style.space(4)
                                RowLayout {
                                    Layout.fillWidth:true;spacing:Style.space(8)
                                    WBText{text:"To";color:Color.muted;font.pixelSize:Style.font.caption}
                                    WBStatus{host:root;visible:!!root.thread.id && !root.newThread;state:root.thread.status || "";label:""}
                                    Text{visible:root.newThread;textFormat:Text.PlainText;text:root.agentGlyph(root.newAgent);color:Color.accent;font.family:Style.font.family;font.pixelSize:Style.font.body}
                                    WBText{Layout.fillWidth:true;text:root.newThread ? "New "+root.agentLabel(root.newAgent)+" thread in "+(root.newProject ? root.projectLabel(root.newProject) : "a project") : root.thread.title ? root.thread.title+"  ·  "+root.thread.agent+" · "+root.thread.status : "Choose a destination above";font.bold:root.newThread || !!root.thread.title;color:root.newThread || root.thread.title ? Color.foreground : Color.muted}
                                }
                                WBText{Layout.fillWidth:true;text:root.captureStatus();color:root.draftError ? Color.urgent : Color.muted;font.pixelSize:Style.font.bodySmall;wrapMode:Text.Wrap;elide:Text.ElideNone}
                            }
                            WBButton{iconText:"󰆴";text:"Discard";destructive:true;visible:root.draftStatus==="draft";enabled:!root.recording && !root.transcribing && !root.starting;onClicked:root.discardDraft()}
                            WBButton{text:"Cancel queue";visible:root.draftStatus==="queued";enabled:!worker.running;onClicked:root.cancelQueue()}
                            WBButton{primary:root.draftStatus==="sent";text:"Go to thread ↗";visible:root.draftStatus!=="draft" && !!root.draftThread;onClicked:root.invoke({op:"focus",id:root.draftThread},function(){root.dismiss()})}
                            WBButton{
                                id:send;primary:true;visible:root.draftStatus==="draft";iconText:root.newThread ? "󰐕" : "󰒊";tooltipText:"Ctrl+Enter"
                                text:root.newThread ? (root.starting ? "Starting…" : "Start thread") : (root.thread.status==="idle" || root.thread.status==="done") ? "Send to thread" : "Queue for thread"
                                enabled:root.draftStatus==="draft" && (root.draftText.trim()!=="" || root.attachments.length>0) && !worker.running && !root.recording && !root.transcribing
                                    && (root.newThread ? !!root.newProject && !root.starting && root.canStartHere : !!root.draftThread && !!root.thread.live)
                                onClicked:root.sendDraft()
                            }
                        }
                    }
                    // Issues: a project's GitHub issues, handed to an agent to write or implement.
                    WBIssues {id:issuesView;host:root;visible:root.view==="issues";Layout.fillWidth:true;Layout.fillHeight:true}
                    RowLayout {
                        Layout.fillWidth:true;spacing:Style.space(16)
                        WBText{Layout.fillWidth:true;text:root.message || (root.view==="shelf" ? root.plural(root.visibleItems.length,"output","outputs")+" · files stay where you saved them"
                            : root.view==="issues" ? (issuesView.repo ? "Each handoff starts a fresh Herdr workspace" : "Projects come from the folders in ~/Projects")
                            : root.newThread ? "Your note and attachments start the new thread" : "Your note and attachments go only to the selected thread");color:root.message ? Color.foreground : Color.muted;font.pixelSize:Style.font.caption}
                        WBKeyHints{model:root.view==="shelf"
                            ? [["Ctrl+1/2/3","switch"],["Ctrl+F","search"],["Enter","open"],["Ctrl+N","add"],["F1","shortcuts"],["Esc","close"]]
                            : root.view==="issues" && !issuesView.repo ? [["Ctrl+1/2/3","switch"],["F1","shortcuts"],["Esc","close"]]
                            : root.view==="issues"
                            ? [["Ctrl+1/2/3","switch"],["Ctrl+N","new issue"]].concat(issuesView.composing ? [["Ctrl+Enter",issuesView.creating ? "write issue" : "implement"]] : []).concat([["Ctrl+R","refresh"],["F1","shortcuts"],["Esc","close"]])
                            : [["Ctrl+1/2/3","switch"],["Ctrl+T",root.newThread ? "existing thread" : "new thread"],["Ctrl+Enter",root.newThread ? "start" : (root.thread.status==="idle" || root.thread.status==="done") ? "send" : "queue"],["F1","shortcuts"],["Esc","close"]]}
                    }
                }
            }
        }
    }
}
