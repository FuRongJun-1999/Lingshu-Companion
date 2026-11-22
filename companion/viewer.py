# -*- coding: utf-8 -*-
"""viewer.py · 对话记录展示页面

# 功能名：伴侣对话可视化
# 生效条件：data/interactions/*.md 存在；端口 8792 可用
# 子功能：
#   - 解析互动 MD 为结构化记录
#   - 按时间倒序展示（观众说了什么、她回了什么、用了哪些标记）
#   - 支持关键词搜索与类型筛选
#   - 右侧显示观众档案摘要（来自她记忆）
# 执行：python viewer.py [--port 8792]
# 验证方式：浏览器打开 http://127.0.0.1:8792 能看到记录；搜索"脸红"能过滤
# 不适用条件：
#   - 只读展示，不提供编辑
#   - 每次请求重新解析 MD（数据量大时需加缓存）
"""
from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent


def _default_data() -> Path:
    for cand in (REPO / "data", ROOT / "data"):
        if cand.is_dir():
            return cand
    return REPO / "data"


DATA = _default_data()

BLOCK_RE = re.compile(
    r"<!-- id:([0-9a-f]{12}) -->\n## (\d{2}:\d{2}:\d{2}) · (.*?)(?: · (.*?))?\n(.*?)(?=\n<!-- id:|\Z)",
    re.S,
)



#: 懒加载单例（首次调用才加载模型，页面只读时不占资源）
_VOICE = None
_BRAIN = None


def _voice():
    global _VOICE
    if _VOICE is None:
        from asr import VoiceInput
        _VOICE = VoiceInput(model_size='small')
    return _VOICE


def _brain():
    global _BRAIN
    if _BRAIN is None:
        from body import Body
        from idle import load_idle
        from scripts import ScriptBook
        from speak import speak
        from store import MdStore
        _BRAIN = {
            'store': MdStore(DATA),
            'book': ScriptBook.load(DATA),
            'pool': load_idle(DATA),
            'body': Body(),
            'speak': speak,
        }
    return _BRAIN


