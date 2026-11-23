"""本地网页界面：双击「启动校对工具.bat」打开，拖进 PDF 或图片就能校对。

只用标准库 + 本项目依赖（pymupdf / numpy / pillow），全程不联网。
"""
from __future__ import annotations

import datetime
import json
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "output")
FONTS = os.path.join(ROOT, "fonts")
PORT = 8765

UPLOAD_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>台历日期校对</title>
<style>
:root{--ink:#1a1a1a;--muted:#6b6b6b;--line:#e0ded9;--bg:#faf9f7;--accent:#2f6fed}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.7 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:720px;margin:0 auto;padding:56px 24px 80px}
h1{font-size:27px;font-weight:600;margin:0 0 8px;letter-spacing:-.01em}
.sub{color:var(--muted);margin-bottom:34px}
#drop{border:1.5px dashed #cfccc6;border-radius:16px;background:#fff;padding:52px 24px;
text-align:center;cursor:pointer;transition:.15s}
#drop:hover,#drop.on{border-color:var(--accent);background:#f6f9ff}
#drop b{font-size:17px;display:block;margin-bottom:6px}
#drop span{color:var(--muted);font-size:13.5px}
#name{margin-top:14px;color:var(--accent);font-size:14px}
.opts{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin:26px 0 22px}
label{display:block;font-size:13px;color:var(--muted);margin-bottom:6px}
input,select{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:9px;
background:#fff;font:inherit;color:var(--ink)}
button{width:100%;padding:14px;border:0;border-radius:11px;background:var(--accent);color:#fff;
font:600 16px/1 system-ui,"Microsoft YaHei";cursor:pointer}
button:disabled{background:#c3cddf;cursor:default}
#msg{margin-top:18px;font-size:14px;color:var(--muted);text-align:center;min-height:22px}
.tip{margin-top:34px;font-size:13px;color:var(--muted);border-top:1px solid var(--line);padding-top:18px}
.tip code{background:#f1efec;padding:1px 6px;border-radius:4px}
</style></head><body><div class="wrap">
<h1>台历日期校对</h1>
<div class="sub">拖入已转曲的矢量 PDF，或者直接拖一张日历图片（JPG / PNG），自动核对每个日期和星期的位置。</div>
<div id="drop"><b>把 PDF 或图片拖到这里</b><span>或点击选择文件</span><div id="name"></div></div>
<input type="file" id="file" accept="application/pdf,image/*" hidden>
<div class="opts">
  <div><label>台历年份</label><input type="number" id="year" value="__YEAR__" min="1990" max="2100"></div>
  <div><label>第一列是</label><select id="col"><option value="auto">自动判断</option><option value="mon">周一</option><option value="sun">周日</option></select></div>
  <div><label>第一个月份页是</label><select id="sm">__MONTHS__</select></div>
  <div><label>&nbsp;</label><button id="go">开始校对</button></div>
</div>
<div id="msg"></div>
<div class="tip">不需要字体文件：工具直接把文件里的字形按形状分组（同一个数字形状完全一样），
再用历法位置反推它是几号。整本 12 页一般 3~5 秒出结果。</div>
</div>
<script>
const drop=document.getElementById('drop'),file=document.getElementById('file'),name=document.getElementById('name');
let picked=null;
drop.onclick=()=>file.click();
file.onchange=e=>{if(e.target.files[0])set(e.target.files[0])};
['dragenter','dragover'].forEach(t=>drop.addEventListener(t,e=>{e.preventDefault();drop.classList.add('on')}));
['dragleave','drop'].forEach(t=>drop.addEventListener(t,e=>{e.preventDefault();drop.classList.remove('on')}));
drop.addEventListener('drop',e=>{const f=e.dataTransfer.files[0];if(f)set(f)});
function set(f){const n=f.name.toLowerCase();
  const ok=n.endsWith('.pdf')||/\.(png|jpg|jpeg|webp|bmp|tif|tiff)$/.test(n);
  if(!ok){alert('请选择 PDF 或图片文件（JPG / PNG 等）');return}
  picked=f;name.textContent=f.name+'  ('+(f.size/1048576).toFixed(1)+' MB)';}
document.getElementById('go').onclick=async()=>{
  if(!picked){alert('请先选择文件');return}
  const btn=document.getElementById('go'),msg=document.getElementById('msg');
  btn.disabled=true;btn.textContent='校对中…';msg.textContent='正在识别版面，请稍等';
  try{
    const r=await fetch('/check',{method:'POST',body:picked,headers:{
      'X-Year':document.getElementById('year').value,
      'X-FirstCol':document.getElementById('col').value,
      'X-StartMonth':document.getElementById('sm').value,
      'X-Name':encodeURIComponent(picked.name)}});
    const j=await r.json();
    if(!r.ok)throw new Error(j.error||'处理失败');
    msg.textContent='完成，正在打开报告…';
    location.href=j.url;
  }catch(e){msg.textContent='出错了：'+e.message;btn.disabled=false;btn.textContent='开始校对';}
};
</script></body></html>"""

class Handler(BaseHTTPRequestHandler):
    server_version = "calproof/0.2"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body: bytes, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            year = str(datetime.date.today().year + 1)
            months = "".join(f'<option value="{i}">{i} 月</option>' for i in range(1, 13))
            html = UPLOAD_HTML.replace("__YEAR__", year).replace("__MONTHS__", months)
            return self._send(200, html.encode("utf-8"))
        if path.startswith("/out/"):
            rel = unquote(path[len("/out/"):]).replace("\\", "/")
            full = os.path.normpath(os.path.join(OUT, rel))
            if full.startswith(os.path.abspath(OUT)) and os.path.isfile(full):
                ext = os.path.splitext(full)[1].lower()
                ctype = {".html": "text/html; charset=utf-8", ".png": "image/png",
                         ".json": "application/json"}.get(ext, "application/octet-stream")
                with open(full, "rb") as f:
                    return self._send(200, f.read(), ctype)
        return self._send(404, b"not found")

    def do_POST(self):
        if urlparse(self.path).path != "/check":
            return self._send(404, b"not found")
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(n)
            year = int(self.headers.get("X-Year", "2027"))
            first_col = self.headers.get("X-FirstCol", "auto")
            start_month = int(self.headers.get("X-StartMonth", "1"))

            stamp = time.strftime("%Y%m%d_%H%M%S")
            job = f"{stamp}_{year}"
            jd = os.path.join(OUT, job)
            os.makedirs(jd, exist_ok=True)

            rawname = unquote(self.headers.get("X-Name", ""))
            ext = os.path.splitext(rawname)[1].lower()
            if ext not in (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"):
                ext = ".pdf"
            upload = os.path.join(jd, "input" + ext)
            with open(upload, "wb") as f:
                f.write(data)

            pdf = upload
            if ext != ".pdf":
                # 图片：先包成单页 PDF，好让后面的流程统一处理
                from calproof.cli import _image_to_pdf
                pdf = _image_to_pdf(upload, year)

            from calproof.pipeline import run
            from calproof.report import build_report

            res = run(pdf, year, first_col, start_month,
                      FONTS if os.path.isdir(FONTS) else None)
            report = os.path.join(jd, "report.html")
            build_report(res, report)

            payload = json.dumps({
                "url": f"/out/{job}/report.html",
                "n_error": res["n_error"],
                "n_warn": res["n_warn"],
            }).encode("utf-8")
            return self._send(200, payload, "application/json; charset=utf-8")
        except Exception as e:
            traceback.print_exc()
            payload = json.dumps({"error": str(e)}).encode("utf-8")
            return self._send(500, payload, "application/json; charset=utf-8")

def _free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0

def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(FONTS, exist_ok=True)
    port = PORT
    while not _free(port) and port < PORT + 20:
        port += 1
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print("=" * 50)
    print("  台历日期校对 已启动")
    print(f"  浏览器地址：{url}")
    print("  用完后关掉这个窗口即可")
    print("=" * 50)
    if not os.environ.get("CALPROOF_NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")

if __name__ == "__main__":
    sys.exit(main())
