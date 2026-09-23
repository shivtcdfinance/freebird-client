#!/usr/bin/env python3
"""freebird_ui — the control panel. What a person actually uses.

WHY THIS EXISTS
  A launcher in a terminal is not an app. Someone installing Freebird should be able to SEE the
  number that matters — how much compute this machine has contributed — change their ceiling with
  a slider, and stop it with one click.

WHAT IT SHOWS
  Quantities only: this week, last week, this month, last month, all time — in minutes and hours,
  plus how many jobs and how many machines. It deliberately does NOT show what the work was:
  Freebird reports HOW MUCH a machine gave, never WHAT it processed. That is the owner's rule and
  it is also the honest thing — a customer is entitled to know the size of their contribution, not
  to a window into other tenants' workload.

WHERE THE NUMBERS COME FROM
  usage  -> the bus, asked with THIS machine's own key (so it can only ever return its own totals)
  limits -> the live container (docker/podman inspect), i.e. what is actually enforced
  usage% -> the kernel's cgroup accounting via the container, not a self-report

Runs on 127.0.0.1 only. It is a local control panel, not a network service.
"""
from __future__ import annotations
import json
import os
import threading
import urllib.error
import urllib.request
import webbrowser

import freebird as fb

UA = "freebird-ui/0.4 (+https://shivelinc.com)"
PORT = int(os.environ.get("FREEBIRD_UI_PORT") or 8787)


def _token():
    try:
        return open(fb.TOKEN).read().strip()
    except Exception:
        return ""


def _call(base, path, obj=None, timeout=8):
    """Talk to the bus. Returns (data, error) — an error is REPORTED, never rendered as zero,
    because a dashboard showing 0 is indistinguishable from a dashboard that cannot reach
    anything, and that is exactly the kind of lie a person cannot detect."""
    if not base:
        return None, "no bus configured"
    req = urllib.request.Request(base.rstrip("/") + path,
                                 data=json.dumps(obj).encode() if obj else None,
                                 headers={"Content-Type": "application/json",
                                          "X-Bus-Token": _token(), "User-Agent": UA})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read() or b"{}"), None
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"{}")
            return None, body.get("error") or ("HTTP %s" % e.code)
        except Exception:
            return None, "HTTP %s" % e.code
    except Exception as e:
        return None, type(e).__name__


def usage(cfg):
    """This machine's own contribution, from the bus. Tries the private door first if one is set."""
    cands = [cfg.get("bus_url_tailnet"), cfg.get("bus_url")]
    err = "not tried"
    for base in cands:
        if not base:
            continue
        d, err = _call(base, "/usage")
        if d:
            return d, None, base
    return None, err, (cfg.get("bus_url_tailnet") or cfg.get("bus_url") or "")


