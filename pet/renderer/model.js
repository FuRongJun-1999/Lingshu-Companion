// model.js · 模型窗口（只渲染，无 UI）
//
// 功能名：Live2D 渲染与指令消费
// 生效条件：pixi + cubism4 + CubismCore 已加载；模型服务 8770
// 子功能：加载模型 / 隐藏冗余部件 / 缩放适配 / 桥指令（姿态·表情·动作）/ 口型 SSE / 配置热更新
// 执行：随 model.html 加载
// 验证方式：模型显示且背景透明；桥发 /pose 时姿态变化
// 不适用条件：
//   - 本窗口无 UI；所有操作在控制窗口
//   - 配置来自控制窗口，通过 DOM 自定义事件 relay-cfg 传入

const BRIDGE = 'http://127.0.0.1:8789'
const MODEL_URL = 'http://127.0.0.1:8770/model/示例模型.model3.json'

let model = null
let app = null
let lastSeq = 0
let failures = 0
const BACKOFF = [500, 2000, 8000, 30000]

// 默认上半身构图（直播用）；可在控制面板切全身/特写
const CFG = { zoom: 1.6, viewY: 0.62, fps: 60, blink: true, idle: true }

// ── 帧率策略与时间基准 ──
// 降帧上限始终尊重用户配置的 CFG.fps（0 表示不限）；口型/指令活跃期间保活高帧率，
// 避免直播窗口失焦后立刻陷入 8fps 导致口型/表情迟滞。
const FPS_BLUR = 20
const FPS_IDLE = 8
const ACTIVE_HOLD_MS = 3000
const IDLE_AFTER_MS = 30000
const NOMINAL_FRAME_MS = 1000 / 60
const MAX_DT_MS = 250

let curFps = CFG.fps
let lastAct = 0
let activeUntil = 0

function userFpsCap() {
  if (CFG.fps == null || CFG.fps === '') return 60
  const v = Number(CFG.fps)
  return Number.isFinite(v) && v >= 0 ? v : 60
}

function parseOptionalMs(v) {
  if (v == null || v === '') return NaN
  const n = Number(v)
  return Number.isFinite(n) ? n : NaN
}

function targetFpsForState(now = performance.now()) {
  const cap = userFpsCap()
  const focused = typeof document !== 'undefined' && typeof document.hasFocus === 'function'
    ? document.hasFocus()
    : true
  if (focused || now < activeUntil) return cap === 0 ? 0 : cap
  const target = (now - lastAct) > IDLE_AFTER_MS ? FPS_IDLE : FPS_BLUR
  return cap === 0 ? target : Math.min(cap, target)
}

function setFps(n) {
  // 不能只比较 curFps：ticker.maxFPS 可能被外部改过（调试/CFG 重设），
  // 那样 curFps 相同就永远修不回来。两边都对齐才算无事可做。
  if (curFps === n) {
    try { if (app && app.ticker && app.ticker.maxFPS !== n) app.ticker.maxFPS = n } catch {}
    return
  }
  curFps = n
  try { if (app && app.ticker) app.ticker.maxFPS = n } catch {}
  report('fps ' + n)
}

function syncFps(now = performance.now()) {
  setFps(targetFpsForState(now))
}

function markActivity(holdMs = ACTIVE_HOLD_MS, now = performance.now()) {
  lastAct = Math.max(lastAct, now)
  if (holdMs > 0) activeUntil = Math.max(activeUntil, now + holdMs)
  syncFps(now)
}

function stepDtMs(now, lastTs) {
  if (!lastTs) return NOMINAL_FRAME_MS
  const dt = now - lastTs
  if (!(dt > 0)) return 0
  return Math.min(dt, MAX_DT_MS)
}

function expAlpha(dtMs, tauMs) {
  if (!(dtMs > 0) || !(tauMs > 0)) return 0
  return 1 - Math.exp(-dtMs / tauMs)
}

function report(text) {
  try { document.title = 'model|' + text } catch {}
}

function applyTransform() {
  if (!model) return
  model.scale.set(1)
  const base = Math.min(
    (window.innerWidth * 0.98) / model.width,
    (window.innerHeight * 0.99) / model.height,
  )
  model.scale.set(base * CFG.zoom)
  model.x = (window.innerWidth - model.width) * 0.5
  // viewY: 0 = 底边对齐窗口底（全身可见）；1 = 顶边对齐窗口顶（露头部）
  // 中间值线性插值，0.6 约等于上半身构图
  const bottomY = window.innerHeight - model.height
  model.y = bottomY * (1 - CFG.viewY)
}

