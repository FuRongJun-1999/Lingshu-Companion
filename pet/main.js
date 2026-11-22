// main.js · 桌宠显示端（双窗口）
//
// 功能名：模型窗口 + 控制窗口分离
// 生效条件：Electron 运行时；模型服务 8770 可达
// 子功能：
//   - modelWin：透明置顶，只渲染 Live2D，大小可调、位置可拖
//   - ctrlWin：小控制窗口，齿轮 + 面板，位置独立
//   - 两个窗口各自记住位置；控制窗口通过 8791 端点改模型窗口大小
//   - F8 切换模型窗口穿透；F9 隐藏两者
// 执行：electron .
// 验证方式：/state 返回两窗口信息；调 /fit 只改 model 窗口尺寸
// 不适用条件：
//   - Windows 上 setIgnoreMouseEvents(forward) 收不到 mousemove，穿透只能手动
//   - 控制窗口不穿透（要能点），因此它覆盖处仍会挡下层

const { app, BrowserWindow, ipcMain, globalShortcut, screen, shell } = require('electron')
const path = require('node:path')
const fs = require('node:fs')

const LOG_PATH = path.join(__dirname, '_main.log')
function log(...args) {
  const line = new Date().toISOString().slice(11, 19) + '  ' +
    args.map(a => (typeof a === 'string' ? a : JSON.stringify(a))).join(' ')
  try {
    const st = fs.statSync(LOG_PATH)
    if (st.size > 2 * 1024 * 1024) fs.writeFileSync(LOG_PATH, '')
  } catch {}
  try { fs.appendFileSync(LOG_PATH, line + '\n') } catch {}
}

const BOUNDS_MODEL = path.join(__dirname, '_bounds_model.json')
const BOUNDS_CTRL = path.join(__dirname, '_bounds_ctrl.json')

function loadBounds(file, fallback) {
  try {
    const b = JSON.parse(fs.readFileSync(file, 'utf8'))
    if (b && Number.isFinite(b.width) && b.width > 50) return b
  } catch {}
  return fallback
}
function saveBounds(file, win) {
  try { fs.writeFileSync(file, JSON.stringify(win.getBounds())) } catch {}
}

let modelWin = null
let ctrlWin = null

/** 把矩形夹回主显示器工作区内，避免窗口跑到屏幕外（屏幕捕获会拍不到）。 */
function clampToScreen(b) {
  // 尺寸不设上限——直播常把窗口放大做上半身特写。
  // 只保证窗口左上角不跑到屏幕外，避免整块看不见。
  const wa = screen.getPrimaryDisplay().workArea
  const w = Math.max(120, Math.round(b.width))
  const h = Math.max(120, Math.round(b.height))
  return {
    x: Math.max(wa.x, Math.min(b.x, wa.x + wa.width - 80)),
    y: Math.max(wa.y, Math.min(b.y, wa.y + wa.height - 80)),
    width: w,
    height: h,
  }
}

function createModelWindow() {
  const { width, height } = screen.getPrimaryDisplay().workAreaSize
  // 默认放在屏幕最右侧的窄条（浏览器通常是最大化的，右侧剩 300~400px）
  const saved = loadBounds(BOUNDS_MODEL, {
    x: Math.max(0, width - 390), y: 10,
    width: 380, height: Math.min(1040, height - 30),
  })

  modelWin = new BrowserWindow({
    ...clampToScreen(saved),
    transparent: true,
    frame: false,
    alwaysOnTop: false,        // 模型不占顶层：被其他窗口盖住可以接受，但绝不能压住齿轮
    hasShadow: false,
    resizable: false,          // 大小由控制窗口滑块决定
    skipTaskbar: true,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      webSecurity: false,
    },
  })
  modelWin.loadFile(path.join(__dirname, 'renderer', 'model.html'))

  modelWin.on('moved', () => saveBounds(BOUNDS_MODEL, modelWin))
  modelWin.webContents.on('console-message', (event, ...rest) => {
    const msg = (event && event.message) || rest[1] || rest[0] || ''
    log('[model]', String(msg).slice(0, 300))
  })
  log('modelWin created', modelWin.getBounds())
}

