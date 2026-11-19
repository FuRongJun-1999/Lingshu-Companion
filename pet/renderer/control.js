// control.js · 控制窗口（齿轮 + 面板）
//
// 功能名：桌宠控制面板
// 生效条件：主进程已开 8791 控制端点；模型窗口存在
// 子功能：齿轮开关面板 / 窗口大小 / 模型缩放 / 表情 / 动作 / 帧率 / 眨眼 / 穿透 / 隐藏
// 执行：随 control.html 加载
// 验证方式：拖窗口大小滑块，模型窗口改变而本窗口不变
// 不适用条件：
//   - 表情/动作列表由模型窗口上报，未上报时下拉为空
//   - 配置转发依赖 preload 暴露的 relay

const API = 'http://127.0.0.1:8791'

const gear = document.getElementById('gear')
const panel = document.getElementById('panel')

// 面板展开/收起时同步伸缩控制窗口：收起只剩齿轮大小，尽量减少遮挡
const SIZE_CLOSED = { w: 48, h: 48 }
const SIZE_OPEN = { w: 292, h: 588 }   // 加了四个主功能入口后需要更宽更高

async function resizeCtrl(open) {
  const s = open ? SIZE_OPEN : SIZE_CLOSED
  try { await fetch(API + '/ctrlsize?w=' + s.w + '&h=' + s.h, { cache: 'no-store' }) } catch {}
}

// 齿轮：手动拖动移动控制窗口；未拖动则视为点击
let gDrag = null
let gMoved = false
gear.addEventListener('mousedown', (e) => {
  if (e.button !== 0) return
  gDrag = { x: e.screenX, y: e.screenY }
  gMoved = false
  e.preventDefault()
})
window.addEventListener('mousemove', (e) => {
  if (!gDrag) return
  const dx = e.screenX - gDrag.x
  const dy = e.screenY - gDrag.y
  if (dx || dy) {
    gMoved = true
    gDrag.x = e.screenX
    gDrag.y = e.screenY
    try { window.pet && window.pet.moveCtrlBy(dx, dy) } catch {}
  }
})
window.addEventListener('mouseup', () => {
  if (!gDrag) return
  gDrag = null
  if (gMoved) return                    // 拖动过就不算点击
  const open = panel.classList.toggle('open')
  gear.classList.toggle('on', open)
  resizeCtrl(open)
})

// ── 与主进程/模型窗口通信 ──
function relay(kind, data) {
  try { window.pet && window.pet.relay({ kind, data }) } catch {}
}

async function api(path) {
  try {
    const r = await fetch(API + path, { cache: 'no-store' })
    return await r.json()
  } catch { return null }
}

// ── 窗口大小（只改模型窗口）──
const pWin = document.getElementById('pWin')
const vWin = document.getElementById('vWin')
let winTimer = null

async function syncWinSize() {
  const st = await api('/state')
  if (st && st.model) {
    const h = Math.round(Math.min(st.model.width, st.model.height))
    pWin.value = h
    vWin.textContent = h
  }
}
pWin.addEventListener('input', () => {
  const v = Number(pWin.value)
  vWin.textContent = v
  clearTimeout(winTimer)
  winTimer = setTimeout(() => { api('/fit?w=' + v + '&h=' + v) }, 120)
})

// ── 模型缩放（转发给模型窗口）──
const pZoom = document.getElementById('pZoom')
const vZoom = document.getElementById('vZoom')
pZoom.value = Math.round(1.6 * 100)
vZoom.textContent = '160%'
pZoom.addEventListener('input', () => {
  const z = Number(pZoom.value) / 100
  vZoom.textContent = pZoom.value + '%'
  relay('zoom', { zoom: z })
})

// ── 表情 / 动作 ──
const selExpr = document.getElementById('pExpr')
const selMotion = document.getElementById('pMotion')

selExpr.addEventListener('change', () => {
  if (selExpr.value) relay('cmd', { type: 'expr', name: selExpr.value })
})
selMotion.addEventListener('change', () => {
  if (!selMotion.value) return
  const [g, i] = selMotion.value.split('|')
  relay('cmd', { type: 'motion', group: g, index: Number(i) })
})

// 模型窗口上报列表
function applyLists(data) {
  const { expressions = [], motions = [] } = data || {}
  selExpr.innerHTML = '<option value="">（不切换）</option>'
  for (const n of expressions) {
    const o = document.createElement('option'); o.value = n; o.textContent = n
    selExpr.appendChild(o)
  }
  selMotion.innerHTML = '<option value="">（不播放）</option>'
  for (const m of motions) {
    const o = document.createElement('option'); o.value = m.value; o.textContent = m.label
    selMotion.appendChild(o)
  }
}
try { window.pet && window.pet.onLists(applyLists) } catch {}

