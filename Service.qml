import QtQuick
import Quickshell.Io

Item {
    id: root

    property var manifest: null
    readonly property string sourceDir: manifest ? manifest.__sourceDir : ""

    Process {
        id: desktopSync
        command: ["python3", "-u", root.sourceDir + "/omarchy/desktop.py"]
        running: root.sourceDir !== ""
        stdinEnabled: true
        stderr: SplitParser {
            onRead: line => console.warn("Flatfind: " + line)
        }
    }
}
