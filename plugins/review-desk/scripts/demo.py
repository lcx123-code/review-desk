"""Start a self-contained live demo. No changes to the user's coding projects."""
import argparse
import functools
import json
from pathlib import Path
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from review_desk import Store, PanelServer, atomic_json

parser = argparse.ArgumentParser()
parser.add_argument("--workspace", required=True, type=Path)
args = parser.parse_args()
args.workspace.mkdir(parents=True, exist_ok=True)
demo = Path(tempfile.mkdtemp(prefix="review-desk-demo-", dir=args.workspace))
project = demo / "project"
project.mkdir()
before_html = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>专注计时器</title><link rel="stylesheet" href="styles.css"></head>
<body><main><p class="eyebrow">FOCUS</p><h1>专注计时器</h1>
<p class="sub">保持专注，成就更好的自己</p>
<div class="ring"><div class="ring-inner"><span id="time">25:00</span><span class="sub" id="status">准备开始</span></div></div>
<div class="actions"><button id="start">开始专注</button></div>
<div class="metrics"><div><span>专注时长</span><strong>25 分钟</strong></div><div><span>已专注</span><strong>0 次</strong></div><div><span>今日总计</span><strong>0 分钟</strong></div></div>
</main><script src="timer.js"></script></body></html>
"""
before_css = """* { box-sizing: border-box; }
body { font: 14px 'Segoe UI',Arial,'Microsoft YaHei',sans-serif; background:#fff; color:#25312b; margin:0; }
main { max-width:430px; margin:0 auto; padding:24px 20px; text-align:center; }
h1 { font-size:21px; font-weight:600; margin:5px 0; letter-spacing:-.4px; }
.eyebrow { color:#6c8d7d; letter-spacing:3px; font-size:10px; margin:0 0 9px; }
.sub { color:#8a9690; font-size:12px; margin:7px 0; }
.ring { margin:24px auto; padding:10px; width:210px; height:210px; border-radius:50%; background:conic-gradient(#548e75 0deg 58deg,#eaf1ed 58deg 360deg); }
.ring-inner { height:100%; width:100%; background:#fff; border-radius:50%; display:flex; align-items:center; justify-content:center; flex-direction:column; }
#time { font-size:46px; font-weight:600; letter-spacing:-2px; font-variant-numeric:tabular-nums; }
.actions { display:flex; justify-content:center; gap:10px; }
button { min-width:110px; padding:9px 13px; border:1px solid #216651; border-radius:5px; background:#216651; color:white; cursor:pointer; font:inherit; }
button:hover { filter:brightness(.96); }
button:focus-visible { outline:2px solid #548e75; outline-offset:4px; }
.metrics { display:grid; grid-template-columns:repeat(3,1fr); gap:7px; margin-top:23px; }
.metrics div { display:flex; flex-direction:column; gap:7px; background:#f8faf9; border:1px solid #e6ede9; border-radius:6px; padding:13px 5px; }
.metrics span { font-size:10px; color:#929e97; }
.metrics strong { font-size:13px; font-weight:500; }
@media(max-width:340px) { main { padding:22px 10px; } .ring { width:185px; height:185px; } }
"""
before_js = """let remaining = 1500;
let timer = null;
document.getElementById('start').onclick = () => {
  timer = setInterval(() => {
    remaining -= 1;
    document.getElementById('time').textContent = `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}`;
  }, 1000);
};
"""
for name, content in [('index.html', before_html), ('styles.css', before_css), ('timer.js', before_js)]:
    (project / name).write_text(content, encoding="utf-8")
store = Store(demo / "data")
s = store.begin(str(project), "计时器 · 暂停与重置", files=["index.html", "styles.css", "timer.js"])
after_html = before_html.replace('<button id="start">开始专注</button>', '<button id="start">开始专注</button><button id="reset" class="secondary">重置</button>')
after_css = before_css.replace('padding:9px 13px', 'padding:12px 17px').replace('border-radius:5px', 'border-radius:7px')
after_css += ".secondary { background:#fff; color:#52665b; border-color:#dce5df; }\n"
after_js = """let remaining = 1500;
let timer = null;
const start = document.getElementById('start');
const status = document.getElementById('status');
function updateDisplay() {
  document.getElementById('time').textContent = `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}`;
}
function pause() {
  clearInterval(timer);
  timer = null;
  start.textContent = '继续专注';
  status.textContent = '已暂停';
}
start.onclick = () => {
  if (timer) { pause(); return; }
  if (remaining <= 0) remaining = 1500;
  start.textContent = '暂停';
  status.textContent = '专注中';
  timer = setInterval(() => {
    remaining = Math.max(0, remaining - 1);
    updateDisplay();
    if (remaining === 0) {
      pause();
      status.textContent = '本次专注已完成';
      start.textContent = '重新开始';
    }
  }, 1000);
};
document.getElementById('reset').onclick = () => {
  pause();
  remaining = 1500;
  updateDisplay();
  start.textContent = '开始专注';
  status.textContent = '准备开始';
};
"""
for name, content in [('index.html', after_html), ('styles.css', after_css), ('timer.js', after_js)]:
    (project / name).write_text(content, encoding="utf-8")

class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass

preview = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(project)))
threading.Thread(target=preview.serve_forever, daemon=True).start()
preview_url = f"http://127.0.0.1:{preview.server_address[1]}"
s = store.refresh(s["id"], summary="新增暂停、继续和重置，避免重复创建计时器。请检查代码与运行效果。", preview_url=preview_url,
                  checks=[{"name": "示例项目：等待运行交互验证", "status": "not_run"}])
panel = PanelServer(store)
panel.start()
info = {"url": panel.url(s["id"]), "previewUrl": preview_url, "session": s["id"], "dataDir": str(store.root), "project": str(project)}
atomic_json(args.workspace / "demo-info.json", info)
print(json.dumps({"url": info["url"], "session": s["id"]}, ensure_ascii=False), flush=True)
try:
    while time.monotonic() - panel.last_used < 7200:
        time.sleep(10)
except KeyboardInterrupt:
    pass
finally:
    panel.close()
    preview.shutdown()
    preview.server_close()