function applyFps() { syncFps(performance.now()) }

function applyBlink() {
  if (!model) return
  try {
    const mm = model.internalModel.motionManager
    if (mm && mm.eyeBlink) {
      mm.eyeBlink.enabled = !!CFG.blink
      if (mm.eyeBlink._enabled !== undefined) mm.eyeBlink._enabled = !!CFG.blink
    }
  } catch {}
}

async function boot() {
  if (typeof PIXI === 'undefined') throw new Error('PIXI 未加载')
  if (!PIXI.live2d) throw new Error('PIXI.live2d 未注册')

  app = new PIXI.Application({
    backgroundAlpha: 0,
    antialias: false,
    autoDensity: true,
    premultipliedAlpha: false,
    resolution: window.devicePixelRatio || 1,
    resizeTo: window,
  })
  const view = app.view
  view.style.pointerEvents = 'none'
  document.getElementById('stage').appendChild(view)

  report('请求模型')
  model = await PIXI.live2d.Live2DModel.from(MODEL_URL, { autoInteract: false })
  app.stage.addChild(model)

  // 模型自带冗余部件：Part10 与 Part46 同名「鼻子」，Part46 渲染成黑块
  try {
    model.internalModel.coreModel.setPartOpacityById('Part46', 0)
  } catch {}

  applyTransform()
  lastAct = performance.now()
  applyFps()
  applyBlink()
  window.addEventListener('resize', applyTransform)
  report('就绪')

  // 上报表情/动作列表给控制窗口
  try {
    const mm2 = model.internalModel.motionManager
    const exprs = ((mm2.expressionManager && mm2.expressionManager.definitions) || [])
      .map(d => d.Name || d.name).filter(Boolean)
    const motions = []
    const defs = mm2.definitions || {}
    for (const g of Object.keys(defs)) {
      (defs[g] || []).forEach((_, i) => {
        motions.push({ value: g + '|' + i, label: g + ' #' + i })
      })
    }
    window.pet && window.pet.reportLists({ expressions: exprs, motions })
  } catch {}

  // 接收控制窗口转发的配置/指令
  try {
    window.pet && window.pet.onRelay((msg) => {
      if (!msg) return
      window.dispatchEvent(new CustomEvent('relay-' + msg.kind, { detail: msg.data }))
    })
  } catch {}

  // 关掉物理与呼吸：两者都会每帧覆盖 ParamAngleX/Y/Z。
  // 物理覆盖头/耳/手/身体（292 个参数），呼吸覆盖头部角度。
  if (PHYSICS_OFF) {
    try {
      const im0 = model.internalModel
      if (im0.physics) im0.physics = null
      if (im0.breath) im0.breath = null
      // lipSync 会独占 ParamMouthOpenY（写后立刻读回 0），关掉改用我们自己的包络
      if (im0.lipSync) im0.lipSync = null
      report('物理+呼吸+lipSync 已关（换取完全控制）')
    } catch (e) { report('关闭失败:' + e.message) }
  }

  // 姿态注入点：包装 updateNaturalMovements。
  // update() 的顺序是 updateFocus → updateNaturalMovements（idle motion 在这写参数）
  // → physics → draw。放在 ticker 里会被 idle motion 覆盖，必须插在它之后。
  try {
    const im = model.internalModel
    // 必须挂在整个 update 之后：内部 coreModel.update() 会把没有
    // motion / expression 驱动的参数重置回默认，之前的写入都会被抹掉。
    const origUpdate = im.update.bind(im)
    im.update = function (dt, now) {
      origUpdate(dt, now)
      const t = performance.now()
      try { pumpPose() } catch {}
      try { idleTick(t) } catch {}
      try { pumpMouth(t) } catch {}
    }
    report('姿态注入已挂载(update 之后)')
  } catch (e) {
    report('注入挂载失败:' + e.message)
  }

  // 调试入口：可通过控制窗口 eval 调用
  window.__poseTest = (params, stopIdle) => {
    const core = model.internalModel.coreModel
    const mm = model.internalModel.motionManager
    if (stopIdle) {
      try { mm.stopAllMotions() } catch {}
      try { mm.groups.idle } catch {}
    }
    for (const [k, v] of Object.entries(params || {})) {
      try { core.setParameterValueById(k, Number(v)) } catch (e) { return 'fail:' + k }
    }
    return 'ok'
  }
  window.__stopIdle = () => {
    try {
      const mm = model.internalModel.motionManager
      mm.stopAllMotions()
      // 清空 idle 组，防止自动重播
      if (mm.groups && mm.groups.idle !== undefined) mm.groups.idle = undefined
      return 'stopped'
    } catch (e) { return 'err:' + e.message }
  }
  window.__paramInfo = (p) => {
    try {
      const core = model.internalModel.coreModel
      return {
        value: core.getParameterValueById(p),
        default: core.getParameterDefaultValueById ? core.getParameterDefaultValueById(p) : null,
        max: core.getParameterMaximumValueById ? core.getParameterMaximumValueById(p) : null,
        min: core.getParameterMinimumValueById ? core.getParameterMinimumValueById(p) : null,
      }
    } catch (e) { return 'err:' + e.message }
  }


  // ── 帧率策略：按焦点与空闲程度降帧，长期挂机不吃 CPU ──
  // 实测结论：Electron 的 document.visibilityState 对「被别的窗口遮挡」不敏感
  //（遮挡时仍是 visible），只有最小化/隐藏才变 hidden。
  // 所以降帧依据是 focus / hidden / 空闲时长，同时尊重用户设置的 CFG.fps 上限。
  // 暴露给调试与外部验证（也是真实使用中降帧策略的唯一入口）
  window.__fps = {
    set: setFps,
    get: () => ({ cur: curFps, max: app.ticker.maxFPS, started: app.ticker.started, cfg: CFG.fps }),
  }

  window.addEventListener('focus', () => {
    lastAct = performance.now()
    // 保险：任何一次 focus 都确保 ticker 在跑，防止 visibilitychange 停掉后再不恢复
    try { if (!app.ticker.started) app.ticker.start() } catch {}
    syncFps(lastAct)
  })
  window.addEventListener('blur', () => syncFps())
  for (const ev of ['mousemove', 'mousedown', 'wheel', 'keydown']) {
    window.addEventListener(ev, () => { lastAct = performance.now() }, { passive: true })
  }

  // 空闲降帧：失焦且超过 30 秒无窗口交互/口型/指令活动就降到 8fps（上限仍受 CFG.fps 约束）
  setInterval(() => {
    if (!document.hasFocus()) {
      syncFps()
    }
  }, 5000)

  // 最小化/隐藏：彻底停 ticker（这时 visibilityState 才变 hidden）
  // 注意：Electron 里窗口被别的窗口遮挡时 hidden 不会变 true（只有最小化才变），
  // 所以这里只处理最小化，停 ticker 后由 focus / 恢复事件负责重启。
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      try { app.ticker.stop() } catch {}
    } else {
      try { app.ticker.start() } catch {}
      lastAct = performance.now()
      syncFps(lastAct)
    }
  })

  // 兜底：每 10 秒检查一次 ticker 是否意外停住（隐藏期间被 stop 但没收到恢复事件）
  setInterval(() => {
    try {
      if (!document.hidden && !app.ticker.started) {
        app.ticker.start()
        syncFps()
      }
    } catch {}
  }, 10000)

  poll()
  openMouthStream()
}

