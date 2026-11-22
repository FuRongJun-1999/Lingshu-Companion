# -*- coding: utf-8 -*-
"""config_server.py · 服务商配置面板（浏览器打开 :8793）

# 功能名：服务配置面板
# 生效条件：providers.py 可用；端口 8793 未被占用
# 子功能：
#   - 列出五类能力（asr/tts/llm/vision/image/control）及其可选服务商
#   - 切换当前服务商、编辑参数、填密钥、一键探测连通性
#   - 密钥只写不读：接口返回 key 是否存在，不回显明文
# 执行：python config_server.py   然后浏览器开 http://127.0.0.1:8793
# 验证方式：打开页面能看到六张卡片；点「测试」显示毫秒数或错误原因
# 不适用条件：
#   - 只做配置，不启动/停止服务本身
#   - 面板改完参数后，调用方需重启（或下次调用自然读到新配置）
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from providers import P  # noqa: E402

PORT = 8793

PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>灵枢伴侣 · 服务配置</title>
<style>
:root{--bg:#0f1319;--card:#171c26;--line:#242c3a;--txt:#dce3ee;--dim:#8896ab;
      --ok:#3ddc97;--bad:#ff6b6b;--accent:#4a9eff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.6 -apple-system,"Segoe UI",system-ui,sans-serif}
header{padding:22px 28px 10px;border-bottom:1px solid var(--line)}
h1{font-size:19px;margin:0 0 4px;font-weight:600}
.sub{color:var(--dim);font-size:13px}
main{padding:20px 28px 60px;max-width:980px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-bottom:14px}
.row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.cap{font-weight:600;font-size:15px}
.tag{font-size:11px;padding:2px 8px;border-radius:20px;background:#222b3a;color:var(--dim)}
.tag.local{background:#1c3328;color:var(--ok)} .tag.cloud{background:#2a2536;color:#b79cff}
.tag.none{background:#2a2020;color:#ff9b9b}
.sel{background:#0d1117;border:1px solid var(--line);color:var(--txt);padding:6px 10px;border-radius:6px;font-size:13px}
button{background:#222b3a;border:1px solid var(--line);color:var(--txt);padding:6px 14px;border-radius:6px;cursor:pointer;font-size:13px}
button:hover{background:#2b3648} button.primary{background:var(--accent);border-color:var(--accent);color:#08111c;font-weight:600}
.status{font-size:12px;color:var(--dim);margin-left:auto}
.ok{color:var(--ok)} .bad{color:var(--bad)}
.params{margin-top:12px;padding-top:12px;border-top:1px dashed var(--line);display:none}
.params.open{display:block}
label{display:block;margin:8px 0 3px;color:var(--dim);font-size:12px}
input{width:100%;background:#0d1117;border:1px solid var(--line);color:var(--txt);padding:7px 10px;border-radius:6px;font-size:13px;font-family:ui-monospace,Consolas,monospace}
.note{color:var(--dim);font-size:12px;margin-top:8px}
.secret{color:#b79cff}
</style></head><body>
<header>
  <h1>灵枢伴侣 · 服务配置</h1>
  <div class="sub">本地 / 云端服务商可切换。密钥只写不读，保存在 config/secrets.json（不进 git）。</div>
</header>
<main id="app">加载中…</main>
<script>
const $ = s => document.querySelector(s);
const el = (t, c, txt) => { const e = document.createElement(t); if(c) e.className=c; if(txt!=null) e.textContent=txt; return e; };
let CAPS = {};

async function api(path, body) {
  const r = await fetch(path, body ? {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)} : {});
  return r.json();
}

async function load() {
  const d = await api('/api/caps');
  CAPS = d.caps;
  const app = $('#app'); app.innerHTML = '';
  for (const [name, spec] of Object.entries(CAPS)) {
    const card = el('div','card');
    const row = el('div','row');
    row.append(el('span','cap', spec.label || name));
    row.append(el('span','tag', name));

    const sel = el('select','sel');
    for (const [k,o] of Object.entries(spec.options||{})) {
      const op = el('option', null, o.label + ' [' + o.kind + ']');
      op.value = k;
      if (k === spec.current) op.selected = true;
      sel.append(op);
    }
    sel.onchange = async () => {
      await api('/api/current', {cap:name, name:sel.value});
      await load();
    };
    row.append(sel);

    const tk = (spec.options[spec.current]||{}).kind || 'none';
    const tagKind = el('span','tag '+tk, tk);
    row.append(tagKind);

    const bEdit = el('button', null, '参数');
    bEdit.onclick = () => $('#p-'+name).classList.toggle('open');
    row.append(bEdit);

    const bTest = el('button', null, '测试');
    const st = el('span','status','');
    bTest.onclick = async () => {
      st.textContent = '测试中…'; st.className = 'status';
      const r = await api('/api/test', {cap:name});
      st.textContent = r.ok ? ('✓ ' + r.ms + 'ms') : ('✗ ' + (r.error||'失败'));
      st.className = 'status ' + (r.ok ? 'ok' : 'bad');
    };
    row.append(bTest); row.append(st);
    card.append(row);

    // 参数区
    const p = el('div','params'); p.id = 'p-'+name;
    const opt = (spec.options||{})[spec.current] || {};
    const params = opt.params || {};
    const inputs = {};
    for (const [k,v] of Object.entries(params)) {
      if (k === 'api_key') continue;
      const lab = el('label', null, k);
      const inp = el('input');
      inp.value = (typeof v === 'object') ? JSON.stringify(v) : v;
      inp.dataset.key = k;
      inputs[k] = inp;
      p.append(lab); p.append(inp);
    }
    // 密钥
    const hasKey = !!params.api_key;
    const lab2 = el('label', 'secret', 'api_key  ' + (hasKey ? '（已设置，留空则不改）' : '（未设置）'));
    const inpKey = el('input');
    inpKey.type = 'password'; inpKey.placeholder = hasKey ? '••••••••' : '粘贴密钥';
    p.append(lab2); p.append(inpKey);

    if (opt.note) p.append(el('div','note', '说明：' + opt.note));

    const saveRow = el('div','row'); saveRow.style.marginTop = '10px';
    const bSave = el('button','primary','保存');
    const saveSt = el('span','status','');
    bSave.onclick = async () => {
      const np = {};
      for (const [k,inp] of Object.entries(inputs)) {
        let v = inp.value.trim();
        try { if (v.startsWith('{') || v.startsWith('[')) v = JSON.parse(v); } catch {}
        if (v === 'true') v = true; else if (v === 'false') v = false;
        else if (v !== '' && !isNaN(Number(v)) && !/^\d{4}-/.test(v)) v = Number(v);
        np[k] = v;
      }
      saveSt.textContent = '保存中…';
      await api('/api/params', {cap:name, name:spec.current, params:np});
      if (inpKey.value.trim()) await api('/api/secret', {ref:'placeholder', cap:name, name:spec.current, value:inpKey.value.trim()});
      saveSt.textContent = '已保存'; saveSt.className = 'status ok';
      setTimeout(()=>{saveSt.textContent=''}, 2000);
    };
    saveRow.append(bSave); saveRow.append(saveSt);
    p.append(saveRow);
    card.append(p);
    app.append(card);
  }
}
load();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if u.path == "/api/caps":
            caps = P.caps()
            out = {}
            for name, spec in caps.items():
                opts = {}
                for k, o in (spec.get("options") or {}).items():
                    p = dict(o.get("params") or {})
                    if "key_ref" in p:
                        p["api_key"] = "***" if P.secret(p["key_ref"]) else ""
                        del p["key_ref"]
                    opts[k] = {"label": o.get("label", k), "kind": o.get("kind", "none"),
                               "params": p, "note": o.get("note", "")}
                out[name] = {"label": spec.get("label", name),
                             "current": spec.get("current", "none"), "options": opts}
            return self._json({"caps": out})
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("content-length") or 0)
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            data = {}
        if u.path == "/api/current":
            ok = P.set_current(data.get("cap", ""), data.get("name", ""))
            return self._json({"ok": ok})
        if u.path == "/api/params":
            cap, name = data.get("cap", ""), data.get("name", "")
            params = dict(data.get("params") or {})
            # 保留原 key_ref（前端不传）
            raw_opt = ((P.caps().get(cap) or {}).get("options") or {}).get(name) or {}
            old_key_ref = (raw_opt.get("params") or {}).get("key_ref")
            if old_key_ref and "key_ref" not in params:
                params["key_ref"] = old_key_ref
            ok = P.set_params(cap, name, params)
            return self._json({"ok": ok})
        if u.path == "/api/secret":
            cap, name, val = data.get("cap", ""), data.get("name", ""), data.get("value", "")
            ref = ""
            caps = P.caps()
            opt = ((caps.get(cap) or {}).get("options") or {}).get(name) or {}
            ref = (opt.get("params") or {}).get("key_ref", "")
            if not ref:
                return self._json({"ok": False, "error": "该服务商不需要密钥"})
            P.set_secret(ref, val)
            return self._json({"ok": True})
        if u.path == "/api/test":
            cap = data.get("cap", "")
            return self._json(P.test(cap))
        self._json({"error": "not found"}, 404)


def main() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"配置面板: http://127.0.0.1:{PORT}")
    print("Ctrl+C 退出")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
