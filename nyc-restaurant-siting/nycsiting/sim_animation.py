"""
The simulation animation: a stylised top-down floor plan that fills and empties
with the model's monthly utilisation.

STRICTLY A RENDERING of the precomputed simulation dataframe. The HTML is
handed every month's numbers as JSON and steps through them; nothing here
computes a business result, so the picture can never disagree with the table
below it. Implemented as one self-contained HTML/JS block (no server, no
websocket, no external asset) so it runs anywhere Streamlit does — play,
pause, restart and speed all happen client-side without reruns.
"""
from __future__ import annotations

import json

import pandas as pd

#: Light editorial tokens, matching nycsiting/ui.py: white surfaces, hairline
#: borders, graphite ink, one restrained plum accent for the customer markers
#: and recovery bar. Monochrome linework otherwise.
COLORS = dict(surface="#f8f6f8", panel="#ffffff", line="#d9d9d9",
              ink="#1d161d", muted="#786c78", accent="#6a2f8d",
              good="#2f8d6e", bad="#a03d2e", table="#efecef",
              occupied="#6a2f8d")


def table_layout(seats: int, max_tables: int = 28) -> list[tuple[float, float]]:
    """
    Deterministic table positions for a given seat count.

    Four seats to a drawn table, clamped so tiny rooms still read as rooms and
    huge ones do not become confetti. A fixed grid, filled row by row from the
    entrance — no randomness, so the same inputs always draw the same room.
    """
    n = max(4, min(max_tables, round(seats / 4)))
    cols = 6
    out = []
    for i in range(n):
        row, col = divmod(i, cols)
        x = 12 + col * 13.2 + (3.3 if row % 2 else 0)
        y = 30 + row * 13.5
        out.append((x, y))
    return out


def occupied_count(n_tables: int, utilization: float) -> int:
    """
    How many drawn tables are occupied at a given utilisation.

    Direct proportionality, rounded — at 0.70 utilisation about 70% of the
    visible seating is occupied, as the spec requires. Clamped so rounding
    can never show a full room at partial utilisation or vice versa.
    """
    u = min(max(float(utilization), 0.0), 1.0)
    return min(n_tables, max(0, round(n_tables * u)))


def frame_payload(df: pd.DataFrame, n_tables: int) -> list[dict]:
    """The per-month numbers the animation needs, and nothing else."""
    out = []
    for row in df.itertuples():
        out.append(dict(
            month=int(row.month),
            occupied=occupied_count(n_tables, row.utilization),
            utilization=round(float(row.utilization), 3),
            customers=round(float(row.customers)),
            revenue=round(float(row.revenue)),
            total_cost=round(float(row.total_cost)),
            operating_profit=round(float(row.operating_profit)),
            cumulative_return=round(float(row.cumulative_return_after_investment)),
            recovery=(None if pd.isna(row.investment_recovery_pct)
                      else round(float(row.investment_recovery_pct), 4)),
        ))
    return out


def build_animation_html(df: pd.DataFrame, seats: int,
                         break_even_month: int | None,
                         height: int = 430) -> str:
    """One self-contained HTML document for st.components.v1.html."""
    tables = table_layout(seats)
    payload = frame_payload(df, len(tables))
    c = COLORS

    tables_svg = "".join(
        f'<g class="tbl" id="tbl{i}">'
        f'<rect x="{x-3.4}" y="{y-3.4}" width="6.8" height="6.8" rx="0.9" '
        f'fill="{c["table"]}" stroke="{c["line"]}" stroke-width="0.35"/>'
        f'<circle class="guest" cx="{x}" cy="{y-5}" r="1.15" fill="{c["occupied"]}" opacity="0"/>'
        f'<circle class="guest" cx="{x}" cy="{y+5}" r="1.15" fill="{c["occupied"]}" opacity="0"/>'
        f'<circle class="guest" cx="{x-5}" cy="{y}" r="1.15" fill="{c["occupied"]}" opacity="0"/>'
        f'<circle class="guest" cx="{x+5}" cy="{y}" r="1.15" fill="{c["occupied"]}" opacity="0"/>'
        f'</g>'
        for i, (x, y) in enumerate(tables))

    be_js = "null" if break_even_month is None else str(int(break_even_month))

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
  * {{ box-sizing: border-box; margin: 0; }}
  body {{ background: {c['surface']}; color: {c['ink']};
         font-family: Inter, "Helvetica Neue", Arial, system-ui, sans-serif; }}
  .wrap {{ display: flex; gap: 14px; padding: 4px; }}
  .floor {{ flex: 0 0 60%; background: {c['panel']}; border: 1px solid {c['line']};
           border-radius: 2px; padding: 6px; position: relative; }}
  .panel {{ flex: 1; display: flex; flex-direction: column; gap: 7px; }}
  .metric {{ background: {c['panel']}; border: 1px solid {c['line']};
            border-radius: 2px; padding: 7px 11px; }}
  .metric .k {{ font-size: 11px; color: {c['muted']}; text-transform: uppercase;
               letter-spacing: .07em; }}
  .metric .v {{ font-size: 19px; font-weight: 600; margin-top: 1px; }}
  .neg {{ color: {c['bad']}; }} .pos {{ color: {c['good']}; }}
  .bar {{ height: 7px; background: {c['line']}; border-radius: 2px;
         overflow: hidden; margin-top: 5px; }}
  .bar > div {{ height: 100%; background: {c['accent']}; width: 0%;
               transition: width .18s; }}
  .controls {{ display: flex; gap: 6px; margin-top: 4px; }}
  button {{ background: {c['panel']}; color: {c['ink']}; border: 1px solid {c['line']};
           border-radius: 2px; padding: 5px 12px; font-size: 12.5px; cursor: pointer; }}
  button:hover {{ border-color: {c['accent']}; }}
  button.active {{ border-color: {c['accent']}; color: {c['accent']}; }}
  .month-label {{ position: absolute; top: 10px; left: 14px; font-size: 13px;
                 color: {c['muted']}; letter-spacing: .05em; }}
  .be-badge {{ position: absolute; top: 10px; right: 14px; font-size: 12px;
              color: {c['good']}; border: 1px solid {c['good']};
              border-radius: 2px; padding: 3px 9px; opacity: 0;
              transition: opacity .5s; }}