// ── 指令 ──
const EXTERNAL_EXPR_HOLD_MS = 6000  // 外部下发表情保护窗口（避免台词说到一半被待机覆盖）
const SPEAKING_HOLD_MS = 1500       // 口型活跃后的说话态驻留窗口
let externalExprUntil = 0
let speakingUntil = 0

function setExpression(name, opts) {
  if (!model) return false
  const isIdle = !!(opts && opts.fromIdle)
  try {
    model.expression(name)
    report('表情 ' + name)
    if (!isIdle) {
      const now = performance.now()
      const parsedHold = parseOptionalMs(opts && opts.holdMs)
      const holdMs = Number.isFinite(parsedHold) && parsedHold >= 0
        ? parsedHold
        : EXTERNAL_EXPR_HOLD_MS
      idleActivity.expr = null
      externalExprUntil = Math.max(externalExprUntil, now + holdMs)
      idleActivity.nextExprAt = Math.max(idleActivity.nextExprAt, externalExprUntil + rand(4000, 8000))
    }
    return true
  } catch {
    return false
  }
}
function playMotion(group, index) {
  if (!model) return false
  try { model.motion(group, index); report('动作 ' + group + '#' + index); return true }
  catch { return false }
}

// ── 物理开关 ──
// 这个模型 57% 的参数（含头 ParamAngle、耳 LEar、手 LArm、身体 ParamBodyAngle）
// 都是 physics3 的输出，物理开着时写不进去。
// 关掉物理换取完全控制，代价是头发/裙子不再自然飘动——但可以用代码模拟摆动。
const PHYSICS_OFF = true

