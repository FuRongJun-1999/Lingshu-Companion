# -*- coding: utf-8 -*-
"""model_server.py · Live2D 模型控制台（浏览器 :8795）

# 功能名：模型控制台
# 生效条件：桥 8789 在跑（表情/姿态下发）；模型目录 _live2d_stage/model 可读
# 子功能：
#   - 表情面板：从 cdi3.json 读全部表情，点一下立刻切换
#   - 姿态面板：从 marks.POSE_MAP 读全部可写姿态，点一下注入参数
#   - 参数滑块：头/耳/身体直接拖，实时看效果
#   - 口型测试：手动推包络，验证口型链路
# 执行：python model_server.py   浏览器开 http://127.0.0.1:8795
# 验证方式：点任意表情，桌宠立刻变化；拖滑块，头/耳跟着动
# 不适用条件：
#   - 只做手动触发，不替代自动待机动画
#   - 姿态依赖物理已关闭（PHYSICS_OFF=true），否则参数会被覆盖
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STAGE = Path(r"D:\2_ai\_live2d_stage\model")
BRIDGE = "http://127.0.0.1:8789"
PORT = 8795
sys.path.insert(0, str(ROOT))


def _expressions() -> list[str]:
    """读表情名。cdi3 里没有 Expressions 字段，直接扫 *.exp3.json 文件名。"""
    out = []
    try:
        for f in sorted((STAGE / "Exp3").glob("*.exp3.json")):
            name = f.name[:-len(".exp3.json")]
            if name:
                out.append(name)
    except Exception:
        pass
    return out


def _poses() -> dict[str, dict]:
    """从 marks.POSE_MAP 读可写姿态。"""
    try:
        from marks import POSE_MAP
        return dict(POSE_MAP)
    except Exception:
        return {}


def _get(url: str, timeout: float = 3.0) -> str:
    try:
        return urllib.request.urlopen(url, timeout=timeout).read().decode("utf-8", "replace")
    except Exception as e:
        return '{"error":"' + str(e)[:60] + '"}'


PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>灵枢伴侣 · 模型控制</title>
<style>
:root{--bg:#0f1319;--card:#171c26;--line:#242c3a;--txt:#dce3ee;--dim:#8896ab;--accent:#4a9eff;--ok:#3ddc97}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.6 -apple-system,"Segoe UI",system-ui,sans-serif}
header{padding:20px 26px 10px;border-bottom:1px solid var(--line)}
h1{font-size:18px;margin:0 0 4px;font-weight:600}
.sub{color:var(--dim);font-size:12px}
main{padding:18px 26px 60px;max-width:1100px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:14px}
h2{font-size:13px;margin:0 0 10px;color:var(--dim);font-weight:600;letter-spacing:.5px}
.grid{display:flex;flex-wrap:wrap;gap:6px}
.grid button{background:#222b3a;border:1px solid var(--line);color:var(--txt);
  padding:6px 12px;border-radius:6px;cursor:pointer;font-size:12px;transition:.12s}
.grid button:hover{background:#2f3d52}
.grid button:active{background:var(--accent);color:#08111c}
.grid button.reset{background:#2a2020;border-color:#4a2a2a;color:#ff9b9b}
.sliders{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px 22px}
.sl{display:flex;align-items:center;gap:8px}
.sl label{flex:0 0 130px;color:var(--dim);font-size:12px;font-family:ui-monospace,Consolas,monospace}
.sl input[type=range]{flex:1;accent-color:var(--accent)}
.sl .v{flex:0 0 46px;text-align:right;color:var(--accent);font-size:12px;font-variant-numeric:tabular-nums}
.log{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:var(--dim);
  background:#0b0e14;border:1px solid var(--line);border-radius:6px;padding:8px 10px;
  max-height:120px;overflow-y:auto;margin-top:12px;white-space:pre-wrap}
</style></head><body>
<header>
  <h1>灵枢伴侣 · 模型控制</h1>
  <div class="sub">点击即时生效。姿态依赖物理已关闭，否则参数会被 physics 覆盖。</div>
</header>
<main>
  <section>
    <h2>表情（{{NEXP}}）</h2>
    <div class="grid" id="exprs"></div>
  </section>
  <section>
    <h2>姿态 / 视线 / 肢体（{{NPOSE}}）</h2>
    <div class="grid" id="poses"></div>
    <div class="grid" style="margin-top:8px">
      <button class="reset" data-reset="pose">复位姿态</button>
    </div>
  </section>
  <section>
    <h2>参数直控</h2>
    <div class="sliders" id="sliders"></div>
    <div class="grid" style="margin-top:10px">
      <button class="reset" data-reset="all">全部归零</button>
    </div>
  </section>
  <section>
    <h2>口型测试</h2>
    <div class="grid">
      <button data-mouth="30">微张 30</button>
      <button data-mouth="60">半张 60</button>
      <button data-mouth="100">全张 100</button>
      <button data-mouth="0">闭上</button>
    </div>
  </section>
  <div class="log" id="log"></div>
</main>
<script>
const logEl = document.getElementById('log');
const log = m => { logEl.textContent = new Date().toLocaleTimeString() + '  ' + m + '\n' + logEl.textContent.slice(0, 800); };
const B = '';

// 表情
fetch('/api/exprs').then(r=>r.json()).then(d => {
  const box = document.getElementById('exprs');
  d.list.forEach(n => {
    const b = document.createElement('button');
    b.textContent = n;
    b.onclick = async () => {
      const r = await fetch('/api/expr?name=' + encodeURIComponent(n));
      log('表情 → ' + n + '  ' + (await r.text()));
    };
    box.append(b);
  });
});

// 姿态
fetch('/api/poses').then(r=>r.json()).then(d => {
  const box = document.getElementById('poses');
  d.list.forEach(([n, params]) => {
    const b = document.createElement('button');
    b.textContent = n;
    b.onclick = async () => {
      const r = await fetch('/api/pose?name=' + encodeURIComponent(n));
      log('姿态 → ' + n + '  ' + (await r.text()));
    };
    box.append(b);
  });
});

// 参数滑块
const SLIDERS = [
  ['ParamAngleX','头前后',-30,30], ['ParamAngleY','头上下',-30,30], ['ParamAngleZ','头左右',-30,30],
  ['LEar1','左耳外',-1,1], ['LEar2','左耳中',-1,1], ['REar1','右耳外',-1,1],
  ['ParamBodyAngleX','身前倾',-30,30], ['ParamBodyAngleZ','身侧倾',-30,30],
  ['ParamEyeBallX','眼球X',-1,1], ['ParamEyeBallY','眼球Y',-1,1],
  ['ParamEyeLOpen','左眼开',0,1.5], ['ParamEyeROpen','右眼开',0,1.5],
  ['ParamBrowLY','左眉',-1,1], ['ParamMouthForm','嘴型',-1,1],
  ['Hat2pha','呆毛',-1,1],
];
const sliderBox = document.getElementById('sliders');
const inputs = {};
SLIDERS.forEach(([key,label,min,max]) => {
  const d = document.createElement('div'); d.className = 'sl';
  const lab = document.createElement('label'); lab.textContent = label;
  const inp = document.createElement('input');
  inp.type='range'; inp.min=min; inp.max=max; inp.step = (max-min)/200;
  inp.value = (min+max)/2 > 0 && max <= 1.5 ? 0 : 0;
  if (key === 'ParamEyeLOpen' || key === 'ParamEyeROpen') inp.value = 1;
  const v = document.createElement('span'); v.className='v'; v.textContent = (+inp.value).toFixed(2);
  inp.oninput = () => {
    v.textContent = (+inp.value).toFixed(2);
    fetch('/api/pose-params?p=' + encodeURIComponent(JSON.stringify({[key]: +inp.value})) + '&ms=8000');
  };
  inputs[key] = { inp, v };
  d.append(lab); d.append(inp); d.append(v);
  sliderBox.append(d);
});

// 口型
document.querySelectorAll('[data-mouth]').forEach(b => {
  b.onclick = async () => {
    const v = b.dataset.mouth;
    await fetch('/api/mouth?value=' + v);
    log('口型 ' + v);
  };
});

// 复位
document.querySelectorAll('[data-reset]').forEach(b => {
  b.onclick = async () => {
    if (b.dataset.reset === 'pose') {
      await fetch('/api/reset-pose');
      log('姿态已复位');
    } else {
      await fetch('/api/reset-all');
      Object.entries(inputs).forEach(([k,o]) => {
        o.inp.value = (k.includes('EyeL')||k.includes('EyeR')) && k.includes('Open') ? 1 : 0;
        o.v.textContent = (+o.inp.value).toFixed(2);
      });
      log('全部参数归零');
    }
  };
});
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, o, code=200):
        self._send(code, json.dumps(o, ensure_ascii=False).encode("utf-8"))

    def _text(self, s, code=200):
        self._send(code, s.encode("utf-8"), "text/plain; charset=utf-8")

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            exps, poses = _expressions(), _poses()
            html = PAGE.replace("{{NEXP}}", str(len(exps))).replace("{{NPOSE}}", str(len(poses)))
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if u.path == "/api/exprs":
            return self._json({"list": _expressions()})
        if u.path == "/api/poses":
            return self._json({"list": [[k, v] for k, v in _poses().items()]})
        if u.path == "/api/expr":
            name = (q.get("name") or [""])[0]
            return self._text(_get(BRIDGE + "/set?name=" + urllib.parse.quote(name)))
        if u.path == "/api/pose":
            name = (q.get("name") or [""])[0]
            params = _poses().get(name) or {}
            if not params:
                return self._text('{"error":"unknown pose"}')
            p = urllib.parse.quote(json.dumps(params))
            return self._text(_get(BRIDGE + f"/pose?params={p}&ms=3000"))
        if u.path == "/api/pose-params":
            p = (q.get("p") or ["{}"])[0]
            ms = (q.get("ms") or ["3000"])[0]
            return self._text(_get(BRIDGE + "/pose?params=" + urllib.parse.quote(p) + "&ms=" + ms))
        if u.path == "/api/mouth":
            v = (q.get("value") or ["0"])[0]
            return self._text(_get(BRIDGE + "/mouth?value=" + v))
        if u.path == "/api/reset-pose":
            zeros = {k: 0 for k in
                     ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamBodyAngleX", "ParamBodyAngleZ",
                      "LEar1", "LEar2", "LEar3", "REar1", "REar2", "REar3"]}
            p = urllib.parse.quote(json.dumps(zeros))
            return self._text(_get(BRIDGE + f"/pose?params={p}&ms=500"))
        if u.path == "/api/reset-all":
            zeros = {k: 0 for k in
                     ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamBodyAngleX", "ParamBodyAngleZ",
                      "LEar1", "LEar2", "LEar3", "REar1", "REar2", "REar3",
                      "ParamEyeBallX", "ParamEyeBallY", "ParamBrowLY", "ParamBrowRY",
                      "ParamMouthForm", "Hat2pha"]}
            p = urllib.parse.quote(json.dumps(zeros))
            return self._text(_get(BRIDGE + f"/pose?params={p}&ms=500"))
        self._json({"error": "not found"}, 404)


def main() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"模型控制: http://127.0.0.1:{PORT}")
    print(f"  表情 {len(_expressions())} 个 / 姿态 {len(_poses())} 个")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