def state():
    cfg = fb.load_cfg()
    c = fb.current()
    cores, host = fb.cpu_cores(cfg)
    rep = None
    try:
        rep = json.load(open(os.path.join(fb.HOME, "status.json")))
    except Exception:
        pass
    usg, err, base = usage(cfg)
    return {
        "machine": cfg.get("worker_name") or "this machine",
        "host_cores": host,
        "config": {k: cfg.get(k) for k in ("cpu_percent_max", "ram_mb_max", "disk_mb_max",
                                           "contribute", "worker_name", "bus_url_tailnet")},
        "would_apply": {"cpus": cores, "mem_mb": int(cfg.get("ram_mb_max") or 1024)},
        "container": c,
        "report": rep,
        "usage": usg,
        "usage_error": err,
        "usage_source": base,
        "runtime": fb.RUNTIME or "detecting",
    }


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Freebird</title><style>
:root{--deep:#3F3DA8;--deep2:#2C2A7A;--ink:#14142B;--mut:#5A5A7A;--line:#E4E4F0;
--bg:#F5F6FB;--ok:#128A5B;--bad:#B3322C;--warn:#9A6206}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif}
.wrap{max-width:880px;margin:0 auto;padding:28px 20px 60px}
header{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:6px}
.logo{width:34px;height:34px;border-radius:9px;background:linear-gradient(135deg,var(--deep),#6B69D8)}
h1{font-size:22px;margin:0;letter-spacing:-.3px}
.sub{color:var(--mut);font-size:13px;margin:2px 0 22px}
.pill{display:inline-flex;align-items:center;gap:7px;padding:5px 12px;border-radius:999px;
font-size:12px;font-weight:600;background:#fff;border:1px solid var(--line);margin-left:auto}
.dot{width:8px;height:8px;border-radius:50%;background:var(--mut)}
.dot.on{background:var(--ok);box-shadow:0 0 0 3px rgba(18,138,91,.16)}
.dot.off{background:var(--bad)}
.card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:20px 22px;margin-bottom:16px}
.hero{background:linear-gradient(135deg,var(--deep),var(--deep2));color:#fff;border:0}
.hero h2{margin:0 0 2px;font-size:13px;font-weight:600;letter-spacing:.4px;text-transform:uppercase;opacity:.82}
.big{display:flex;gap:34px;flex-wrap:wrap;margin-top:12px}
.big div{min-width:150px}
.big .n{font-size:34px;font-weight:700;letter-spacing:-1px;line-height:1.1}
.big .l{font-size:12.5px;opacity:.82;margin-top:3px}
h3{margin:0 0 12px;font-size:14px;letter-spacing:.2px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:right;padding:8px 0;border-bottom:1px solid var(--line)}
th:first-child,td:first-child{text-align:left}
th{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.4px}
tr:last-child td{border-bottom:0}
td.n{font-variant-numeric:tabular-nums;font-weight:600}
.row{display:flex;align-items:center;gap:14px;margin:14px 0}
.row label{flex:0 0 190px;font-size:13.5px}
.row .val{flex:0 0 92px;text-align:right;font-weight:600;font-variant-numeric:tabular-nums}
input[type=range]{flex:1;accent-color:var(--deep)}
.hint{color:var(--mut);font-size:12.5px;margin-top:-4px}
button{font:inherit;font-weight:600;padding:10px 18px;border-radius:10px;border:1px solid var(--deep);
background:var(--deep);color:#fff;cursor:pointer}
button.ghost{background:#fff;color:var(--deep)}
button.danger{background:#fff;color:var(--bad);border-color:#E9C9C7}
button:disabled{opacity:.5;cursor:not-allowed}
.note{background:#F0F0FA;border:1px solid #DCDCF0;border-radius:10px;padding:12px 14px;font-size:12.5px;color:var(--mut)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px}
.kv{font-size:12.5px;color:var(--mut)}
.kv b{display:block;color:var(--ink);font-size:15px;font-weight:600;margin-top:2px}
.warn{color:var(--warn);font-size:12.5px}
.bad{color:var(--bad);font-size:12.5px}
footer{color:var(--mut);font-size:12px;text-align:center;margin-top:26px}
</style></head><body><div class="wrap">
<header>
  <div class="logo"></div>
  <div><h1>Freebird</h1></div>
  <span class="pill"><span class="dot" id="dot"></span><span id="pilltxt">checking</span></span>
</header>
<div class="sub" id="sub">&nbsp;</div>

<div class="card hero">
  <h2>Compute this machine has contributed</h2>
  <div class="big">
    <div><div class="n" id="w_h">0</div><div class="l">contributed this week</div></div>
    <div><div class="n" id="m_h">0</div><div class="l">contributed this month</div></div>
    <div><div class="n" id="a_h">0</div><div class="l">contributed all time</div></div>
  </div>
  <div class="l" id="herosub" style="margin-top:14px;opacity:.82">&nbsp;</div>
</div>

<div class="card">
  <h3>Weekly and monthly</h3>
  <table><thead><tr><th>Period</th><th>Jobs</th><th>Compute</th></tr></thead>
  <tbody id="usage"></tbody></table>
  <div class="hint" id="usagenote" style="margin-top:10px"></div>
</div>

<div class="card">
  <h3>Ceiling</h3>
  <div class="row"><label>CPU — max share of this machine</label>
    <input type="range" id="cpu" min="5" max="100" step="5"><span class="val" id="cpuv">20%</span></div>
  <div class="row"><label>Memory cap</label>
    <input type="range" id="ram" min="256" max="8192" step="256"><span class="val" id="ramv">1024 MB</span></div>
  <div class="row"><label>Scratch disk</label>
    <input type="range" id="disk" min="128" max="4096" step="128"><span class="val" id="diskv">512 MB</span></div>
  <div class="row"><label>Help the network with spare capacity</label>
    <input type="checkbox" id="contrib" style="width:18px;height:18px;accent-color:var(--deep)">
    <span class="hint">Off means your machine only ever does your own work.</span></div>
  <div class="hint" id="would" style="margin:6px 0 16px"></div>
  <div style="display:flex;gap:10px;flex-wrap:wrap">
    <button id="apply">Apply</button>
    <button class="ghost" id="refresh">Refresh</button>
    <button class="danger" id="stop">Stop</button>
  </div>
  <div id="msg" style="margin-top:12px" class="hint"></div>
</div>

<div class="card">
  <h3>Right now</h3>
  <div class="grid">
    <div class="kv">Enforced ceiling<b id="k_ceil">&mdash;</b></div>
    <div class="kv">Actually in use<b id="k_use">&mdash;</b></div>
    <div class="kv">Enforced by<b id="k_enf">&mdash;</b></div>
    <div class="kv">Started<b id="k_up">&mdash;</b></div>
  </div>
  <div class="note" style="margin-top:16px">
    The limit is applied by the operating system kernel (a cgroup quota), not by the software
    agreeing to behave. This panel reports how <em>much</em> this machine contributed — never
    <em>what</em> it processed: the work is other tenants' data, and its contents are not this
    machine's business.
  </div>
</div>
<footer>Freebird client &middot; local control panel on 127.0.0.1 &middot; nothing connects in to this machine</footer>
</div><script>
const $=id=>document.getElementById(id);
// Read the number in the unit that FITS it. A real 40 ms job rendered as "0.0 minutes" looks like
// a broken panel; the same job as "0.04 s" is a measurement. Every branch shows real time.
function fmt(sec){sec=sec||0; if(sec<60)return sec.toFixed(sec<1?2:1)+' s';
  if(sec<3600)return (sec/60).toFixed(1)+' min'; return (sec/3600).toFixed(2)+' h'}
function mins(x){return (x||0).toFixed(1)}
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
let S=null;
async function load(){
  const r=await fetch('/api/state');S=await r.json();
  const c=S.container, rep=S.report||{};
  const running=!!(c&&c.running);
  $('dot').className='dot '+(running?'on':'off');
  $('pilltxt').textContent=running?'running':'stopped';
  $('sub').innerHTML='machine <b>'+esc(S.machine)+'</b> &middot; '+S.host_cores+' cores &middot; runtime '+esc(S.runtime);
  $('k_ceil').textContent=c?(c.cpus.toFixed(2)+' cores / '+c.mem_mb+' MB'):'not running';
  $('k_use').textContent=rep.workers_rss_mb!=null?(mins(rep.workers_rss_mb)+' MB RAM'):'—';
  $('k_enf').textContent=rep.enforced_by||'—';
  $('k_up').textContent=rep.updated||'—';
  const u=S.usage;
  const rows=[['This week','this_week'],['Last week','last_week'],['This month','this_month'],
              ['Last month','last_month'],['All time','all_time']];
  if(u){
    $('usage').innerHTML=rows.map(([lbl,k])=>{const d=u[k]||{};
      return '<tr><td>'+lbl+'</td><td class="n">'+(d.jobs||0)+'</td><td class="n">'
      +fmt(d.compute_s)+'</td></tr>'}).join('');
    $('w_h').textContent=fmt((u.this_week||{}).compute_s);
    $('m_h').textContent=fmt((u.this_month||{}).compute_s);
    $('a_h').textContent=fmt((u.all_time||{}).compute_s);
    $('herosub').textContent=(u.machines||0)+' machine(s) linked to '+(u.identity&&u.identity.email?u.identity.email:'this key')
      +'  ·  reported by '+(S.usage_source||'the bus');
    $('usagenote').textContent='Quantities only. Freebird does not report which processes ran.';
  }else{
    $('usage').innerHTML='<tr><td colspan="3" class="bad">Cannot reach the bus'+(S.usage_source?(' at '+esc(S.usage_source)):'')+': '+esc(S.usage_error)+'</td></tr>';
    $('usagenote').textContent='Shown as an error, not as zero — a blank panel must not look like a real measurement.';
  }
  const cfg=S.config;
  $('cpu').value=cfg.cpu_percent_max;$('cpuv').textContent=cfg.cpu_percent_max+'%';
  $('ram').value=cfg.ram_mb_max;$('ramv').textContent=cfg.ram_mb_max+' MB';
  $('disk').value=cfg.disk_mb_max;$('diskv').textContent=cfg.disk_mb_max+' MB';
  $('contrib').checked=!!cfg.contribute;
  const w=S.would_apply;
  $('would').textContent='Will apply as '+w.cpus+' of '+S.host_cores+' cores ('+cfg.cpu_percent_max+'%) and '+w.mem_mb+' MB RAM.'
    +(c?('  Now: '+c.cpus+' cores / '+c.mem_mb+' MB.'):'');
}
$('cpu').oninput=e=>{$('cpuv').textContent=e.target.value+'%'};
$('ram').oninput=e=>{$('ramv').textContent=e.target.value+' MB'};
$('disk').oninput=e=>{$('diskv').textContent=e.target.value+' MB'};
$('refresh').onclick=load;
$('apply').onclick=async()=>{
  $('msg').textContent='applying…';
  const r=await fetch('/api/apply',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({cpu_percent_max:+$('cpu').value,ram_mb_max:+$('ram').value,
      disk_mb_max:+$('disk').value,contribute:$('contrib').checked})});
  const j=await r.json();
  $('msg').innerHTML=j.ok?('Applied. '+esc(j.change||'no change')):('<span class="bad">'+esc(j.error||'failed')+'</span>');
  load();
};
$('stop').onclick=async()=>{
  if(!confirm('Stop the Freebird client on this machine?'))return;
  $('msg').textContent='stopping…';
  await fetch('/api/stop',{method:'POST'});
  $('msg').textContent='stopped.';load();
};
load();setInterval(load,5000);
</script></body></html>"""


def _html(body, code=200):
    return code, "text/html; charset=utf-8", body.encode()


class Handler(__import__("http.server", fromlist=["BaseHTTPRequestHandler"]).BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, ctype, payload):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, obj, code=200):
        self._send(code, "application/json", json.dumps(obj).encode())

    def do_GET(self):
        if self.path.startswith("/api/state"):
            try:
                return self._json(state())
            except Exception as e:
                return self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)
        if self.path in ("/", "/index.html"):
            return self._send(*_html(PAGE))
        self._json({"error": "no such endpoint"}, 404)

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            d = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            d = {}
        if self.path == "/api/apply":
            try:
                cfg = fb.load_cfg()
                before = fb.current()
                cfg.update({k: v for k, v in d.items()
                            if k in ("cpu_percent_max", "ram_mb_max", "disk_mb_max", "contribute")})
                with open(fb.CONFIG, "w") as fh:
                    json.dump(cfg, fh, indent=2)
                fb.up()                      # recreates the container with the new limits
                after = fb.current()
                ch = []
                if before and after:
                    if abs(before["cpus"] - after["cpus"]) > 0.001:
                        ch.append("CPU %.2f -> %.2f cores" % (before["cpus"], after["cpus"]))
                    if before["mem_mb"] != after["mem_mb"]:
                        ch.append("RAM %d -> %d MB" % (before["mem_mb"], after["mem_mb"]))
                return self._json({"ok": True, "change": "; ".join(ch) or "restarted as-is",
                                   "applied": after})
            except SystemExit as e:
                return self._json({"ok": False, "error": str(e)}, 500)
            except Exception as e:
                return self._json({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)
        if self.path == "/api/stop":
            try:
                fb.stop()
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 500)
        self._json({"error": "no such endpoint"}, 404)


def main(open_browser=True):
    from http.server import ThreadingHTTPServer
    srv = None
    for port in range(PORT, PORT + 12):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if srv is None:
        print("could not find a free local port near %d" % PORT)
        return 1
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    print("Freebird control panel: %s" % url)
    print("(local only — it listens on 127.0.0.1 and is not reachable from the network)")
    if open_browser:
        threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\npanel closed (the client keeps running)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--no-open" not in __import__("sys").argv))
