"""Derselbe Bildschirm als HTML-Datei.

Eine Datei, keine Abhängigkeiten, kein Netz: Doppelklick im Finder, fertig.
Sie zeigt exakt dieselben Zahlen wie ``board.render()`` — es gibt keine
Kennzahl, die nur in der hübschen Variante vorkommt, und keine, die dort
freundlicher gerundet wird.

Bei vielen Märkten übernimmt der Browser das, was ein Terminal nicht kann:
suchen und filtern. Was er nicht übernimmt, ist sortieren nach "am besten
gelaufen" — diese Spalte gibt es nicht, weil die Auswahl daraus der übliche
Weg ist, einen gemessenen Vorteil wieder auszugeben.
"""

from __future__ import annotations

import html
from decimal import Decimal

from .board import Board

_PCT = Decimal("0.01")

#: Klasse je Dringlichkeitsstufe. Die Farbe folgt der Rangfolge aus
#: ``Board._verdict`` — sie erfindet keine eigene.
_TONE = {
    "DATEN VERALTET": "stale",
    "HEUTE SCHLIESSEN": "close",
    "SIGNAL, ABER KEIN PLATZ": "blocked",
    "SIGNAL": "signal",
    "NICHTS ZU TUN": "quiet",
}

_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #fbfbfa; --fg: #1a1a18; --muted: #6b6b64; --line: #e2e2dd;
  --card: #ffffff; --quiet: #6b6b64; --signal: #1c6b3a; --close: #a8410f;
  --stale: #7a3fa8; --blocked: #4a4a44;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #161614; --fg: #ededea; --muted: #96968c; --line: #2c2c28;
    --card: #1e1e1b; --quiet: #96968c; --signal: #6cc48d; --close: #f0a06a;
    --stale: #c39ae0; --blocked: #8a8a80;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.5 ui-sans-serif, -apple-system, system-ui, sans-serif; }
.wrap { max-width: 1000px; margin: 0 auto; padding: 24px 20px 64px; }
h1 { font-size: 15px; font-weight: 600; letter-spacing: .08em;
  text-transform: uppercase; color: var(--muted); margin: 0 0 4px; }
.when { color: var(--muted); font-size: 13px; margin-bottom: 24px; }
.verdict { border: 1px solid var(--line); border-left: 4px solid var(--quiet);
  background: var(--card); border-radius: 8px; padding: 18px 20px; margin-bottom: 24px; }
.verdict.signal { border-left-color: var(--signal); }
.verdict.close  { border-left-color: var(--close); }
.verdict.stale  { border-left-color: var(--stale); }
.verdict.blocked { border-left-color: var(--blocked); }
.verdict h2 { margin: 0 0 8px; font-size: 20px; letter-spacing: .02em; }
.verdict.signal h2 { color: var(--signal); }
.verdict.close h2  { color: var(--close); }
.verdict.stale h2  { color: var(--stale); }
.verdict p { margin: 4px 0; }
.verdict code { font-size: 13px; background: var(--bg); padding: 2px 6px;
  border-radius: 4px; border: 1px solid var(--line); display: inline-block; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 1px; background: var(--line); border: 1px solid var(--line);
  border-radius: 8px; overflow: hidden; margin-bottom: 24px; }
.stat { background: var(--card); padding: 14px 16px; }
.stat .k { font-size: 11px; letter-spacing: .07em; text-transform: uppercase;
  color: var(--muted); }
.stat .v { font-size: 20px; margin-top: 2px; font-variant-numeric: tabular-nums; }
h3 { font-size: 12px; letter-spacing: .08em; text-transform: uppercase;
  color: var(--muted); margin: 28px 0 10px; }
.pos { border: 1px solid var(--line); background: var(--card);
  border-radius: 8px; padding: 14px 16px; margin-bottom: 10px; }
.pos.due { border-left: 4px solid var(--close); }
.pos .ref { font-weight: 600; }
.pos .meta { color: var(--muted); font-size: 13px; margin-top: 4px;
  font-variant-numeric: tabular-nums; }
