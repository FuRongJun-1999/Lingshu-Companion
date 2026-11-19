// app.js · 桌宠渲染与操控面板
//
// 功能名：Live2D 桌宠渲染 + 可视化操控
// 生效条件：pixi + cubism4 + CubismCore 已加载；模型服务 8770 可达
// 子功能：加载模型 / 缩放 / 位置偏移 / 表情 / 动作 / 帧率 / 眨眼 / 待机动作 /
//         指令消费（姿态·表情·动作）/ 设置持久化
// 执行：随 index.html 加载
// 验证方式：面板滑块改变模型大小与位置；表情下拉切换生效；设置重启后保持
// 不适用条件：
//   - 动作/表情列表来自模型定义，模型换了列表随之变化
//   - 帧率 0 表示不限，实际受显示器刷新率约束

const BRIDGE = 'http://127.0.0.1:8789'
const MODEL_URL = 'http://127.0.0.1:8770/model/示例模型.model3.json'
const LS_KEY = 'pet.control.v1'

let model = null
let app = null
let lastSeq = 0
let failures = 0
const BACKOFF = [500, 2000, 8000, 30000]

// ── 设置状态（持久化）──
const DEFAULTS = { zoom: 1.0, winSize: 0, fps: 60, blink: true, idle: true }
let S = { ...DEFAULTS }

function loadSettings() {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) S = { ...DEFAULTS, ...JSON.parse(raw) }
  } catch {}
}
function saveSettings() {
  try { localStorage.setItem(LS_KEY, JSON.stringify(S)) } catch {}
}

function hud(text) {
  const el = document.getElementById('hud')
  if (el) el.textContent = text
  try { document.title = 'pet|' + text } catch {}
}

// ── 布局 ──
function applyTransform() {
  if (!model) return
  model.scale.set(1)
  const base = Math.min(
    (window.innerWidth * 0.95) / model.width,
    (window.innerHeight * 0.98) / model.height,
  )
  model.scale.set(base * S.zoom)
  model.x = (window.innerWidth - model.width) * 0.5
  model.y = window.innerHeight - model.height
}

// ── 启动 ──
async function boot() {
  if (typeof PIXI === 'undefined') throw new Error('PIXI 未加载')
  if (!PIXI.live2d) throw new Error('PIXI.live2d 未注册（cubism4 未加载）')
  hud('PIXI 就绪')

  loadSettings()

  app = new PIXI.Application({
    backgroundAlpha: 0,
    antialias: false,
    autoDensity: true,
    premultipliedAlpha: false,
    preserveDrawingBuffer: true,   // 允许 gl.readPixels 读当前帧做命中判定
    resolution: window.devicePixelRatio || 1,
    resizeTo: window,
  })
  const view = app.view
  view.style.pointerEvents = 'none'
  document.getElementById('stage').appendChild(view)

  hud('请求模型…')
  model = await PIXI.live2d.Live2DModel.from(MODEL_URL, { autoInteract: false })
  app.stage.addChild(model)

  // 模型自带冗余部件：Part10 与 Part46 同名「鼻子」，
  // Part46 的 drawable 渲染成黑块，隐藏它。—— 2026-11-15 实测
  try {
    const core = model.internalModel.coreModel
    for (const id of ['Part46']) {
      try { core.setPartOpacityById(id, 0) } catch {}
    }
  } catch {}

  applyTransform()
  window.addEventListener('resize', applyTransform)

  // 恢复上次窗口大小（有记录且与当前差异明显时才调）
  if (S.winSize && window.pet) {
    const cur = Math.round(Math.min(window.innerWidth, window.innerHeight))
    if (Math.abs(cur - S.winSize) > 20) {
      try { await window.pet.fitWindow({ width: S.winSize, height: S.winSize }) } catch {}
    }
  }

  applyFps()
  applyBlink()

  initPanel()
  hud('已加载 · 点右上角 ⚙ 打开操控面板')

  poll()
  openMouthStream()
}

// ── 帧率 / 眨眼 ──
function applyFps() {
  if (!app) return
  try { app.ticker.maxFPS = Number(S.fps) || 0 } catch {}
}
function applyBlink() {
  if (!model) return
  try {
    const im = model.internalModel
    if (im.motionManager && im.motionManager.eyeBlink) {
      im.motionManager.eyeBlink.enabled = !!S.blink
      // 老版本 API
      const eb = im.motionManager.eyeBlink._enabled
      if (eb !== undefined) im.motionManager.eyeBlink._enabled = !!S.blink
    }
  } catch {}
}