def parse_day(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for m in BLOCK_RE.finditer(text):
        iid, hh, who, aid, rest = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        stim = re.search(r"\*\*观众\*\*：\n\n(.*?)\n\n", rest, re.S)
        resp = re.search(r"\*\*她\*\*：\n\n(.*?)\n\n", rest, re.S)
        kind = re.search(r"- 类型：(\S+)", rest)
        mood = re.search(r"- 情绪：(\S+)", rest)
        acts = re.search(r"- 动作：(.*)", rest)
        lat = re.search(r"- 延迟：(\d+) ms", rest)
        out.append({
            "id": iid,
            "day": path.stem,
            "time": hh,
            "who": (who or "").strip(),
            "actor_id": (aid or "").strip(),
            "kind": (kind.group(1) if kind else ""),
            "stimulus": (stim.group(1) if stim else "").strip(),
            "response": (resp.group(1) if resp else "").strip(),
            "mood": (mood.group(1) if mood else ""),
            "acts": (acts.group(1) if acts else "").strip(),
            "latency": int(lat.group(1)) if lat else None,
        })
    return out


def all_records() -> list[dict]:
    d = DATA / "interactions"
    if not d.exists():
        return []
    recs = []
    for p in sorted(d.glob("*.md")):
        recs.extend(parse_day(p))
    recs.sort(key=lambda r: (r["day"], r["time"]), reverse=True)
    return recs


def relations() -> dict:
    d = DATA / "relations"
    out = {}
    if not d.exists():
        return out
    for p in d.glob("*.md"):
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        name = ""
        m = re.match(r"^#\s*(.+)$", t, re.M)
        if m:
            name = m.group(1).strip()
        out[p.stem] = {"file": p.name, "title": name, "chars": len(t), "preview": t[:300]}
    return out


PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<title>灵枢伴侣 · 对话记录</title>
<style>
 :root {{ --bg:#0d1219; --panel:#141c26; --line:#22303f; --fg:#c8d8e8; --dim:#6f8399; --acc:#4ea8ff; }}
 * {{ box-sizing:border-box; }}
 body {{ margin:0; background:var(--bg); color:var(--fg);
        font:14px/1.7 system-ui,"Microsoft YaHei",sans-serif; }}
 header {{ position:sticky; top:0; z-index:5; background:rgba(13,18,25,.96);
           border-bottom:1px solid var(--line); padding:12px 20px;
           display:flex; gap:14px; align-items:center; flex-wrap:wrap; }}
 h1 {{ font-size:15px; margin:0; color:var(--acc); font-weight:600; }}
 .stat {{ color:var(--dim); font-size:12px; }}
 input[type=text] {{ flex:1; min-width:200px; background:var(--panel);
        border:1px solid var(--line); color:var(--fg); border-radius:6px; padding:6px 10px; }}
 select {{ background:var(--panel); border:1px solid var(--line); color:var(--fg);
        border-radius:6px; padding:6px 8px; }}
 main {{ max-width:1000px; margin:0 auto; padding:16px 20px 60px; }}
 .day {{ margin:26px 0 10px; color:var(--dim); font-size:12px;
         border-bottom:1px dashed var(--line); padding-bottom:6px; }}
 .row {{ background:var(--panel); border:1px solid var(--line); border-radius:9px;
         padding:11px 14px; margin-bottom:10px; }}
 .meta {{ display:flex; gap:10px; align-items:baseline; margin-bottom:7px;
          font-size:12px; color:var(--dim); flex-wrap:wrap; }}
 .who {{ color:var(--acc); font-weight:600; }}
 .tag {{ background:#1d2a38; color:#8fb6d8; border-radius:4px; padding:1px 6px; font-size:11px; }}
 .tag.expr {{ background:#2a1d38; color:#c9a0ff; }}
 .tag.pose {{ background:#1d3038; color:#7fd8c0; }}
 .stim {{ color:#e0e8f0; margin:4px 0 6px; }}
 .resp {{ color:#b8cddd; border-left:2px solid var(--acc); padding-left:10px; }}
 .empty {{ color:var(--dim); }}
 .none {{ color:var(--dim); font-style:italic; }}
 /* 底部输入栏 */
 #composer {{ position:fixed; left:0; right:0; bottom:0; z-index:9;
   background:rgba(13,18,25,.97); border-top:1px solid var(--line);
   padding:10px 20px; display:flex; gap:8px; align-items:flex-end; }}
 #composer .wrap {{ max-width:1000px; margin:0 auto; width:100%;
   display:flex; gap:8px; align-items:flex-end; }}
 #msg {{ flex:1; min-height:38px; max-height:140px; resize:vertical;
   background:var(--panel); border:1px solid var(--line); color:var(--fg);
   border-radius:8px; padding:8px 11px; font:inherit; outline:none; }}
 #msg:focus {{ border-color:var(--acc); }}
 #composer button {{ background:#1d2a38; color:#8fb6d8; border:1px solid var(--line);
   border-radius:8px; padding:9px 16px; cursor:pointer; font:inherit; white-space:nowrap; }}
 #composer button:hover {{ background:#25374a; }}
 #composer button:disabled {{ opacity:.45; cursor:default; }}
 #btnSend {{ background:var(--acc); color:#08131f; border-color:var(--acc); font-weight:600; }}
 #btnMic.on {{ background:#b03030; color:#fff; border-color:#d04040; }}
 #cstatus {{ color:var(--dim); font-size:12px; padding:0 4px 8px; min-width:90px; }}
 main {{ padding-bottom:120px !important; }}
</style></head><body>
<header>
  <h1>灵枢伴侣 · 对话记录</h1>
  <span class="stat">{stat}</span>
  <input id="q" type="text" placeholder="搜索关键词（台词、观众、动作）…"/>
  <select id="kf">
    <option value="">全部类型</option>
    {kinds}
  </select>
</header>
<main id="list">{body}</main>
<script>
const q = document.getElementById('q'), kf = document.getElementById('kf');
function apply() {{
  const s = q.value.trim().toLowerCase(), k = kf.value;
  document.querySelectorAll('.row').forEach(el => {{
    const hit = (!s || el.dataset.text.includes(s)) && (!k || el.dataset.kind === k);
    el.style.display = hit ? '' : 'none';
  }});
  document.querySelectorAll('.day').forEach(el => {{
    let n = el.nextElementSibling, any = false;
    while (n && !n.classList.contains('day')) {{
      if (n.classList.contains('row') && n.style.display !== 'none') any = true;
      n = n.nextElementSibling;
    }}
    el.style.display = any ? '' : 'none';
  }});
}}
q.addEventListener('input', apply); kf.addEventListener('change', apply);
</script></body></html>"""

#: 附加到页面尾部的输入区与脚本。
#: 不能用 PAGE.format 注入——JS 里的 {} 会被当成格式占位符吃掉。
EXTRA_HTML = """<div id="composer"><div class="wrap">
  <textarea id="msg" rows="1" placeholder="打字，或点 🎤 说完自动转文字（可先改再发）"></textarea>
  <span id="cstatus"></span>
  <button id="btnMic" title="点一下开始录，说完自动停">🎤 说话</button>
  <button id="btnSend">发送</button>
</div></div>
<script>
const $=id=>document.getElementById(id);
const msgEl=$('msg'), btnSend=$('btnSend'), btnMic=$('btnMic'), cst=$('cstatus');
const say=(t,cls)=>{ if(!cst) return; cst.textContent=t; cst.style.color = cls==='err' ? '#e06060' : (cls==='ok' ? '#5fc98a' : ''); };
async function sendMsg(){
  const text = msgEl.value.trim();
  if(!text) return;
  btnSend.disabled = true; say('她在想…');
  try{
    const r = await fetch('/api/send', {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({text})});
    const d = await r.json();
    if(d.ok){ msgEl.value=''; say('已回复','ok'); setTimeout(()=>location.reload(),1200); }
    else say(d.error||'失败','err');
  }catch(e){ say('请求失败','err'); }
  btnSend.disabled = false;
}
if(btnSend) btnSend.onclick = sendMsg;
if(msgEl) msgEl.addEventListener('keydown', e => { if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); sendMsg(); } });
let recing = false;
if(btnMic) btnMic.onclick = async () => {
  if(recing) return;
  recing = true; btnMic.classList.add('on'); btnMic.textContent='录中…';
  say('请说话，停顿约 1.2 秒自动结束');
  try{
    const r = await fetch('/api/voice', {method:'POST'});
    const d = await r.json();
    if(d.ok && d.text){ msgEl.value = d.text; msgEl.focus(); say('识别完成，可修改后发送','ok'); }
    else if(d.ok) say('没听清，再说一次','err');
    else say(d.error||'录音失败','err');
  }catch(e){ say('录音请求失败','err'); }
  recing = false; btnMic.classList.remove('on'); btnMic.textContent='🎤 说话';
};
</script>
"""


ROW = """<div class="row" data-kind="{kind}" data-text="{search}">
  <div class="meta">
    <span class="who">{who}</span><span>{time}</span>
    <span class="tag">{kind}</span>{tags}
    {latency}
  </div>
  <div class="stim">{stim}</div>
  <div class="{respcls}">{resp}</div>
</div>"""


def render(qs: dict) -> bytes:
    recs = all_records()
    rels = relations()

    kinds = sorted({r["kind"] for r in recs if r["kind"]})
    kind_opts = "".join(f'<option value="{html.escape(k)}">{html.escape(k)}</option>' for k in kinds)

    answered = sum(1 for r in recs if r["response"])
    stat = (f'共 {len(recs)} 条 · 已回应 {answered} · '
            f'观众 {len(rels)} 份档案')

    parts, last_day = [], None
    for r in recs:
        if r["day"] != last_day:
            d = r["day"]
            try:
                d = datetime.strptime(r["day"], "%Y-%m-%d").strftime("%Y-%m-%d %A")
            except Exception:
                pass
            parts.append(f'<div class="day">{html.escape(d)}</div>')
            last_day = r["day"]

        tags = ""
        if r["mood"] and r["mood"] not in ("neutral", "未标注"):
            tags += f'<span class="tag expr">{html.escape(r["mood"])}</span>'
        if r["acts"] and r["acts"] != "无":
            for a in str(r["acts"]).split("、"):
                if a.strip():
                    cls = "expr" if a.strip() in ("脸红","爱心眼","黑脸","流泪","圈圈眼","屑屑眼","流冷汗","＞＜","加载") else "pose"
                    tags += f'<span class="tag {cls}">{html.escape(a.strip())}</span>'

        latency = f'<span>{r["latency"]}ms</span>' if r["latency"] is not None else ""
        search = " ".join([r["who"], r["stimulus"], r["response"], r["acts"], r["mood"]]).lower()

        parts.append(ROW.format(
            kind=html.escape(r["kind"]),
            search=html.escape(search),
            who=html.escape(r["who"] or "（未知）"),
            time=html.escape(r["time"]),
            tags=tags,
            latency=latency,
            stim=html.escape(r["stimulus"]) or '<span class="none">（冷场）</span>',
            respcls="resp" if r["response"] else "none",
            resp=html.escape(r["response"]) or "（未回应）",
        ))

    if not parts:
        parts = ['<div class="empty">还没有记录。跑一次 main.py 就会出现在这里。</div>']

    page = PAGE.format(stat=html.escape(stat), kinds=kind_opts, body="\n".join(parts))
    page = page.replace("</body>", EXTRA_HTML + "</body>")
    return page.encode("utf-8")


class Handler(BaseHTTPRequestHandler):

    # ── 需要时才导入，避免页面只读时加载模型 ──
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('content-type', 'application/json; charset=utf-8')
        self.send_header('content-length', str(len(body)))
        self.send_header('cache-control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get('content-length') or 0)
        try:
            payload = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            payload = {}

        if u.path == '/api/voice':
            try:
                v = _voice()
                text = v.listen()
                return self._json({'ok': True, 'text': text})
            except Exception as e:
                return self._json({'ok': False, 'error': f'{type(e).__name__}: {str(e)[:90]}'})

        if u.path == '/api/send':
            text = (payload.get('text') or '').strip()
            if not text:
                return self._json({'ok': False, 'error': '空消息'})
            try:
                d = _brain()
                iid = d['store'].record_stimulus(
                    actor_id='local:owner', actor_name='主人', kind='chat', text=text)
                r = d['speak'](d['book'], text, kind='chat', actor_name='主人',
                               actor_id='local:owner', idle_pool=d['pool'])
                if r.text is None:
                    return self._json({'ok': False, 'error': '裁决过滤，选择沉默'})
                d['store'].record_response(iid, response=r.text, acts=r.acts,
                                           mood=r.mood, latency_ms=0)
                d['body'].speak(r.text, r.acts, expr=r.expr, motion=r.motion)
                try:
                    from lingshu import enqueue
                    enqueue('主人', text, r.text, kind='chat',
                            mood=r.mood, acts=r.acts, importance=0.6)
                except Exception:
                    pass
                return self._json({'ok': True, 'reply': r.text})
            except Exception as e:
                import traceback; traceback.print_exc()
                return self._json({'ok': False, 'error': f'{type(e).__name__}: {str(e)[:90]}'})

        return self._json({'error': 'not found'}, 404)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            body = render(parse_qs(u.query))
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if u.path == "/api/records":
            body = json.dumps(all_records(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *a):
        pass


def main() -> int:
    global DATA
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8792)
    ap.add_argument("--data", help="数据目录（默认优先仓根 data/）")
    args = ap.parse_args()
    if args.data:
        DATA = Path(args.data)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"对话记录页面: http://127.0.0.1:{args.port}", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())