.pos .due-note { color: var(--close); font-weight: 600; margin-top: 6px; }
.tools { display: flex; gap: 10px; align-items: center; margin-bottom: 10px;
  flex-wrap: wrap; }
input[type=search] { flex: 1 1 200px; padding: 8px 10px; border-radius: 6px;
  border: 1px solid var(--line); background: var(--card); color: var(--fg);
  font: inherit; font-size: 14px; }
label.toggle { font-size: 13px; color: var(--muted); display: flex;
  gap: 6px; align-items: center; cursor: pointer; }
.tablewrap { overflow-x: auto; border: 1px solid var(--line);
  border-radius: 8px; background: var(--card); }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { text-align: right; padding: 9px 14px; white-space: nowrap;
  border-bottom: 1px solid var(--line); font-variant-numeric: tabular-nums; }
th:first-child, td:first-child { text-align: left; }
th { font-size: 11px; letter-spacing: .06em; text-transform: uppercase;
  color: var(--muted); font-weight: 500; }
tbody tr:last-child td { border-bottom: none; }
tr.fires td { color: var(--signal); font-weight: 600; }
.count { color: var(--muted); font-size: 13px; margin-top: 8px; }
footer { margin-top: 40px; padding-top: 16px; border-top: 1px solid var(--line);
  color: var(--muted); font-size: 13px; }