// ── 操控面板 ──
function initPanel() {
  const gear = document.getElementById('gear')
  const panel = document.getElementById('panel')

  // ── 齿轮：可拖动 + 点击开关面板 ──
  try {
    const gp = JSON.parse(localStorage.getItem('pet.gearPos') || 'null')
    if (gp && Number.isFinite(gp.left) && Number.isFinite(gp.top)) {
      gear.style.left = gp.left + 'px'
      gear.style.top = gp.top + 'px'
      gear.style.right = 'auto'
    }
  } catch {}

  let gearDrag = null
  let gearMoved = false
  gear.addEventListener('mousedown', (e) => {
    const r = gear.getBoundingClientRect()
    gearDrag = { dx: e.clientX - r.left, dy: e.clientY - r.top }
    gearMoved = false
    e.preventDefault()
  })
  window.addEventListener('mousemove', (e) => {
    if (!gearDrag) return
    gearMoved = true
    const left = Math.max(0, Math.min(window.innerWidth - gear.offsetWidth, e.clientX - gearDrag.dx))
    const top = Math.max(0, Math.min(window.innerHeight - gear.offsetHeight, e.clientY - gearDrag.dy))
    gear.style.left = left + 'px'
    gear.style.top = top + 'px'
    gear.style.right = 'auto'
  })
  window.addEventListener('mouseup', () => {
    if (!gearDrag) return
    gearDrag = null
    localStorage.setItem('pet.gearPos', JSON.stringify({
      left: gear.offsetLeft, top: gear.offsetTop,
    }))
    // 拖动过就不算点击
    if (gearMoved) { gearMoved = false; return }
    panel.classList.toggle('open')
    gear.classList.toggle('on', panel.classList.contains('open'))
  })

  // ── 面板：可拖动 ──
  try {
    const pos = JSON.parse(localStorage.getItem('pet.panelPos') || 'null')
    if (pos && Number.isFinite(pos.left) && Number.isFinite(pos.top)) {
      panel.style.left = pos.left + 'px'
      panel.style.top = pos.top + 'px'
    }
  } catch {}
  const handle = panel.querySelector('h4')
  let pDrag = null
  handle.addEventListener('mousedown', (e) => {
    const r = panel.getBoundingClientRect()
    pDrag = { dx: e.clientX - r.left, dy: e.clientY - r.top }
    panel.style.left = r.left + 'px'
    panel.style.top = r.top + 'px'
    e.preventDefault()
  })
  window.addEventListener('mousemove', (e) => {
    if (!pDrag) return
    const left = Math.max(0, Math.min(window.innerWidth - panel.offsetWidth, e.clientX - pDrag.dx))
    const top = Math.max(0, Math.min(window.innerHeight - panel.offsetHeight, e.clientY - pDrag.dy))
    panel.style.left = left + 'px'
    panel.style.top = top + 'px'
  })
  window.addEventListener('mouseup', () => {
    if (!pDrag) return
    pDrag = null
    localStorage.setItem('pet.panelPos', JSON.stringify({
      left: panel.offsetLeft, top: panel.offsetTop,
    }))
  })

  // ── 窗口大小（直接改窗口，不是挪模型）──
  const pWin = document.getElementById('pWin')
  const vWin = document.getElementById('vWin')
  const cur = Math.round(Math.min(window.innerWidth, window.innerHeight))
  pWin.value = cur
  vWin.textContent = cur
  let winTimer = null
  pWin.addEventListener('input', () => {
    const v = Number(pWin.value)
    vWin.textContent = v
    clearTimeout(winTimer)
    winTimer = setTimeout(async () => {
      if (!window.pet) return
      await window.pet.fitWindow({ width: v, height: v })
      S.winSize = v
      saveSettings()
      applyTransform()
    }, 120)
  })

  // ── 模型缩放（窗口内的相对大小）──
  const pZoom = document.getElementById('pZoom')
  const vZoom = document.getElementById('vZoom')
  pZoom.value = Math.round(S.zoom * 100)
  vZoom.textContent = pZoom.value + '%'
  pZoom.addEventListener('input', () => {
    S.zoom = Number(pZoom.value) / 100
    vZoom.textContent = pZoom.value + '%'
    applyTransform(); saveSettings()
  })

  // ── 表情 / 动作 ──
  const selExpr = document.getElementById('pExpr')
  try {
    const defs = (model.internalModel.motionManager.expressionManager
                  && model.internalModel.motionManager.expressionManager.definitions) || []
    for (const d of defs) {
      const name = d.Name || d.name
      if (!name) continue
      const o = document.createElement('option')
      o.value = name; o.textContent = name
      selExpr.appendChild(o)
    }
  } catch {}
  selExpr.addEventListener('change', () => { if (selExpr.value) setExpression(selExpr.value) })

  const selMotion = document.getElementById('pMotion')
  try {
    const defs = model.internalModel.motionManager.definitions || {}
    for (const group of Object.keys(defs)) {
      (defs[group] || []).forEach((_, i) => {
        const o = document.createElement('option')
        o.value = group + '|' + i
        o.textContent = group + ' #' + i
        selMotion.appendChild(o)
      })
    }
  } catch {}
  selMotion.addEventListener('change', () => {
    if (!selMotion.value) return
    const [g, i] = selMotion.value.split('|')
    playMotion(g, Number(i))
  })

  // ── 帧率 / 眨眼 / 待机 ──
  const fps = document.getElementById('pFps')
  fps.value = String(S.fps)
  fps.addEventListener('change', () => {
    S.fps = Number(fps.value); applyFps(); saveSettings()
  })

  const blink = document.getElementById('pBlink')
  blink.checked = !!S.blink
  blink.addEventListener('change', () => {
    S.blink = blink.checked; applyBlink(); saveSettings()
  })

  const idle = document.getElementById('pIdle')
  idle.checked = !!S.idle
  idle.addEventListener('change', () => {
    S.idle = idle.checked; saveSettings()
    if (!S.idle) { try { model.internalModel.motionManager.stopAllMotions() } catch {} }
  })

  // ── 复位 / 穿透 ──
  document.getElementById('btnReset').addEventListener('click', () => {
    S.zoom = 1.0
    pZoom.value = 100; vZoom.textContent = '100%'
    applyTransform(); saveSettings()
    hud('缩放已复位')
  })

  document.getElementById('btnPass').addEventListener('click', async () => {
    if (!window.pet) return
    const on = !window.__pass
    window.__pass = on
    await window.pet.setClickThrough(on)
    hud(on ? '穿透已开 —— 按 F8 恢复' : '穿透已关')
  })
}

