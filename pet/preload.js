// preload.js · 渲染进程 ↔ 主进程
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('pet', {
  relay: (msg) => ipcRenderer.invoke('pet:relay', msg),
  onRelay: (cb) => ipcRenderer.on('relay', (_e, m) => cb(m)),
  reportLists: (data) => ipcRenderer.invoke('pet:lists', data),
  onLists: (cb) => ipcRenderer.on('lists', (_e, d) => cb(d)),
  getState: () => ipcRenderer.invoke('pet:getState'),
  setClickThrough: (on) => ipcRenderer.invoke('pet:setClickThrough', on),
  // 控制窗口：按屏幕坐标增量移动自身
  moveCtrlBy: (dx, dy) => ipcRenderer.invoke('pet:moveCtrlBy', { dx, dy }),
  // 模型窗口：改变自身尺寸（编辑模式中键缩放用）
  fitModel: (size) => ipcRenderer.invoke('pet:fitModel', size),
  // 模型窗口：按屏幕坐标增量移动自身（编辑模式左键拖动用）
  moveModelBy: (dx, dy) => ipcRenderer.invoke('pet:moveModelBy', { dx, dy }),
  // 编辑模式开关（模型窗口接收）
  onEditMode: (cb) => ipcRenderer.on('edit-mode', (_e, on) => cb(on)),
  setEditMode: (on) => ipcRenderer.invoke('pet:editMode', on),
})