// ── 待机自动活动 ──
// 不说话时她也要"活着"：呼吸起伏、视线游移、偶尔换个轻表情。
// 优先级低于 pose（别人给的姿态），pose 活跃时让位。
const idleActivity = {
  enabled: true,
  nextGazeAt: 0,
  gaze: { x: 0, y: 0 },
  gazeTarget: { x: 0, y: 0 },
  nextExprAt: 0,
  expr: null,
  exprUntil: 0,
}
const IDLE_EXPRS = ['微笑', '眯眼', '眨单眼', '挑眉', '笑眼']   // 只换轻表情，不用黑脸/流泪
const GAZE_TAU_MS = 408           // 等效 60fps 下每帧 0.04 趋近速率
let lastIdleTs = 0

//: 头部角度时间戳历史，用来给头发做帧率无关的惯性延迟（跟着头滞后甩）
const headHist = []
const HEAD_LAG_MS = 117           // 基础滞后约 117ms（等效 60fps 下 7 帧）
const HEAD_LAYER_STEP_MS = 17     // 每层外延滞后 ~17ms（等效 60fps 下 +1 帧）
const HEAD_HIST_MAX_MS = 1000     // 历史窗口保留 1 秒

function recordHeadHist(now, x, y) {
  if (headHist.length && (now < headHist[headHist.length - 1].t || (now - headHist[headHist.length - 1].t) > HEAD_HIST_MAX_MS)) {
    headHist.length = 0
  }
  headHist.push({ t: now, x, y })
  const cutoff = now - HEAD_HIST_MAX_MS
  while (headHist.length > 2 && headHist[1].t < cutoff) {
    headHist.shift()
  }
}

function sampleHeadHist(targetT) {
  const n = headHist.length
  if (!n) return { x: 0, y: 0 }
  if (targetT <= headHist[0].t) return { x: headHist[0].x, y: headHist[0].y }
  if (targetT >= headHist[n - 1].t) return { x: headHist[n - 1].x, y: headHist[n - 1].y }
  for (let j = n - 2; j >= 0; j--) {
    const a = headHist[j]
    const b = headHist[j + 1]
    if (a.t <= targetT && targetT <= b.t) {
      const span = b.t - a.t
      const u = span > 0 ? (targetT - a.t) / span : 0
      return { x: a.x + (b.x - a.x) * u, y: a.y + (b.y - a.y) * u }
    }
  }
  return { x: headHist[0].x, y: headHist[0].y }
}

function rand(a, b) { return a + Math.random() * (b - a) }

