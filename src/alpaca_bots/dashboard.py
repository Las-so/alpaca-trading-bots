"""Live dashboard: equity curve, trade log, decision rationale — refreshing
every ~2s, matching the video's cadence. FastAPI + one static HTML page
that polls /api/state. No build step, no node — just `uvicorn` this module.
"""
from __future__ import annotations
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from . import state

app = FastAPI(title="Alpaca Bots Dashboard")

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Alpaca Bots</title>
<style>
body{font-family:system-ui,sans-serif;background:#0b0e14;color:#e6e6e6;margin:0;padding:24px}
h1{font-size:18px;font-weight:600;color:#9fd3ff}
.grid{display:grid;grid-template-columns:2fr 3fr;gap:20px;margin-top:16px}
.card{background:#141822;border:1px solid #232838;border-radius:8px;padding:16px}
#equity{font-size:28px;font-weight:700}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #232838}
.entered{color:#5fd97a}.skipped{color:#8a93a6}.risk_blocked{color:#e0b84c}.error{color:#ff6b6b}
canvas{width:100%;height:120px}
</style></head>
<body>
<h1>Alpaca Bots — live (paper)</h1>
<div class="grid">
  <div class="card"><div>Equity</div><div id="equity">—</div><canvas id="chart"></canvas></div>
  <div class="card"><div>Decisions</div>
    <table id="log"><thead><tr><th>time</th><th>bot</th><th>symbol</th><th>signal</th><th>jev</th><th>action</th><th>why</th></tr></thead>
    <tbody id="logbody"></tbody></table>
  </div>
</div>
<script>
async function tick() {
  const r = await fetch('/api/state'); const d = await r.json();
  const curve = d.equity_curve || [];
  if (curve.length) document.getElementById('equity').textContent =
    '$' + curve[curve.length-1].equity.toLocaleString(undefined,{maximumFractionDigits:2});
  const body = document.getElementById('logbody'); body.innerHTML = '';
  (d.decisions || []).slice().reverse().slice(0,50).forEach(e => {
    const tr = document.createElement('tr');
    const t = new Date(e.ts*1000).toLocaleTimeString();
    tr.innerHTML = `<td>${t}</td><td>${e.bot}</td><td>${e.symbol}</td><td>${e.signal}</td>`+
      `<td>${e.jev_verdict} ${e.jev_probability!=null?(e.jev_probability*100).toFixed(0)+'%':''}</td>`+
      `<td class="${e.action}">${e.action}</td><td>${e.rationale}</td>`;
    body.appendChild(tr);
  });
  const c = document.getElementById('chart').getContext('2d');
  c.clearRect(0,0,2000,120);
  if (curve.length>1) {
    const vals = curve.map(p=>p.equity); const min=Math.min(...vals), max=Math.max(...vals)||1;
    c.strokeStyle='#5fd97a'; c.beginPath();
    curve.forEach((p,i)=>{const x=i/(curve.length-1)*600; const y=110-((p.equity-min)/(max-min||1))*100;
      i===0?c.moveTo(x,y):c.lineTo(x,y);});
    c.stroke();
  }
}
tick(); setInterval(tick, 2000);
</script>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


@app.get("/api/state", response_class=JSONResponse)
def api_state():
    return state.load()