// ── 指令 ──
function setExpression(name) {
  if (!model) return false
  try {
    model.expression(name)
    hud('表情→ ' + name)
    return true
  } catch (e) { hud('表情失败: ' + name); return false }
}

function playMotion(group, index) {
  if (!model) return false
  try {
    model.motion(group, index)
    hud('动作 ' + group + '#' + index)
    return true
  } catch (e) { return false }
}

let poseTimer = null
const POSE_DEFAULT = {}
function applyPose(params, holdMs) {
  if (!model) return false
  const core = model.internalModel.coreModel
  const keys = Object.keys(params || {})
  if (!keys.length) return false
  for (const k of keys) {
    try {
      if (!(k in POSE_DEFAULT)) {
        try { POSE_DEFAULT[k] = core.getParameterValueById(k) } catch { POSE_DEFAULT[k] = 0 }
      }
      core.setParameterValueById(k, Number(params[k]))
    } catch {}
  }
  hud('姿态 ' + keys.join(','))
  clearTimeout(poseTimer)
  poseTimer = setTimeout(() => {
    for (const k of keys) {
      try { core.setParameterValueById(k, POSE_DEFAULT[k] || 0) } catch {}
    }
  }, Math.max(300, holdMs || 700))
  return true
}

function setMouth(v) {
  if (!model) return
  try {
    model.internalModel.coreModel.setParameterValueById('ParamMouthOpenY',
      Math.max(0, Math.min(1, v)))
  } catch {}
}

// ── 动态穿透：只有鼠标真的落在模型或面板上时才接管 ──
let clickThrough = true