function idleTick(now = performance.now()) {
  if (!idleActivity.enabled || !model) return
  const dtMs = stepDtMs(now, lastIdleTs)
  lastIdleTs = now
  const core = model.internalModel.coreModel
  const poseActive = now < poseUntil

  // ① 头部 / 耳朵 / 头发 / 身体：物理关掉后全部可写，做成缓慢多相位摆动
  if (!poseActive) {
    try {
      const t = now / 1000
      // 头部：前后 + 左右 + 微转（多频率叠加，避免机械感）
      const hx = Math.sin(t / 5.1 * Math.PI * 2) * 9 + Math.sin(t / 2.3 * Math.PI * 2) * 3
      const hy = Math.sin(t / 6.7 * Math.PI * 2) * 5
      const hz = Math.sin(t / 7.3 * Math.PI * 2) * 7
      core.setParameterValueById('ParamAngleX', hx)
      core.setParameterValueById('ParamAngleY', hy)
      core.setParameterValueById('ParamAngleZ', hz)

      // 身体跟着头部轻微摆动
      core.setParameterValueById('ParamBodyAngleX', Math.sin(t / 4.2 * Math.PI * 2) * 6)
      core.setParameterValueById('ParamBodyAngleZ', Math.sin(t / 5.7 * Math.PI * 2) * 5)

      // 耳朵：三段相位错开地扇动（幅度大，看得出来）
      const e1 = Math.sin(t / 2.6 * Math.PI * 2) * 0.85
      const e2 = Math.sin(t / 2.6 * Math.PI * 2 + 1.1) * 0.75
      const e3 = Math.sin(t / 2.6 * Math.PI * 2 + 2.2) * 0.60
      for (const p of ['LEar1', 'REar1']) core.setParameterValueById(p, e1)
      for (const p of ['LEar2', 'REar2']) core.setParameterValueById(p, e2)
      for (const p of ['LEar3', 'REar3']) core.setParameterValueById(p, e3)

      // 呆毛（帽子尖）：周期摆动。注意这些参数范围是 ±1，不是 ±30
      const aha = Math.sin(t / 1.75 * Math.PI * 2) * 0.72
             + Math.sin(t / 0.9 * Math.PI * 2) * 0.18        // 高频小抖
      core.setParameterValueById('Hat2pha', aha)
      core.setParameterValueById('Hat2phB', aha * 0.85)
      core.setParameterValueById('Hat2phC', aha * 0.65)
      core.setParameterValueById('Hat2phD', aha * 0.45)

      // 帽子本体：被动跟随头部（归一化到 ±1 再乘系数）
      const nx = hx / 30, ny = hy / 30
      core.setParameterValueById('Hat_XA', nx * 0.45)
      core.setParameterValueById('Hat_YA', ny * 0.40)

      // 头发：按时间戳采样头部历史，保证降帧时惯性滞后时间恒定不拖慢。
      recordHeadHist(now, nx, ny)
      for (let i = 1; i <= 4; i++) {
        // 越外层（i 大）甩得越多、滞后越久
        const k = 0.35 + i * 0.15
        const ref = sampleHeadHist(now - (HEAD_LAG_MS + (i - 1) * HEAD_LAYER_STEP_MS))
        try {
          core.setParameterValueById('FrontHairX' + i, Math.max(-0.8, Math.min(0.8, ref.x * k)))
          core.setParameterValueById('FrontHairY' + i, Math.max(-0.8, Math.min(0.8, ref.y * k * 0.6)))
        } catch {}
      }

      // 肩膀
      core.setParameterValueById('ShoulderY', Math.sin(t / 4.2 * Math.PI * 2) * 5)
    } catch {}
  }

  // ② 视线游移：每 3~6 秒换一个注视点，按 dt 指数平滑趋近
  if (now > idleActivity.nextGazeAt) {
    idleActivity.gazeTarget = { x: rand(-0.8, 0.8), y: rand(-0.5, 0.5) }
    idleActivity.nextGazeAt = now + rand(3000, 6000)
  }
  if (!poseActive) {
    const gazeAlpha = expAlpha(dtMs, GAZE_TAU_MS)
    idleActivity.gaze.x += (idleActivity.gazeTarget.x - idleActivity.gaze.x) * gazeAlpha
    idleActivity.gaze.y += (idleActivity.gazeTarget.y - idleActivity.gaze.y) * gazeAlpha
    try {
      core.setParameterValueById('ParamEyeBallX', idleActivity.gaze.x)
      core.setParameterValueById('ParamEyeBallY', idleActivity.gaze.y)
    } catch {}
  }

  // ③ 偶尔换个轻表情：每 9~16 秒一次，持续 3~5 秒回默认
  // 外部表情保护期内、说话中或姿态活跃时暂停随机切换与复位，避免打断台词情绪。
  const speaking = now < speakingUntil || mouthValue > 0.01 || mouthSmooth > 0.01
  const exprLocked = now < externalExprUntil || speaking || poseActive
  if (exprLocked) {
    if (idleActivity.nextExprAt < now + 1500) {
      idleActivity.nextExprAt = now + 1500
    }
  } else if (now > idleActivity.nextExprAt) {
    if (idleActivity.expr) {
      // 回默认
      setExpression('默认体型', { fromIdle: true })
      idleActivity.expr = null
      idleActivity.nextExprAt = now + rand(9000, 16000)
    } else {
      const name = IDLE_EXPRS[Math.floor(Math.random() * IDLE_EXPRS.length)]
      setExpression(name, { fromIdle: true })
      idleActivity.expr = name
      idleActivity.nextExprAt = now + rand(3000, 5000)
    }
  }
}

