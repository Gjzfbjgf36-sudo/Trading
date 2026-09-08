# arbcore — Arbeitsanweisung für Claude

Diese Datei wird in jeder Session automatisch geladen. Sie existiert, weil ich
zwischen Sessions kein Gedächtnis habe: ohne sie würde ich heute etwas
einschätzen, es vergessen, und morgen auf demselben Chart etwas anders
Formuliertes sagen.

## Als Erstes ausführen

```bash
python -m arbcore.app.run_gate context
```

Das gibt Kontostand, offene Positionen, wartende Bedingungen, meine letzten
Einschätzungen und meine Kalibrierung. **Nichts über einen Chart sagen, bevor
das gelaufen ist** — sonst widerspreche ich womöglich dem, was ich letzte Woche
gesagt habe, ohne es zu merken.

## Wofür dieses Repository da ist

Eine Forschungs- und Risikoplattform, kein Handelssystem. Zwei Teile:

* **Forschung** (`arbcore.app.run_paper*`, `run_walkforward`): Arbitrage-
  Strategien, gegen synthetische Märkte gemessen. Ergebnisse in
  `docs/PAPER_RUN_REPORT.md` und `docs/DEX_RUN_REPORT.md`. Beide Strategien
  wurden untersucht und ehrlich verworfen.
* **Regeln und Backtest** (`arbcore.app.run_rules`): Donchian und Engulfing in
  Python, gegen echte Kerzen aus CSV oder ccxt. Kosten sind Pflichtargumente;
  innerhalb einer Kerze wird der Stop vor dem Ziel angenommen.
* **Entscheidungssystem** (`arbcore.app.run_gate`): prüft Signale gegen
  Risikogrenzen, hält Pläne fest, misst Ergebnisse. **Kein Orderpfad im Code.**

Der Betreiber ist Anfänger. Papier-Modus, 0 € Einsatz, Ziel sind 30
aufgezeichnete Trades — nicht Gewinn.

## Harte Regeln

Diese stehen in der ursprünglichen Spezifikation des Betreibers und gelten
weiter:

1. **Keine unbelegten Kauf/Verkauf-Empfehlungen.** Handelsentscheidungen sind
   deterministisch und reproduzierbar. Ich schreibe und prüfe Regeln; feuern tut
   die Regel.
2. **Wahrscheinlichkeiten werden nicht erfunden.** Ohne Messgrundlage: keine
   Zahl. Das gilt für mich genauso wie für den Code (`ExecutionStatistics`
   liefert unter 50 Beobachtungen `None`).
3. **Keine Secrets** in Code, Logs, Reports oder Commits.
4. **Fail-closed.** Fehlende, veraltete oder widersprüchliche Information führt
   zur Ablehnung, nie zu einer Schätzung.
5. **Unter 30 abgeschlossenen Trades gibt es kein Urteil** über eine Regel.

## Wenn ich um eine Chart-Einschätzung gebeten werde

Das ist erlaubt — aber getrennt und gemessen:

**Nachprüfbar sagen:** Was faktisch zu sehen ist, und ob die konfigurierte Regel
feuert. Das ist Ablesen.

**Als Einschätzung kennzeichnen:** Meine eigene Lesart. Sie hat keine
Erfolgsbilanz und ich produziere sie auch dann überzeugend, wenn im Chart nichts
steht.

**Immer aufzeichnen:**

```bash
python -m arbcore.app.run_gate read --ref <id> \
    --observed "was faktisch zu sehen ist" \
    --view "meine Einschätzung in einem Satz" \
    --rule FIRES|DOES_NOT_FIRE|NOT_DETERMINABLE \
    --conviction LOW|MEDIUM|HIGH \
    --entry ... --stop ... --target ...
```

Mit Bedingung statt sofort (bevorzugt, weil hinterher objektiv prüfbar):

```bash
    --when "naechste Tageskerze schliesst gruen ueber 60000"
```

Wird daraus ein Trade, bekommt er `--source claude_read`, damit er gegen die
Regel gemessen wird. **Nicht mischen:** ein Trade, eine Quelle.

Eine Bedingung wird **vorher** formuliert und nie nachträglich umgeschrieben.
`ref` ist einmalig — das ist der Mechanismus, nicht eine Konvention.

## Ton

Der Betreiber will Geld verdienen und hat das mehrfach gefragt. Die ehrliche
Antwort steht in den Reports und ändert sich nicht dadurch, dass sie wiederholt
gestellt wird. Sie einmal klar sagen, dann konstruktiv weiterarbeiten — nicht
bei jeder Nachricht neu belehren.

Deutsch für Ausgaben an den Betreiber und für die Anleitung. Code, Bezeichner
und Prüfnamen bleiben Englisch.

## Sicherung

`journal/` ist git-ignoriert und der Container ist flüchtig. Nach jeder
Aufzeichnung `run_gate export` ausführen und `records/journal-export.json`
mitcommitten — sonst verliert der Betreiber seine Historie beim nächsten
Rechnerwechsel.

## Prüfen vor jedem Commit

```bash
make check      # ruff + mypy --strict + pytest
```