/**
 * 读画布该点的 alpha。
 * Live2D 的透明区域 alpha=0，只有真正画出来的像素才 >0。
 * 比包围盒精确：手臂之间的空隙也能穿透。
 */
const _px = new Uint8Array(4)
function alphaAt(x, y) {
  try {
    const gl = app.renderer.gl
    const dpr = window.devicePixelRatio || 1
    const gx = Math.floor(x * dpr)
    const gy = Math.floor((window.innerHeight - y) * dpr)
    if (gx < 0 || gy < 0 || gx >= gl.drawingBufferWidth || gy >= gl.drawingBufferHeight) return 0
    gl.readPixels(gx, gy, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, _px)
    return _px[3]
  } catch { return 0 }
}

let lastHitAt = 0
function updateHitTest(x, y) {
  if (!model) return
  const now = performance.now()
  if (now - lastHitAt < 40) return      // 节流，readPixels 不便宜
  lastHitAt = now

  // 面板/齿轮优先（DOM 层，不需要读像素）
  let onUI = false
  try {
    const el = document.elementFromPoint(x, y)
    onUI = !!(el && el.closest && el.closest('#panel, #gear'))
  } catch {}

  // 像素 alpha：>8 认为落在她身上
  const onModel = !onUI && alphaAt(x, y) > 8

  const ignore = !(onModel || onUI)
  if (ignore !== clickThrough) {
    clickThrough = ignore
    try { window.pet && window.pet.setClickThrough(ignore) } catch {}
  }
}

// 悬停判定在 Windows 上不可用（穿透时收不到 mousemove），此监听仅保留拖动等用途
window.addEventListener('mousemove', () => {})
// 鼠标离开窗口时恢复穿透，避免卡在"接管"状态
window.addEventListener('mouseleave', () => {
  if (!clickThrough) {
    clickThrough = true
    try { window.pet && window.pet.setClickThrough(true) } catch {}
  }
})

// ── 轮询 ──
function schedulePoll(d) { setTimeout(pollOnce, d) }
async function pollOnce() {
  try {
    const res = await fetch(BRIDGE + '/cmd', { cache: 'no-store' })
    failures = 0
    if (!res.ok) return schedulePoll(BACKOFF[0])
    const cmd = await res.json()
    if (cmd && cmd.seq !== lastSeq) {
      lastSeq = cmd.seq
      if (cmd.kind === 'motion') playMotion(cmd.group || 'Gesture', cmd.index || 0)
      else if (cmd.kind === 'pose' && cmd.params) applyPose(cmd.params, cmd.ms)
      else if (typeof cmd.name === 'string' && cmd.name) setExpression(cmd.name)
    }
    schedulePoll(BACKOFF[0])
  } catch (e) {
    const d = BACKOFF[Math.min(failures, BACKOFF.length - 1)]
    failures++
    if (failures <= 3) hud('桥不可达(' + failures + ')')
    schedulePoll(d)
  }
}
function poll() { schedulePoll(0) }

function openMouthStream() {
  try {
    const es = new EventSource(BRIDGE + '/mouth-stream')
    es.addEventListener('message', ev => {
      const v = Number(ev.data)
      if (Number.isFinite(v)) setMouth(v)
    })
    es.addEventListener('error', () => { es.close(); setTimeout(openMouthStream, 3000) })
  } catch {}
}

// ── 滚轮缩放（保留快捷操作）──
window.addEventListener('wheel', (e) => {
  if (!e.ctrlKey) return
  e.preventDefault()
  S.zoom = Math.max(0.3, Math.min(3.0, S.zoom * (e.deltaY > 0 ? 0.92 : 1.08)))
  const z = document.getElementById('pZoom')
  const vz = document.getElementById('vZoom')
  if (z) { z.value = Math.round(S.zoom * 100); if (vz) vz.textContent = z.value + '%' }
  applyTransform(); saveSettings()
  hud('缩放 ' + Math.round(S.zoom * 100) + '%')
}, { passive: false })

window.addEventListener('error', e => hud('JS错误:' + (e.message || '?')))
window.addEventListener('unhandledrejection', e => hud('Promise:' + (e.reason && e.reason.message || '?')))

window.addEventListener('DOMContentLoaded', () => {
  boot().catch(err => {
    hud('加载失败: ' + (err && err.message ? err.message : String(err)))
    console.error('boot failed', err)
  })
})
