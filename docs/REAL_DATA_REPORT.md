# Erster Lauf gegen echte Kurse — BTC/USD, Kraken, Tageskerzen

Bis zu diesem Report war jede Zahl in diesem Repository entweder aus einem
synthetischen Markt oder aus einem Testfall. Das hier ist der erste Lauf einer
Regel gegen Kurse, die wirklich passiert sind.

## Datengrundlage

* Quelle: `https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1440`
* Paar: `XXBTZUSD`, Intervall 1440 Minuten (Tageskerzen)
* 721 Kerzen, 2024-09-18 bis 2026-09-08
* Abgelegt als `records/btcusd_1d_kraken.csv`

Kraken liefert über diesen Endpunkt maximal 720 Kerzen. Zwei Jahre Tagesdaten
sind alles, was ohne kostenpflichtige Historie zu bekommen ist — und zwei Jahre
sind für eine Regel, die etwa drei Trades pro Jahr macht, zu wenig. Das ist
keine Nebenbemerkung, sondern das Hauptergebnis.

## Kosten

0,26 % Gebühr (Kraken Taker) und 0,05 % Slippage, jeweils pro Seite. Innerhalb
einer Kerze wird angenommen, dass der Stop vor dem Ziel erreicht wird.

## Ergebnis

| Regel | Signale | Trades | Treffer | Netto auf 1000 | Profit-Faktor | Max. DD |
|---|---|---|---|---|---|---|
| Donchian 55/20 | 25 | 6 | 33 % | **−24,41** | 0,28 | 33,89 |
| Engulfing EMA200, CRV 2,0 | 15 | 10 | 40 % | **−7,26** | 0,90 | 48,14 |

Beide negativ. Beide unter 30 Trades, damit gilt Regel 5: **kein Urteil.** Die
Zahlen sagen nicht, dass die Regeln verlieren — sie sagen, dass zwei Jahre
Tagesdaten nicht ausreichen, um überhaupt etwas zu sagen.

Bemerkenswert an Donchian: 25 Signale, aber nur 6 Trades. 18 Signale fielen weg,
weil bereits eine Position offen war. Die Regel sieht mehr als sie handelt.

## Robustheit

Sweep über Donchian-Parameter (Einstieg/Ausstieg):

```
Profitabel: 40,0 % der geprüften Parametersätze
Bester: 40/10 (+36,00) — 3 von 5 direkten Nachbarn ebenfalls profitabel
KEIN PLATEAU
```

Die Struktur ist auffällig sauber: alles bei Einstiegsfenster 30 und 40 ist
positiv, alles ab 55 negativ. Das *sieht* aus wie ein Plateau. Der Prüfer sagt
trotzdem Nein, und er hat recht: die profitablen Sätze bestehen aus 5 bis 9
Trades. Bei neun Trades ist ein Vorzeichen kein Befund. Man kann aus diesem
Sweep nicht ablesen, dass 40/10 besser ist als 55/20 — man kann nur ablesen,
dass beide Zahlen aus zu wenig Beobachtungen stammen.

Genau dafür ist der Sweep da. Ohne ihn hätte hier gestanden: "Donchian 40/10
macht +3,6 % in zwei Jahren." Das wäre die Sorte Satz, die Geld kostet.

## Was daraus folgt

1. **Tageskerzen auf einem einzelnen Markt liefern zu wenige Trades.** Um 30
   Trades zu erreichen, braucht es entweder mehr Historie (mehrere Jahre, also
   bezahlte Daten), mehr Märkte parallel (`run_rules portfolio`), oder ein
   kürzeres Intervall — wobei Letzteres den Kostenanteil pro Trade erhöht, siehe
   `run_gate costs`.
2. **Die Reihenfolge bleibt: erst messen, dann urteilen.** Der Betreiber hat
   jetzt eine reale, reproduzierbare Nullmessung statt einer Vermutung.
3. **Nichts an diesem Ergebnis rechtfertigt Echtgeld.** Es rechtfertigt die
   Papier-Phase, und zwar über mehrere Märkte gleichzeitig.

## Reproduzieren

```bash
python -m arbcore.app.run_rules backtest --csv records/btcusd_1d_kraken.csv --rule donchian
python -m arbcore.app.run_rules backtest --csv records/btcusd_1d_kraken.csv --rule engulfing
python -m arbcore.app.run_rules robustness --csv records/btcusd_1d_kraken.csv
```

Ohne installiertes Paket, nur mit der Kraken-Antwort als Datei:

```bash
python3 standalone/backtest.py ohlc.json
```
