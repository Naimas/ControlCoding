'use strict';
const { contextBridge, ipcRenderer } = require('electron');
const noArgs = channel => (...args) => args.length ? Promise.reject(new Error('unexpected_arguments')) : ipcRenderer.invoke(channel);
contextBridge.exposeInMainWorld('ccExternal', Object.freeze({
  snapshot: noArgs('external:snapshot'), chooseSource: noArgs('external:choose-source'), chooseWorkspace: noArgs('external:choose-workspace'),
  request: (action, value = null) => ipcRenderer.invoke('external:request', action, value),
  subscribe: callback => { if (typeof callback !== 'function') throw Error('callback_required'); const handler = (_event, value) => callback(value); ipcRenderer.on('external:state', handler); return () => ipcRenderer.removeListener('external:state', handler); }
}));