footer p { margin: 6px 0; }
.empty { color: var(--muted); padding: 4px 0; }
"""

_SCRIPT = """
(function () {
  var box = document.getElementById('q');
  var only = document.getElementById('only');
  var rows = Array.prototype.slice.call(
    document.querySelectorAll('#markets tbody tr'));
  var count = document.getElementById('count');
  function apply() {
    var term = (box.value || '').toLowerCase();
    var firing = only.checked;
    var shown = 0;
    rows.forEach(function (row) {
      var name = row.getAttribute('data-market');
      var fires = row.classList.contains('fires');
      var ok = (!term || name.indexOf(term) !== -1) && (!firing || fires);
      row.hidden = !ok;
      if (ok) { shown += 1; }
    });
    count.textContent = shown + ' von ' + rows.length + ' Märkten';
  }
  box.addEventListener('input', apply);
  only.addEventListener('change', apply);
  apply();
})();
"""


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _verdict_block(board: Board) -> str:
    lines = [line.strip() for line in board.verdict_lines() if line.strip()]
    headline = lines[0] if lines else "NICHTS ZU TUN"
    tone = _TONE.get(headline, "quiet")
    body = []
    for line in lines[1:]:
        if line.startswith("run_gate") or line.startswith("run_rules"):
            body.append(f"<p><code>{_esc(line)}</code></p>")
        else:
            body.append(f"<p>{_esc(line)}</p>")
    return (
        f'<section class="verdict {tone}">'
        f"<h2>{_esc(headline)}</h2>{''.join(body)}</section>"
    )


def _stats(board: Board) -> str:
    state = board.account
    drawdown = (state.drawdown * Decimal(100)).quantize(_PCT)
    change = state.equity - board.starting_capital
    cells = [
        ("Kapital", f"{state.equity}"),
        ("Seit Start", f"{change:+}"),
        ("Drawdown", f"{drawdown} %"),
        ("Heute", f"{state.pnl_today:+}"),
        ("Positionen", f"{len(board.open_lines)} / {board.max_open_positions}"),
    ]
    inner = "".join(
        f'<div class="stat"><div class="k">{_esc(k)}</div>'
        f'<div class="v">{_esc(v)}</div></div>'
        for k, v in cells
    )
    return f'<section class="stats">{inner}</section>'


def _positions(board: Board) -> str:
    if not board.open_lines:
        return '<p class="empty">Keine offenen Positionen.</p>'
    blocks = []
    for line in board.open_lines:
        if line.bars_left is None:
            note = '<div class="meta">Zeit-Stop: nicht konfiguriert</div>'
        elif line.overdue:
            note = (
                f'<div class="due-note">ZEIT-STOP ERREICHT — heute schliessen, '
                f"egal wie der Kurs steht ({_esc(line.bars_held)} Kerzen)</div>"
            )
        else:
            note = (
                f'<div class="meta">Zeit-Stop in {_esc(line.bars_left)} Kerzen '
                f"({_esc(line.bars_held)} gehalten)</div>"
            )
        blocks.append(
            f'<div class="pos{" due" if line.overdue else ""}">'
            f'<div class="ref">{_esc(line.ref)} · {_esc(line.symbol)}</div>'
            f'<div class="meta">Einstieg {_esc(line.entry)} · '
            f"Stop {_esc(line.stop)} · Ziel {_esc(line.target)}</div>"
            f"{note}</div>"
        )
    return "".join(blocks)


def _markets(board: Board) -> str:
    if not board.scan_rows:
        return '<p class="empty">Keine Märkte übergeben.</p>'
    body = []
    for row in board.scan_rows:
        s = row.status
        if s.warmup_missing > 0:
            cells = "<td>—</td><td>—</td><td>—</td><td>zu wenig Historie</td>"
            body.append(
                f'<tr data-market="{_esc(row.market.lower())}">'
                f"<td>{_esc(row.market)}</td>{cells}</tr>"
            )
            continue
        level = s.trigger_level if s.trigger_level is not None else "—"
        gap = "—"
        if s.distance is not None:
            gap = f"{(s.distance * Decimal(100)).quantize(_PCT)} %"
        state = "KAUFEN" if s.fires else "wartet"
        klass = ' class="fires"' if s.fires else ""
        body.append(
            f'<tr{klass} data-market="{_esc(row.market.lower())}">'
            f"<td>{_esc(row.market)}</td><td>{_esc(s.price)}</td>"
            f"<td>{_esc(level)}</td><td>{_esc(gap)}</td><td>{_esc(state)}</td></tr>"
        )
    return (
        '<div class="tools">'
        '<input type="search" id="q" placeholder="Markt suchen…" '
        'autocomplete="off" spellcheck="false">'
        '<label class="toggle"><input type="checkbox" id="only"> '
        "nur feuernde</label></div>"
        '<div class="tablewrap"><table id="markets"><thead><tr>'
        "<th>Markt</th><th>Kurs</th><th>Auslöser</th><th>Abstand</th><th>Stand</th>"
        f"</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
        '<div class="count" id="count"></div>'
    )


def render_html(board: Board) -> str:
    """Die ganze Seite als eine Zeichenkette. Kein Netz, keine Bibliothek."""
    mode = "ECHTGELD" if not board.account.paper else "PAPIER"
    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Board · {_esc(mode)} · {board.now:%Y-%m-%d}</title>
<style>{_STYLE}</style>
</head>
<body>
<div class="wrap">
<h1>{_esc(mode)} · {_esc(board.rule_name)}</h1>
<div class="when">Stand {board.now:%Y-%m-%d %H:%M} · erzeugt aus abgeschlossenen
Kerzen, nicht aus Live-Kursen</div>
{_verdict_block(board)}
{_stats(board)}
<h3>Offene Positionen</h3>
{_positions(board)}
<h3>Märkte</h3>
{_markets(board)}
<footer>
<p>Diese Seite aktualisiert sich nicht von selbst. Sie zeigt den Stand vom
Zeitpunkt oben — neu erzeugen mit demselben Befehl.</p>
<p>Es gibt hier keine Spalte "beste Performance" und keine Sortierung nach
Trendstärke. Sich aus vielen Märkten den schönsten auszusuchen ist ein
Auswahlschritt, der nie gemessen wurde.</p>
</footer>
</div>
<script>{_SCRIPT}</script>
</body>
</html>
"""