// ── 帧率 / 眨眼 ──
const pFps = document.getElementById('pFps')
pFps.addEventListener('change', () => relay('cfg', { fps: Number(pFps.value) }))

const pBlink = document.getElementById('pBlink')
pBlink.addEventListener('change', () => relay('cfg', { blink: pBlink.checked }))

// ── 镜头预设：全身 / 上半身 / 特写 ──
const VIEWS = {
  全身:   { zoom: 1.00, viewY: 0.00 },
  上半身: { zoom: 1.60, viewY: 0.62 },
  特写:   { zoom: 2.20, viewY: 0.80 },
}
function applyView(name) {
  const v = VIEWS[name]
  if (!v) return
  relay('cfg', { zoom: v.zoom, viewY: v.viewY })
  pZoom.value = Math.round(v.zoom * 100)
  vZoom.textContent = pZoom.value + '%'
  hint('镜头：' + name)
}
for (const [id, name] of [['btnFull','全身'], ['btnBust','上半身'], ['btnClose','特写']]) {
  const el = document.getElementById(id)
  if (el) el.addEventListener('click', () => applyView(name))
}


// ── 主功能入口 ──
const OPEN = (path, params) => {
  const q = params ? '?' + new URLSearchParams(params) : ''
  fetch('http://127.0.0.1:8791' + path + q).catch(() => {})
}

// 聊天：打开对话页面（看 + 发）
const btnChat = document.getElementById('btnChat')
if (btnChat) btnChat.addEventListener('click', () => {
  OPEN('/open', { url: 'http://127.0.0.1:8792' })
  hint('已打开对话页面')
})

// 语音：切换语音对话开关
let voiceOn = false
const btnVoice = document.getElementById('btnVoice')
if (btnVoice) btnVoice.addEventListener('click', async () => {
  voiceOn = !voiceOn
  btnVoice.classList.toggle('on', voiceOn)
  try {
    const r = await fetch('http://127.0.0.1:8791/voice?on=' + (voiceOn ? 1 : 0))
    const d = await r.json()
    if (!d.ok) {
      voiceOn = false
      btnVoice.classList.remove('on')
      hint('语音不可用：' + (d.error || '未配置语音识别'))
    } else {
      hint('语音对话已' + (voiceOn ? '开启' : '关闭'))
    }
  } catch (e) {
    voiceOn = false; btnVoice.classList.remove('on')
    hint('语音切换失败')
  }
})

// 详情：打开服务配置
const btnConfig = document.getElementById('btnConfig')
if (btnConfig) btnConfig.addEventListener('click', () => {
  OPEN('/open', { url: 'http://127.0.0.1:8793' })
  hint('已打开服务配置')
})

// 模型：打开 Live2D 控制页
const btnModel = document.getElementById('btnModel')
if (btnModel) btnModel.addEventListener('click', () => {
  OPEN('/open', { url: 'http://127.0.0.1:8795' })
  hint('已打开模型控制')
})

// ── 穿透 / 隐藏 ──
function hint(t) {
  const el = document.getElementById('hint')
  if (el) el.textContent = t
}
document.getElementById('btnPass').addEventListener('click', async () => {
  const st = await api('/state')
  const on = !(st && st.passthrough)
  await api('/passthrough?on=' + (on ? 1 : 0))
  hint(on ? '穿透已开（按 F8 恢复）' : '穿透已关')
})
document.getElementById('btnHide').addEventListener('click', () => api('/hide'))

// 编辑模式：模型窗口可用左键拖动、中键缩放
let editing = false
const btnEdit = document.getElementById('btnEdit')
if (btnEdit) {
  btnEdit.addEventListener('click', () => {
    editing = !editing
    btnEdit.textContent = editing ? '退出编辑' : '编辑模式'
    btnEdit.style.background = editing ? 'rgba(80,140,90,.95)' : ''
    try { window.pet && window.pet.setEditMode(editing) } catch {}
    hint(editing ? '编辑中：左键拖模型，中键滚轮缩放' : '已退出编辑')
  })
}

// ── 初始化 ──
// 初始化收缩：启动时只显示齿轮
setTimeout(() => resizeCtrl(false), 300)
syncWinSize()
setInterval(syncWinSize, 3000)
