"""Live keypad simulator in the browser. The firmware's real UI and key logic run on this PC; click the keys
(or type 1-8, arrow keys, Enter = 7, Esc = 5) and send host messages, no hardware needed.

    python firmware/sim/build.py      # once
    python firmware/sim/live.py       # then open http://127.0.0.1:8765
"""
import io
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).parent))
import scenarios as sc  # noqa: E402
from keypad_sim import Sim  # noqa: E402

PORT = 8765
lock = threading.Lock()
sim = Sim()
sim.msg(sc.HELLO)
sim.msg(sc.SETTINGS)
sim.msg(sc.status())

SCENARIOS = {"status": sc.status(), **{f"mode: {m}": sc.status(mode=m) for m in sc.MODES},
             "asking": sc.status(state="permission", queue=2), "paused": sc.status(paused=True),
             "no sessions": {"t": "status", "sessions": [], "sel": "", "pinned": False, "queue": 0, "paused": False, "menu": False},
             **{f"screen: {k}": v for k, v in sc.SCREENS.items()}}

PAGE = """<!doctype html><meta charset=utf-8><title>Keypad simulator</title>
<style>
body{font:14px system-ui;background:#1b1b1f;color:#ddd;margin:0;padding:16px;display:flex;gap:24px;flex-wrap:wrap}
img{width:640px;image-rendering:pixelated;border:6px solid #333;border-radius:10px;background:#000;display:block}
.keys{display:grid;grid-template-columns:repeat(4,70px);gap:8px;margin-top:12px}
button{background:#33363d;color:#eee;border:1px solid #555;border-radius:8px;padding:12px 0;font:600 15px system-ui;cursor:pointer}
button:active{background:#57a}.small{padding:6px 10px;font-weight:400}
select,textarea{width:100%;background:#222;color:#ddd;border:1px solid #555;border-radius:6px;box-sizing:border-box}
textarea{height:110px;font:12px monospace}pre{background:#111;padding:8px;height:200px;overflow:auto;font-size:12px;border-radius:6px}
.col{width:340px}h3{margin:14px 0 6px;font-size:13px;color:#999}
</style>
<div><img id=f src=/frame.png>
<div class=keys>
<button onclick=k(1)>1</button><button onclick=k(2)>2</button><button onclick=k(3)>3</button><button onclick=k(4)>4 &#9650;</button>
<button onclick=k(5)>5 Esc</button><button onclick=k(6)>6 list</button><button onclick=k(7)>7 Enter</button><button onclick=k(8)>8 &#9660;</button></div>
<div style=margin-top:10px>
<button class=small onclick=t(-1)>&#8634; turn</button> <button class=small onclick=t(1)>turn &#8635;</button>
<button class=small onclick=k(0)>encoder click</button> <button class=small onclick=k(9)>mic</button></div></div>
<div class=col><h3>Show what the host sends</h3><select id=sc size=1 onchange=scn(this.value)></select>
<h3>Raw message (JSON)</h3><textarea id=raw placeholder='{"t":"screen",...}'></textarea>
<button class=small onclick=raw()>send</button>
<h3>The keypad sent to the host</h3><pre id=tx></pre></div>
<script>
const $=id=>document.getElementById(id);
async function post(u,b){await fetch(u,{method:'POST',body:b||''});}
const k=n=>post('/key?k='+n), t=n=>post('/turn?n='+n), scn=v=>post('/scenario?name='+encodeURIComponent(v)), raw=()=>post('/msg',$('raw').value);
fetch('/scenarios').then(r=>r.json()).then(l=>{$('sc').innerHTML='<option>-</option>'+l.map(x=>'<option>'+x+'</option>').join('')});
addEventListener('keydown',e=>{if(e.target.tagName=='TEXTAREA'||e.target.tagName=='SELECT')return;
 const m={'1':1,'2':2,'3':3,'4':4,'5':5,'6':6,'7':7,'8':8,'ArrowUp':4,'ArrowDown':8,'Enter':7,'Escape':5,'0':0};
 if(e.key in m){k(m[e.key]);e.preventDefault()}});
setInterval(()=>{$('f').src='/frame.png?'+Date.now()},200);
setInterval(async()=>{$('tx').textContent=(await (await fetch('/tx')).json()).join('\\n')},700);
</script>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, body: bytes, ctype="application/json"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            self.reply(PAGE.encode(), "text/html; charset=utf-8")
        elif u.path == "/frame.png":
            with lock:
                im = sim.image().resize((960, 510))
            buf = io.BytesIO()
            im.save(buf, "PNG")
            self.reply(buf.getvalue(), "image/png")
        elif u.path == "/scenarios":
            self.reply(json.dumps(list(SCENARIOS)).encode())
        elif u.path == "/tx":
            with lock:
                self.reply(json.dumps([json.dumps(m, ensure_ascii=False) for m in sim.tx[-30:]]).encode())
        else:
            self.send_error(404)

    def do_POST(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8")
        with lock:
            try:
                if u.path == "/key":
                    sim.key(int(q["k"]))
                elif u.path == "/turn":
                    sim.turn(int(q["n"]))
                elif u.path == "/scenario" and q.get("name") in SCENARIOS:
                    sim.msg(SCENARIOS[q["name"]])
                elif u.path == "/msg":
                    sim.msg(json.loads(body))
            except (ValueError, KeyError) as e:
                print("bad request:", e)
        self.reply(b"{}")


def tick():
    n = 0
    while True:   # let simulated time pass so the spinner, countdowns and dimming run
        time.sleep(0.1)
        with lock:
            sim.wait(100)
            n += 1
            if n % 20 == 0:   # the host pings every 2 s; without it the keypad shows "Waiting for your computer"
                sim.msg({"t": "ping"})


threading.Thread(target=tick, daemon=True).start()
print(f"Keypad simulator on http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
