import QtQuick
import QtQuick.Window
import QtQuick.Controls as QQC
import QtQuick.Layouts
import qs.Commons
import qs.Ui as Ui

QQC.Popup {
    id:picker
    property var host
    property string folder:""
    property var entries:[]
    property string selected:""
    property string error:""
    property bool hiddenFiles:false
    property var returnFocus:null
    property int browseRequest:0
    readonly property real modifiedWidth:Style.space(96)
    readonly property real typeWidth:Style.space(56)
    readonly property real sizeWidth:Style.space(64)
    // The columns in display order, which Ctrl+S cycles through. Dates and sizes start newest or largest first.
    readonly property var sortColumns:[
        {key:"name",label:"Name",about:"name"},
        {key:"modified",label:"Modified",about:"date modified",width:modifiedWidth},
        {key:"type",label:"Type",about:"file type",width:typeWidth},
        {key:"size",label:"Size",about:"size",width:sizeWidth}]
    property string sortKey:"name"
    property bool sortDescending:false
    property bool sortLoaded:false
    signal chosen(string path)
    width:Math.min(Style.space(760),parent.width-Style.space(48));height:Math.min(Style.space(600),parent.height-Style.space(48))
    x:(parent.width-width)/2;y:(parent.height-height)/2
    modal:true;focus:true;padding:Style.space(22)
    closePolicy:QQC.Popup.CloseOnEscape
    background:Ui.BorderSurface{color:Color.popups.background;radius:Style.cornerRadius;borderSpec:picker.host.surfaceBorder}
    QQC.Overlay.modal:Rectangle{color:Color.menu.scrim}
    onClosed:{
        browseRequest++
        var target=returnFocus
        returnFocus=null
        Qt.callLater(function(){if(!picker.opened && picker.parent.Window.active && target && target.visible && target.enabled)target.forceActiveFocus(Qt.OtherFocusReason)})
    }
    // `keep` reselects that entry after a re-sort, so the selection doesn't jump back to the top.
    function browse(path,keep) {
        error=""
        var request=++browseRequest
        host.invoke({op:"browse",path:path,hidden:hiddenFiles,sort:sortKey,descending:sortDescending},function(data){
            if(!picker.opened || request!==browseRequest)return
            if(data.file){picker.chosen(data.file);picker.close();return}
            var index=keep ? Math.max(0,data.entries.findIndex(function(e){return e.path===keep})) : 0
            folder=data.folder;entries=data.entries;selected="";location.text=folder
            list.currentIndex=entries.length ? index : -1;selected=entries.length ? entries[index].path : ""
            if(keep)list.positionViewAtIndex(index,ListView.Contain)
            if(picker.parent.Window.active)list.forceActiveFocus()
        })
    }
    function choose(index){if(index>=0 && index<entries.length)browse(entries[index].path)}
    function start(path){
        returnFocus=parent.Window.window ? parent.Window.window.activeFocusItem : null
        var saved=host.state.settings
        if(!sortLoaded && saved){
            sortKey=sortColumns.some(function(c){return c.key===saved.file_sort}) ? saved.file_sort : "name"
            sortDescending=!!saved.file_sort_descending;sortLoaded=true
        }
        open();browse(path || "~")
    }
    function toggleHidden(){hiddenFiles=!hiddenFiles;browse(folder || "~")}
    function sortBy(key){
        sortDescending=key===sortKey ? !sortDescending : key==="modified" || key==="size"
        sortKey=key;resort()
    }
    function cycleSort(){
        var keys=sortColumns.map(function(c){return c.key})
        sortBy(keys[(keys.indexOf(sortKey)+1)%keys.length])
    }
    function resort(){
        sortLoaded=true
        if(folder)browse(folder,selected)
        host.invoke({op:"settings",value:{file_sort:sortKey,file_sort_descending:sortDescending}})
    }
    function handleShortcut(event){
        if(event.key===Qt.Key_Escape){close();event.accepted=true}
        else if(event.key===Qt.Key_L && (event.modifiers & Qt.ControlModifier)){location.forceActiveFocus();location.selectAll();event.accepted=true}
        else if(event.key===Qt.Key_H && (event.modifiers & Qt.ControlModifier)){toggleHidden();event.accepted=true}
        else if(event.key===Qt.Key_S && (event.modifiers & Qt.ControlModifier)){cycleSort();event.accepted=true}
        else if(event.key===Qt.Key_R && (event.modifiers & Qt.ControlModifier)){sortBy(sortKey);event.accepted=true}
        else if(event.key===Qt.Key_Up && (event.modifiers & Qt.AltModifier)){browse(folder+"/..");event.accepted=true}
    }
    contentItem:ColumnLayout{
        spacing:Style.space(12)
        Keys.onPressed:function(event){picker.handleShortcut(event)}
        RowLayout{
            Layout.fillWidth:true;spacing:Style.space(12)
            Text{textFormat:Text.PlainText;text:picker.host.view==="shelf" ? "󰐕" : "󰏢";color:Color.accent;font.family:Style.font.family;font.pixelSize:Style.font.iconLarge+4}
            WBText{Layout.fillWidth:true;text:picker.host.view==="shelf" ? "Add a file to your shelf" : "Attach a reference file";font.pixelSize:Style.font.heading;font.bold:true}
            WBText{text:picker.entries.length===1 ? "1 item" : picker.entries.length+" items";color:Color.muted;font.pixelSize:Style.font.caption}
        }
        RowLayout{
            Layout.fillWidth:true;spacing:Style.spacing.controlGap
            WBButton{id:upButton;iconText:"󰁝";text:"Up";tooltipText:"Alt+Up";KeyNavigation.tab:homeButton;KeyNavigation.backtab:chooseButton.enabled ? chooseButton : list;onClicked:picker.browse(picker.folder+"/..")}
            WBButton{id:homeButton;iconText:"󰋜";text:"Home";KeyNavigation.tab:hiddenButton;KeyNavigation.backtab:upButton;onClicked:picker.browse("~")}
            WBButton{id:hiddenButton;iconText:picker.hiddenFiles ? "󰈈" : "󰈉";text:"Dotfiles";selected:picker.hiddenFiles;tooltipText:(picker.hiddenFiles ? "Hide" : "Show")+" dotfiles · Ctrl+H";KeyNavigation.tab:location;KeyNavigation.backtab:homeButton;onClicked:picker.toggleHidden()}
            Ui.TextField{
                id:location;placeholderTextColor:Color.muted;Layout.fillWidth:true;placeholderText:"Folder or file path"
                activeFocusOnTab:true;KeyNavigation.tab:list;KeyNavigation.backtab:hiddenButton
                Keys.priority:Keys.BeforeItem
                Keys.onPressed:function(event){picker.handleShortcut(event)}
                onAccepted:picker.browse(text)
            }
        }
        Ui.BorderSurface{
            visible:picker.error!=="";Layout.fillWidth:true;implicitHeight:pickerError.implicitHeight+Style.space(16)
            radius:Style.cornerRadius;color:Util.alpha(Color.urgent,0.10);borderSpec:Border.flat(Util.alpha(Color.urgent,0.35),1)
            WBText{id:pickerError;anchors.fill:parent;anchors.margins:Style.space(8);anchors.leftMargin:Style.space(12);text:picker.error;wrapMode:Text.Wrap;elide:Text.ElideNone}
        }
        Ui.BorderSurface{
            Layout.fillWidth:true;Layout.fillHeight:true;radius:Style.cornerRadius
            color:Style.normalFill;borderSpec:Border.flat(list.activeFocus ? Style.focusBorderColor : picker.host.line,1)
            ColumnLayout{
                anchors.fill:parent;anchors.margins:Style.space(4);spacing:Style.space(2)
                // Column headings sort the list; they line up with the row columns below.
                RowLayout{
                    Layout.fillWidth:true;Layout.leftMargin:Style.space(12);Layout.rightMargin:Style.space(12);spacing:Style.space(10)
                    Item{Layout.preferredWidth:Style.space(20)}
                    Repeater{
                        model:picker.sortColumns
                        delegate:Item{
                            id:heading
                            required property var modelData
                            readonly property bool active:picker.sortKey===modelData.key
                            Layout.fillWidth:!modelData.width;Layout.preferredWidth:modelData.width || 0
                            implicitHeight:headingText.implicitHeight+Style.space(10)
                            WBText{
                                id:headingText
                                anchors.verticalCenter:parent.verticalCenter;width:parent.width
                                horizontalAlignment:heading.modelData.key==="size" ? Text.AlignRight : Text.AlignLeft
                                text:heading.modelData.label+(heading.active ? (picker.sortDescending ? " ↓" : " ↑") : "")
                                color:heading.active || headingMouse.containsMouse ? Color.foreground : Color.muted
                                font.pixelSize:Style.font.caption;font.bold:heading.active
                            }
                            MouseArea{id:headingMouse;anchors.fill:parent;hoverEnabled:true;cursorShape:Qt.PointingHandCursor;onClicked:picker.sortBy(heading.modelData.key)}
                            Ui.PanelToolTip{visible:headingMouse.containsMouse;text:heading.active ? "Reverse the order · Ctrl+R" : "Sort by "+heading.modelData.about}
                        }
                    }
                }
                Rectangle{Layout.fillWidth:true;implicitHeight:1;color:picker.host.line}
                ListView{
                    id:list;Layout.fillWidth:true;Layout.fillHeight:true;clip:true;model:picker.entries;spacing:Style.space(2)
                    activeFocusOnTab:true;KeyNavigation.tab:chooseButton.enabled ? chooseButton : upButton;KeyNavigation.backtab:location
                    keyNavigationEnabled:true;keyNavigationWraps:false;boundsBehavior:Flickable.StopAtBounds;QQC.ScrollBar.vertical:QQC.ScrollBar{}
                    Keys.onReturnPressed:picker.choose(currentIndex)
                    Keys.onEnterPressed:picker.choose(currentIndex)
                    Keys.onPressed:function(event){
                        picker.handleShortcut(event)
                        if(event.accepted)return
                        if(event.key===Qt.Key_Backspace){picker.browse(picker.folder+"/..");event.accepted=true}
                        else if(event.key===Qt.Key_Home){currentIndex=count ? 0 : -1;positionViewAtBeginning();event.accepted=true}
                        else if(event.key===Qt.Key_End){currentIndex=count-1;positionViewAtEnd();event.accepted=true}
                    }
                    onCurrentIndexChanged:picker.selected=currentIndex>=0 && currentIndex<picker.entries.length ? picker.entries[currentIndex].path : ""
                    delegate:Ui.BorderSurface{
                        id:entry
                        required property var modelData
                        required property int index
                        readonly property bool current:list.currentIndex===index
                        width:list.width;height:Math.max(Style.space(36),Style.font.body+Style.spacing.rowPaddingX*2);radius:Style.cornerRadius
                        // The Omarchy menu's selected row: a quiet fill, its border and accent text.
                        color:current ? (list.activeFocus ? Color.menu.selectedBackground : Style.normalFill) : entryMouse.containsMouse ? Style.normalFill : "transparent"
                        borderSpec:current && list.activeFocus ? Border.surfaceSpec("menu","selected-border",Color.menu.selectedBorder,0) : Border.none()
                        RowLayout{
                            anchors.fill:parent;anchors.leftMargin:Style.space(12);anchors.rightMargin:Style.space(12);spacing:Style.space(10)
                            Text{
                                Layout.preferredWidth:Style.space(20);horizontalAlignment:Text.AlignHCenter;textFormat:Text.PlainText
                                text:entry.modelData.directory ? "󰉋" : picker.host.fileGlyph(entry.modelData.name)
                                color:entry.modelData.directory || (entry.current && list.activeFocus) ? Color.accent : Color.muted
                                font.family:Style.font.family;font.pixelSize:Style.font.icon
                            }
                            WBText{Layout.fillWidth:true;text:entry.modelData.name;font.bold:entry.modelData.directory;color:entry.current && list.activeFocus ? Color.menu.selectedText : Color.foreground}
                            WBText{Layout.preferredWidth:picker.modifiedWidth;text:picker.host.shortDate(entry.modelData.modified);color:Color.muted;font.pixelSize:Style.font.caption}
                            WBText{Layout.preferredWidth:picker.typeWidth;text:entry.modelData.type;color:Color.muted;font.pixelSize:Style.font.caption}
                            WBText{Layout.preferredWidth:picker.sizeWidth;horizontalAlignment:Text.AlignRight;text:entry.modelData.directory ? "" : entry.modelData.size;color:Color.muted;font.pixelSize:Style.font.caption}
                        }
                        MouseArea{id:entryMouse;anchors.fill:parent;hoverEnabled:true;cursorShape:Qt.PointingHandCursor;onClicked:{list.currentIndex=entry.index;list.forceActiveFocus()}onDoubleClicked:picker.choose(entry.index)}
                    }
                    WBEmptyState{anchors.centerIn:parent;width:Math.min(Style.space(320),parent.width-Style.space(24));visible:picker.entries.length===0 && picker.error==="";glyph:"󰝰";title:"This folder is empty";body:picker.hiddenFiles ? "" : "Ctrl+H shows dotfiles."}
                }
            }
        }
        RowLayout{
            Layout.fillWidth:true;spacing:Style.space(12)
            WBKeyHints{Layout.fillWidth:true;model:[["Enter","open"],["Ctrl+L","path"],["Alt+↑","up"],["Ctrl+H","dotfiles"],["Ctrl+S","sort"],["Esc","back"]]}
            WBButton{id:chooseButton;primary:true;text:list.currentIndex>=0 && picker.entries[list.currentIndex].directory ? "Open folder" : "Choose file";enabled:picker.selected!=="";KeyNavigation.tab:upButton;KeyNavigation.backtab:list;onClicked:picker.choose(list.currentIndex)}
        }
    }
}