</style></head><body>
<div class="wrap">
  <div class="floor">
    <div class="month-label" id="mLabel">MONTH 1</div>
    <div class="be-badge" id="beBadge">BREAK-EVEN · MONTH <span id="beM"></span></div>
    <svg viewBox="0 0 100 100" width="100%" height="{height - 66}">
      <rect x="2" y="20" width="96" height="78" rx="2" fill="none"
            stroke="{c['line']}" stroke-width="0.7"/>
      <rect x="2" y="2" width="60" height="14" rx="1.5" fill="{c['table']}"
            stroke="{c['line']}" stroke-width="0.5"/>
      <text x="32" y="10.5" font-size="4.2" fill="{c['muted']}"
            text-anchor="middle">KITCHEN</text>
      <rect x="66" y="2" width="32" height="14" rx="1.5" fill="{c['table']}"
            stroke="{c['line']}" stroke-width="0.5"/>
      <text x="82" y="10.5" font-size="4.2" fill="{c['muted']}"
            text-anchor="middle">BAR</text>
      <rect x="42" y="96.6" width="16" height="2.4" fill="{c['accent']}"/>
      <text x="50" y="94.4" font-size="3.6" fill="{c['muted']}"
            text-anchor="middle">ENTRANCE</text>
      {tables_svg}
    </svg>
    <div class="controls">
      <button id="playBtn">Play</button>
      <button id="restartBtn">Restart</button>
      <button class="speed active" data-s="1">1x</button>
      <button class="speed" data-s="2">2x</button>
      <button class="speed" data-s="4">4x</button>
    </div>
  </div>
  <div class="panel">
    <div class="metric"><div class="k">Customers (scenario est.)</div><div class="v" id="mCust">–</div></div>
    <div class="metric"><div class="k">Revenue (scenario est.)</div><div class="v" id="mRev">–</div></div>
    <div class="metric"><div class="k">Operating costs</div><div class="v" id="mCost">–</div></div>
    <div class="metric"><div class="k">Operating profit</div><div class="v" id="mProfit">–</div></div>
    <div class="metric"><div class="k">Cumulative return</div><div class="v" id="mCum">–</div></div>
    <div class="metric"><div class="k">Investment recovered</div>
      <div class="v" id="mRec">–</div><div class="bar"><div id="mRecBar"></div></div></div>
  </div>
</div>
<script>
const DATA = {json.dumps(payload)};
const BREAK_EVEN = {be_js};
let idx = 0, playing = false, speed = 1, timer = null;
const usd = v => (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString();

function render(i) {{
  const d = DATA[i];
  document.getElementById("mLabel").textContent = "MONTH " + d.month;
  document.getElementById("mCust").textContent = d.customers.toLocaleString();
  document.getElementById("mRev").textContent = usd(d.revenue);
  document.getElementById("mCost").textContent = usd(d.total_cost);
  const p = document.getElementById("mProfit");
  p.textContent = usd(d.operating_profit);
  p.className = "v " + (d.operating_profit < 0 ? "neg" : "pos");
  const cm = document.getElementById("mCum");
  cm.textContent = usd(d.cumulative_return);
  cm.className = "v " + (d.cumulative_return < 0 ? "neg" : "pos");
  const rec = d.recovery;
  document.getElementById("mRec").textContent =
      rec === null ? "n/a" : Math.round(rec * 100) + "%";
  document.getElementById("mRecBar").style.width =
      rec === null ? "0%" : (rec * 100) + "%";
  document.querySelectorAll(".tbl").forEach((t, k) => {{
    t.querySelectorAll(".guest").forEach(g =>
        g.setAttribute("opacity", k < d.occupied ? "0.95" : "0"));
  }});
  const badge = document.getElementById("beBadge");
  if (BREAK_EVEN !== null && d.month >= BREAK_EVEN) {{
    document.getElementById("beM").textContent = BREAK_EVEN;
    badge.style.opacity = "1";
  }} else {{ badge.style.opacity = "0"; }}
}}
function tick() {{
  if (!playing) return;
  if (idx < DATA.length - 1) {{ idx += 1; render(idx); }}
  else {{ setPlaying(false); }}
}}
function setPlaying(on) {{
  playing = on;
  document.getElementById("playBtn").textContent = on ? "Pause" : "Play";
  clearInterval(timer);
  if (on) timer = setInterval(tick, 420 / speed);
}}
document.getElementById("playBtn").onclick = () => setPlaying(!playing);
document.getElementById("restartBtn").onclick = () => {{
  idx = 0; render(0); setPlaying(true);
}};
document.querySelectorAll(".speed").forEach(b => b.onclick = () => {{
  speed = +b.dataset.s;
  document.querySelectorAll(".speed").forEach(x => x.classList.remove("active"));
  b.classList.add("active");
  if (playing) setPlaying(true);
}});
render(0);
</script></body></html>"""
