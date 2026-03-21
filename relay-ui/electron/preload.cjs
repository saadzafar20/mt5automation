const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronBridge', {
  openExternal: (url) => ipcRenderer.invoke('open-external', url),
  getPlatform: () => ipcRenderer.invoke('get-platform'),
  getVersion: () => ipcRenderer.invoke('get-version'),
  keyringGet: (service, userId) => ipcRenderer.invoke('keyring-get', service, userId),
  keyringSet: (service, userId, password) => ipcRenderer.invoke('keyring-set', service, userId, password),
  clipboardWrite: (text) => ipcRenderer.invoke('clipboard-write', text),
  isStartupEnabled: () => ipcRenderer.invoke('is-startup-enabled'),
  setStartup: (enabled) => ipcRenderer.invoke('set-startup', enabled),
  lastUserGet: () => ipcRenderer.invoke('last-user-get'),
  lastUserSet: (data) => ipcRenderer.invoke('last-user-set', data),
});