function createControlWindow() {
  const { width: sw } = screen.getPrimaryDisplay().workAreaSize
  const saved = loadBounds(BOUNDS_CTRL, {
    x: Math.round(sw * 0.60) - 268, y: Math.round(screen.getPrimaryDisplay().workAreaSize.height * 0.12),
    width: 256, height: 470,
  })

  ctrlWin = new BrowserWindow({
    ...saved,
    transparent: true,
    frame: false,
    alwaysOnTop: true,         // 齿轮必须始终可用：控制窗口置顶
    hasShadow: false,
    resizable: true,
    skipTaskbar: false,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      webSecurity: false,
    },
  })
  ctrlWin.loadFile(path.join(__dirname, 'renderer', 'control.html'))

  ctrlWin.on('moved', () => saveBounds(BOUNDS_CTRL, ctrlWin))
  ctrlWin.on('resized', () => saveBounds(BOUNDS_CTRL, ctrlWin))
  ctrlWin.webContents.on('console-message', (event, ...rest) => {
    const msg = (event && event.message) || rest[1] || rest[0] || ''
    log('[ctrl]', String(msg).slice(0, 300))
  })
  // 齿轮必须在任何应用之上：模型窗口可以被盖住，控制窗口不行
  // 齿轮必须永远在模型窗口之上，否则一旦同屏就点不到
  ctrlWin.setAlwaysOnTop(true, 'screen-saver')
  ctrlWin.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })
  ctrlWin.once('ready-to-show', () => {
    try { ctrlWin.moveTop() } catch {}
  })

  log('ctrlWin created', ctrlWin.getBounds())
}

// ── 控制端点（控制窗口用；也便于外部脚本）──
function startControlServer() {
  const http = require('node:http')
  const srv = http.createServer((req, res) => {
    const url = new URL(req.url || '/', 'http://127.0.0.1')
    const json = (o, status = 200) => {
      res.writeHead(status, { 'content-type': 'application/json', 'access-control-allow-origin': '*' })
      res.end(JSON.stringify(o))
    }
      if (url.pathname === '/voice') {
        // 语音对话开关。ASR 尚未接通时明确拒绝，不假装成功。
        const on = url.searchParams.get('on') === '1'
        if (on) {
          return json({ ok: false, error: 'ASR 尚未接通，请到功能详情里配置语音识别' })
        }
        return json({ ok: true, voice: false })
      }
    if (url.pathname === '/state') {
      return json({
        model: modelWin ? modelWin.getBounds() : null,
        ctrl: ctrlWin ? ctrlWin.getBounds() : null,
        passthrough: modelWin ? !!modelWin.__passthrough : false,
        ctrlVisible: ctrlWin ? ctrlWin.isVisible() : false,
        ctrlOnTop: ctrlWin ? ctrlWin.isAlwaysOnTop() : false,
      })
    }
    if (url.pathname === '/fit' && modelWin) {
      const w = Math.max(120, Number(url.searchParams.get('w') || 0))
      const h = Math.max(120, Number(url.searchParams.get('h') || 0))
      if (!w || !h) return json({ ok: false })
      const b = modelWin.getBounds()
      const cx = b.x + Math.round(b.width / 2)
      const cy = b.y + Math.round(b.height / 2)
      const nb = clampToScreen({ x: cx - Math.round(w / 2), y: cy - Math.round(h / 2), width: w, height: h })
      modelWin.setBounds(nb)
      saveBounds(BOUNDS_MODEL, modelWin)
      log('[http] fit model ->', w, 'x', h)
      return json({ ok: true, bounds: modelWin.getBounds() })
    }
    if (url.pathname === '/modelpos' && modelWin && ctrlWin) {
      // 让模型窗口对齐到控制窗口右侧
      const c = ctrlWin.getBounds()
      modelWin.setBounds({ x: c.x + c.width, y: c.y, width: modelWin.getBounds().width, height: modelWin.getBounds().height })
      saveBounds(BOUNDS_MODEL, modelWin)
      return json({ ok: true, bounds: modelWin.getBounds() })
    }
    if (url.pathname === '/passthrough' && modelWin) {
      const on = url.searchParams.get('on') === '1'
      modelWin.__passthrough = on
      modelWin.setIgnoreMouseEvents(on, { forward: true })
      log('[http] model passthrough ->', on ? 'on' : 'off')
      return json({ ok: true, passthrough: on })
    }
    if (url.pathname === '/ctrlsize' && ctrlWin) {
      const w = Math.max(44, Number(url.searchParams.get('w') || 256))
      const h = Math.max(44, Number(url.searchParams.get('h') || 470))
      const b = ctrlWin.getBounds()
      ctrlWin.setBounds({ x: b.x, y: b.y, width: w, height: h })
      saveBounds(BOUNDS_CTRL, ctrlWin)
      return json({ ok: true, bounds: ctrlWin.getBounds() })
    }
    if (url.pathname === '/move' && modelWin) {
      const x = Number(url.searchParams.get('x'))
      const y = Number(url.searchParams.get('y'))
      if (!Number.isFinite(x) || !Number.isFinite(y)) return json({ ok: false })
      const b = modelWin.getBounds()
      const nb = clampToScreen({ x: Math.round(x), y: Math.round(y), width: b.width, height: b.height })
      modelWin.setBounds(nb)
      saveBounds(BOUNDS_MODEL, modelWin)
      return json({ ok: true, bounds: modelWin.getBounds() })
    }
    if (url.pathname === '/edit') {
      const on = url.searchParams.get('on') === '1'
      if (modelWin) modelWin.webContents.send('edit-mode', on)
      if (ctrlWin) ctrlWin.webContents.send('edit-mode-ack', on)
      log('[http] edit ->', on ? 'on' : 'off')
      return json({ ok: true, edit: on })
    }
    if (url.pathname === '/show') {
      modelWin && modelWin.show(); ctrlWin && ctrlWin.show(); return json({ ok: true })
    }
    if (url.pathname === '/hide') {
      modelWin && modelWin.hide(); ctrlWin && ctrlWin.hide(); return json({ ok: true })
    }
      if (url.pathname === '/open') {
        const target = url.searchParams.get('url') || ''
        // 只允许打开本机服务，避免被当成任意协议跳板
        if (!/^https?:\/\/(127\.0\.0\.1|localhost)[:/]/.test(target)) {
          return json({ ok: false, error: '只允许本地地址' }, 400)
        }
        shell.openExternal(target).catch(() => {})
        return json({ ok: true, opened: target })
      }
    if (url.pathname === '/focusmodel') {
      if (modelWin) { modelWin.show(); modelWin.moveTop() }
      return json({ ok: true })
    }
    res.writeHead(404); res.end('not found')
  })
  srv.listen(8791, '127.0.0.1', () => log('[http] control on 8791'))
}