/**
 * 姿态注入。Cubism 每帧 update 会把参数重置回默认，
 * 所以一次性 setParameterValueById 会立刻被冲掉——必须逐帧持续写入。
 * 做法：维护一个当前姿态表 + 到期时间，在 ticker 里每帧写回。
 */
const poseCurrent = {}     // 参数名 -> 目标值
let poseUntil = 0          // 保持到什么时候（ms 时间戳）
let poseReturn = {}        // 到期后回到的值

function applyPose(params, holdMs) {
  if (!model) return false
  const core = model.internalModel.coreModel
  const keys = Object.keys(params || {})
  if (!keys.length) return false

  for (const k of keys) {
    if (!(k in poseReturn)) {
      try { poseReturn[k] = core.getParameterValueById(k) || 0 } catch { poseReturn[k] = 0 }
    }
    poseCurrent[k] = Number(params[k])
  }
  poseUntil = performance.now() + Math.max(300, holdMs || 900)
  report('姿态 ' + keys.join(','))
  return true
}

/** 每帧把当前姿态写回参数表（必须在模型 update 之后、绘制之前）。 */
function pumpPose() {
  if (!model) return
  const core = model.internalModel.coreModel
  const now = performance.now()
  const active = now < poseUntil
  for (const [k, v] of Object.entries(poseCurrent)) {
    try {
      if (active) core.setParameterValueById(k, v)
      else {
        core.setParameterValueById(k, poseReturn[k] || 0)
        delete poseCurrent[k]
        delete poseReturn[k]
      }
    } catch {}
  }
}

// 桥推送 0-100，模型参数 0-1。
// mouthValue 是目标值，mouthSmooth 是按 dt 指数平滑后的实际值——
// 既避免一帧跳到位显得抽搐，也保证 8fps 降帧时不会变成慢动作粘滞。
let mouthValue = 0
let mouthSmooth = 0
let lastMouthTs = 0
// 张开和闭合用不同时间常数：张开跟手（~21ms，等效 60fps 下 0.55），
// 闭合柔和（~130ms，等效 60fps 下 0.12，收尾 ~130~170ms）。
const MOUTH_OPEN_TAU_MS = 21
const MOUTH_CLOSE_TAU_MS = 130

function setMouth(v) {
  if (v == null || v === '') return
  const n = Number(v)
  if (!Number.isFinite(n)) return
  const nextVal = Math.max(0, Math.min(1, n / 100))
  const now = performance.now()
  mouthValue = nextVal
  if (nextVal > 0.01) {
    speakingUntil = Math.max(speakingUntil, now + SPEAKING_HOLD_MS)
    markActivity(ACTIVE_HOLD_MS, now)
  } else {
    lastAct = Math.max(lastAct, now)
  }
}

function pumpMouth(now = performance.now()) {
  if (!model) return
  const dtMs = stepDtMs(now, lastMouthTs)
  lastMouthTs = now
  const tau = mouthValue > mouthSmooth ? MOUTH_OPEN_TAU_MS : MOUTH_CLOSE_TAU_MS
  const alpha = expAlpha(dtMs, tau)
  mouthSmooth += (mouthValue - mouthSmooth) * alpha
  // 收尾：目标与当前都接近 0 时直接归零，避免浮点残留微张
  if (mouthValue < 0.01 && mouthSmooth < 0.01) mouthSmooth = 0
  try {
    model.internalModel.coreModel.setParameterValueById('ParamMouthOpenY', mouthSmooth)
  } catch {}
}

// ── 轮询桥 ──
function schedulePoll(d) { setTimeout(pollOnce, d) }
async function pollOnce() {
  try {
    const res = await fetch(BRIDGE + '/cmd', { cache: 'no-store' })
    failures = 0
    if (!res.ok) return schedulePoll(BACKOFF[0])
    const cmd = await res.json()
    if (cmd && cmd.seq !== lastSeq) {
      lastSeq = cmd.seq
      const cmdMs = parseOptionalMs(cmd.ms)
      const hold = cmd.kind === 'pose' && Number.isFinite(cmdMs) && cmdMs > 0
        ? Math.max(ACTIVE_HOLD_MS, cmdMs)
        : ACTIVE_HOLD_MS
      markActivity(hold)
      if (cmd.kind === 'motion') playMotion(cmd.group || 'Gesture', cmd.index || 0)
      else if (cmd.kind === 'pose' && cmd.params) applyPose(cmd.params, cmd.ms)
      else if (typeof cmd.name === 'string' && cmd.name) {
        setExpression(cmd.name, Number.isFinite(cmdMs) && cmdMs > 0 ? { holdMs: cmdMs } : undefined)
      }
    }
    schedulePoll(BACKOFF[0])
  } catch {
    failures++
    schedulePoll(BACKOFF[Math.min(failures, BACKOFF.length - 1)])
  }
}
function poll() { schedulePoll(0) }

