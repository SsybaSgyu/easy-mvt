// Minimal, explicit bridge: the page gets native file dialogs, Quit and its backend access
// token, nothing else. The main process only answers pages from the local UI's exact origin.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("mvtDesktop", {
  pick: (kind) => ipcRenderer.invoke("pick", kind === "folder" ? "folder" : "file"),
  quit: () => ipcRenderer.invoke("quit"),
  token: () => ipcRenderer.invoke("token"),
});