ipcMain.handle('pet:setClickThrough', (_e, on) => {
  if (!modelWin) return false
  modelWin.__passthrough = !!on
  try { modelWin.setIgnoreMouseEvents(!!on, { forward: true }); return true } catch { return false }
})

ipcMain.handle('pet:moveCtrlBy', (_e, d) => {
  if (!ctrlWin || !d) return false
  const b = ctrlWin.getBounds()
  ctrlWin.setBounds({
    x: Math.round(b.x + (d.dx || 0)),
    y: Math.round(b.y + (d.dy || 0)),
    width: b.width, height: b.height,
  })
  return true
})

ipcMain.handle('pet:moveModelBy', (_e, d) => {
  if (!modelWin || !d) return false
  const b = modelWin.getBounds()
  modelWin.setBounds({
    x: Math.round(b.x + (d.dx || 0)),
    y: Math.round(b.y + (d.dy || 0)),
    width: b.width, height: b.height,
  })
  return true
})

ipcMain.handle('pet:fitModel', (_e, size) => {
  if (!modelWin || !size) return false
  const w = Math.max(120, Math.round(size))
  const b = modelWin.getBounds()
  const cx = b.x + Math.round(b.width / 2)
  const cy = b.y + Math.round(b.height / 2)
  modelWin.setBounds({ x: cx - Math.round(w / 2), y: cy - Math.round(w / 2),
                       width: w, height: w })
  saveBounds(BOUNDS_MODEL, modelWin)
  return true
})

ipcMain.handle('pet:editMode', (_e, on) => {
  if (modelWin) modelWin.webContents.send('edit-mode', !!on)
  if (ctrlWin) ctrlWin.webContents.send('edit-mode-ack', !!on)
  log('[ipc] editMode ->', on ? 'on' : 'off')
  return true
})

ipcMain.handle('pet:relay', (_e, msg) => {
  if (!msg) return false
  if (msg.kind === 'lists') {
    if (ctrlWin) ctrlWin.webContents.send('lists', msg.data)
    return true
  }
  if (modelWin) modelWin.webContents.send('relay', msg)
  return true
})

ipcMain.handle('pet:lists', (_e, data) => {
  if (ctrlWin) ctrlWin.webContents.send('lists', data)
  return true
})

ipcMain.handle('pet:getState', () => ({
  model: modelWin ? modelWin.getBounds() : null,
  ctrl: ctrlWin ? ctrlWin.getBounds() : null,
  passthrough: modelWin ? !!modelWin.__passthrough : false,
}))

app.whenReady().then(() => {
  createModelWindow()
  createControlWindow()

  const regF8 = globalShortcut.register('F8', () => {
    if (!modelWin) return
    const on = !!modelWin.__passthrough
    modelWin.__passthrough = !on
    modelWin.setIgnoreMouseEvents(!on, { forward: true })
    log('[hotkey] F8 ->', !on ? '穿透' : '接管')
  })
  log('register F8 ->', regF8 ? 'ok' : 'FAILED')

  const regF9 = globalShortcut.register('F9', () => {
    if (!modelWin) return
    const show = !modelWin.isVisible()
    show ? (modelWin.show(), ctrlWin && ctrlWin.show()) : (modelWin.hide(), ctrlWin && ctrlWin.hide())
    log('[hotkey] F9 ->', show ? 'show' : 'hide')
  })
  log('register F9 ->', regF9 ? 'ok' : 'FAILED')

  startControlServer()
})

app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit() })
app.on('will-quit', () => globalShortcut.unregisterAll())