function openMouthStream() {
  try {
    const es = new EventSource(BRIDGE + '/mouth-stream')
    es.addEventListener('message', ev => {
      setMouth(ev.data)
    })
    es.addEventListener('error', () => { es.close(); setTimeout(openMouthStream, 3000) })
  } catch {}
}

// ── 控制窗口通过 preload 转发的配置 ──
window.addEventListener('relay-cfg', (e) => {
  const c = e.detail || {}
  if (Number.isFinite(c.zoom)) CFG.zoom = c.zoom
  if (Number.isFinite(c.viewY)) CFG.viewY = c.viewY
  if (Number.isFinite(c.fps) && c.fps >= 0) {
    CFG.fps = c.fps
    markActivity(ACTIVE_HOLD_MS)
    applyFps()
  }
  if (typeof c.blink === 'boolean') { CFG.blink = c.blink; applyBlink() }
  applyTransform()
  report('cfg ' + JSON.stringify(c).slice(0, 40))
})
window.addEventListener('relay-cmd', (e) => {
  const c = e.detail || {}
  if (c.type === 'expr' && c.name) {
    markActivity(ACTIVE_HOLD_MS)
    setExpression(c.name)
  } else if (c.type === 'motion') {
    markActivity(ACTIVE_HOLD_MS)
    playMotion(c.group, c.index)
  }
})
window.addEventListener('relay-zoom', (e) => {
  const z = Number((e.detail || {}).zoom)
  if (Number.isFinite(z)) { CFG.zoom = z; applyTransform(); report('zoom ' + z) }
})

// ── 编辑模式：左键拖动窗口、中键滚轮缩放 ──
let editMode = false
let dragFrom = null
let midDown = false

function setEditMode(on) {
  editMode = !!on
  // drag 区会吞掉 mousedown，编辑时改成 no-drag 才能手动处理
  document.body.style.webkitAppRegion = editMode ? 'no-drag' : 'drag'
  document.documentElement.style.webkitAppRegion = editMode ? 'no-drag' : 'drag'
  report(editMode ? '编辑中' : '普通')
}

try {
  window.pet && window.pet.onEditMode(setEditMode)
} catch {}

window.addEventListener('mousedown', (e) => {
  if (!editMode) return
  if (e.button === 1) { midDown = true; e.preventDefault(); return }
  if (e.button === 0) {
    dragFrom = { x: e.screenX, y: e.screenY }
    e.preventDefault()
  }
})

window.addEventListener('mousemove', (e) => {
  if (!editMode || !dragFrom) return
  const dx = e.screenX - dragFrom.x
  const dy = e.screenY - dragFrom.y
  if (!dx && !dy) return
  dragFrom.x = e.screenX
  dragFrom.y = e.screenY
  try { window.pet && window.pet.moveModelBy(dx, dy) } catch {}
})

window.addEventListener('mouseup', (e) => {
  if (e.button === 1) midDown = false
  if (e.button === 0) dragFrom = null
})

window.addEventListener('wheel', (e) => {
  if (!editMode && !midDown) return
  e.preventDefault()
  const cur = Math.round(Math.min(window.innerWidth, window.innerHeight))
  const step = e.deltaY > 0 ? -36 : 36
  const next = Math.max(160, Math.min(1400, cur + step))
  try { window.pet && window.pet.fitModel(next) } catch {}
  report('缩放 ' + next)
}, { passive: false })

// 触控板/鼠标中键按下时的滚动
window.addEventListener('auxclick', (e) => { if (e.button === 1) e.preventDefault() })

window.addEventListener('error', e => report('JS错误:' + (e.message || '?')))
window.addEventListener('DOMContentLoaded', () => {
  boot().catch(err => report('加载失败: ' + (err && err.message ? err.message : String(err))))
})
