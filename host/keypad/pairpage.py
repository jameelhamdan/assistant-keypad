"""The pairing page: a small local web page (127.0.0.1, random port, secret token in the
address) where you name a keypad plugged in with USB, pick its Wi-Fi and type the password.
It replaces three native pop-up dialogs with one form that looks the same on macOS and
Windows, and it shows the result live (USB seen, paired, joined Wi-Fi). The agent serves it
only after the tray asks for its address."""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import osutil, proto

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Set up a keypad</title>
<style>
:root{color-scheme:light dark;--bg:#fff;--fg:#1d1d1f;--mut:#6e6e73;--line:#d2d2d7;--acc:#d77757}
@media(prefers-color-scheme:dark){:root{--bg:#161618;--fg:#f2f2f2;--mut:#a1a1a6;--line:#3a3a3c}}
body{font:16px system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0;display:grid;place-items:center;min-height:100vh}
main{width:min(420px,calc(100vw - 32px));padding:24px 0}h1{font-size:22px;margin:0 0 4px}
p{color:var(--mut);margin:4px 0 16px}label{display:block;font-size:13px;color:var(--mut);margin:12px 0 4px}
input{width:100%;box-sizing:border-box;padding:10px;font:inherit;border:1px solid var(--line);border-radius:8px;background:transparent;color:inherit}
button{margin-top:20px;width:100%;padding:11px;font:inherit;font-weight:600;border:0;border-radius:8px;background:var(--acc);color:#fff;cursor:pointer}
button:disabled{opacity:.5;cursor:default}#st{margin-top:16px;font-size:14px}.ok{color:#2e9d4f}.bad{color:#d1384f}
</style>
<main><h1>Set up a keypad</h1>
<p id=usb>Plug the keypad into this computer with a USB-C cable (not a charge-only one)...</p>
<form id=f hidden>
<label>Name</label><input id=name maxlength=24 value="__NAME__">
<label>Wi-Fi network (2.4 GHz)</label><input id=ssid value="__SSID__" required autocomplete=off>
<label>Wi-Fi password (stored only on the keypad)</label><input id=pw type=password autocomplete=off>
<button id=go>Pair keypad</button></form><div id=st></div></main>
<script>
const T="__TOKEN__",$=i=>document.getElementById(i);let id="",paired=false;
const get=p=>fetch(p+"?t="+T).then(r=>r.json());
async function tick(){try{const s=await get("/state");
 const k=s.keypads.find(k=>k.link=="wifi"&&k.id==id);
 if(paired&&k){$("st").innerHTML="<span class=ok>Connected over Wi-Fi ("+k.addr+"). You can unplug the keypad.</span>";return}
 const u=s.keypads.find(k=>k.link=="usb");
 if(u&&!paired){id=u.id;$("usb").textContent="Found "+u.id+" over USB.";$("f").hidden=false;if(!$("name").dataset.set){$("name").value=u.name&&u.name!=u.id?u.name:$("name").value;$("name").dataset.set=1}}
 else if(!u&&!paired){$("usb").textContent="Plug the keypad into this computer with a USB-C cable (not a charge-only one)...";$("f").hidden=true}
 if(s.problem&&paired)$("st").innerHTML="<span class=bad>"+s.problem+"</span>";
}catch(e){}setTimeout(tick,1500)}
$("f").onsubmit=async e=>{e.preventDefault();$("go").disabled=true;$("st").textContent="Pairing...";
 const r=await fetch("/pair?t="+T,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id,name:$("name").value,ssid:$("ssid").value,pass:$("pw").value})});
 const j=await r.json();if(j.ok){paired=true;$("f").hidden=true;$("usb").textContent="Paired. The keypad is joining "+$("ssid").value+"...";$("st").textContent=""}
 else{$("st").innerHTML="<span class=bad>"+(j.error||"Pairing failed")+"</span>";$("go").disabled=false}};
tick();
</script>"""


class PairPage:
    def __init__(self, a: Any, hub: Any):
        self.a, self.hub = a, hub
        self.token = secrets.token_urlsafe(24)
        self._srv: ThreadingHTTPServer | None = None
        self._lock = threading.Lock()

    def url(self) -> str:
        """The page's address, starting its server on first use."""
        with self._lock:
            if self._srv is None:
                self._srv = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
                threading.Thread(target=self._srv.serve_forever, daemon=True).start()
            return f"http://127.0.0.1:{self._srv.server_address[1]}/?t={self.token}"

    def close(self) -> None:
        with self._lock:
            if self._srv:
                self._srv.shutdown()
                self._srv = None

    def state(self) -> dict[str, Any]:
        s = self.a.snapshot()
        return {"keypads": [{k: c.get(k) for k in ("id", "name", "link", "addr")} for c in s["keypads"]],
                "problem": next(iter(s.get("problems", {}).values()), "")}

    def provision(self, b: dict[str, Any]) -> dict[str, Any]:
        dev_id, ssid = str(b.get("id", "")), str(b.get("ssid", "")).strip()
        if not ssid:
            return {"ok": False, "error": "Enter the Wi-Fi network name."}
        try:
            self.hub.provision(dev_id, ssid, str(b.get("pass", "")), proto.fit(str(b.get("name", "")).strip(), proto.DEVICE_NAME))
        except Exception as e:  # shown on the page
            return {"ok": False, "error": f"Pairing failed: {e}"}
        return {"ok": True}

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        page = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_: Any) -> None:
                pass

            def _allowed(self) -> bool:
                host = self.headers.get("Host", "")  # a page on another site cannot reach us (DNS rebinding)
                query = self.path.partition("?")[2]
                return host == f"127.0.0.1:{self.server.server_address[1]}" and f"t={page.token}" in query.split("&")

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code: int, obj: Any) -> None:
                self._send(code, json.dumps(obj).encode(), "application/json")

            def do_GET(self) -> None:
                if not self._allowed():
                    return self._send(403, b"forbidden", "text/plain")
                path = self.path.partition("?")[0]
                if path == "/state":
                    return self._json(200, page.state())
                if path != "/":
                    return self._send(404, b"not found", "text/plain")
                html = (PAGE.replace("__TOKEN__", page.token).replace("__SSID__", _attr(osutil.ssid()))
                        .replace("__NAME__", "Desk keypad"))
                self._send(200, html.encode(), "text/html; charset=utf-8")

            def do_POST(self) -> None:
                if not self._allowed() or self.path.partition("?")[0] != "/pair":
                    return self._send(403, b"forbidden", "text/plain")
                try:
                    n = int(self.headers.get("Content-Length", "0"))
                    body = json.loads(self.rfile.read(min(n, 4096)) or b"{}")
                except ValueError:
                    return self._json(400, {"ok": False, "error": "bad request"})
                self._json(200, page.provision(body if isinstance(body, dict) else {}))

        return H


def _attr(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
