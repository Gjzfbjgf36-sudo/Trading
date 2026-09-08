# Erster Lauf gegen echte Kurse — BTC, ETH, SOL bei Kraken, Tageskerzen

Bis zu diesem Report war jede Zahl in diesem Repository entweder aus einem
synthetischen Markt oder aus einem Testfall. Das hier ist der erste Lauf einer
Regel gegen Kurse, die wirklich passiert sind.

## Datengrundlage

* Quelle: `https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1440` (ebenso `ETHUSD`, `SOLUSD`)
* Paare: `XXBTZUSD`, `XETHZUSD`, `SOLUSD`, Intervall 1440 Minuten (Tageskerzen)
* 720 abgeschlossene Kerzen, 2024-09-18 bis 2026-09-07
* Abgelegt als `records/btcusd_1d_kraken.csv`, `records/ethusd_1d_kraken.csv`,
  `records/solusd_1d_kraken.csv`

Kraken markiert im Feld `last`, bis wohin die Daten abgeschlossen sind. Die
Kerze danach läuft noch — ihr Hoch, Tief und Schluss ändern sich bis zum
Tagesende. Sie wird verworfen, sonst misst die Regel eine Kerze, die es so nie
gab. Von 721 gelieferten Zeilen bleiben 720.

Kraken liefert über diesen Endpunkt maximal 720 abgeschlossene Kerzen. Zwei Jahre Tagesdaten
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

## Drei Märkte statt einem

Dieselben zwei Jahre für ETH/USD und SOL/USD, ebenfalls über den Browser geholt
und mit `import-kraken` umgewandelt. Einzeln, Donchian 55/20:

| Markt | Trades | Treffer | Netto auf 1000 | Max. DD |
|---|---|---|---|---|
| BTC/USD | 6 | 33 % | −24,41 | 33,89 |
| ETH/USD | 4 | 50 % | **+33,93** | 11,12 |
| SOL/USD | 6 | **0 %** | −56,94 | 56,94 |

Sechs Verlierer aus sechs Trades bei SOL, und ein Plus bei ETH. Aus vier
beziehungsweise sechs Beobachtungen folgt daraus nichts über die Märkte — es
zeigt nur, wie weit das Ergebnis streut, wenn man dieselbe Regel auf drei
ähnliche Dinge anwendet.

Der Sweep bestätigt das von der anderen Seite. Bei ETH sind **92 % aller
Parametersätze profitabel** und der Prüfer meldet PLATEAU; bei SOL sind es
**0 %** und KEIN PLATEAU. Dieselbe Regel, derselbe Zeitraum, gegensätzliches
Urteil. Ein Plateau aus zwei bis vier Trades ist kein Plateau, sondern eine
Ansammlung von Zufällen, die zufällig in dieselbe Richtung zeigen. Die Warnung
unter dem ETH-Sweep sagt genau das.

Als Portfolio, Kapital gedrittelt:

| Regel | Trades | Netto auf 1000 | Kombinierter DD | Summe Einzel-DD | Streuungsvorteil |
|---|---|---|---|---|---|
| Donchian 55/20 | 16 | −15,81 | 16,35 | 33,99 | **51,9 %** |
| Engulfing | 21 | +5,96 | 17,19 | 31,76 | **45,9 %** |

**Der Streuungsvorteil ist das einzige belastbare Ergebnis dieses Reports.** Die
Rendite hängt an 16 beziehungsweise 21 Trades und ist damit Rauschen. Der
Drawdown-Vergleich hängt daran nicht: er misst, ob die drei Märkte *gleichzeitig*
verlieren, und das wird an jedem einzelnen Tag der zwei Jahre gemessen, nicht nur
an den Handelstagen. Die drei Märkte fallen nicht im Gleichschritt — der
kombinierte Rückgang ist rund halb so groß wie die Summe der einzelnen.

Das ist bemerkenswert, weil es der landläufigen Annahme widerspricht, Krypto sei
"sowieso alles Bitcoin". Über diesen Zeitraum stimmt das für die Zeitpunkte der
Verluste nicht. An 52 % der Tage war mehr als eine Position offen, die
Rückgänge fielen trotzdem nicht zusammen.

Und es kostet nichts: keine Parameteranpassung, keine zusätzliche Annahme, kein
weiterer Freiheitsgrad, an dem man sich selbst betrügen kann. Es ist die einzige
Verbesserung in diesem ganzen Report, die man geschenkt bekommt.

## Was daraus folgt

1. **Tageskerzen liefern zu wenige Trades — auch über drei Märkte.** 16 bis 21
   statt 30. Drei Märkte haben die Zahl fast vervierfacht und reichen trotzdem
   nicht. Der Weg zu 30 führt über noch mehr Märkte, mehrere Jahre Historie
   (bezahlte Daten) oder ein kürzeres Intervall — wobei Letzteres den
   Kostenanteil pro Trade erhöht, siehe `run_gate costs`.
2. **Streuung ist der einzige gemessene Vorteil.** Rund 50 % weniger Drawdown,
   ohne Parameteranpassung. Das gilt unabhängig davon, ob die Regel Geld
   verdient.
3. **Die Reihenfolge bleibt: erst messen, dann urteilen.** Der Betreiber hat
   jetzt eine reale, reproduzierbare Messung statt einer Vermutung.
4. **Nichts an diesem Ergebnis rechtfertigt Echtgeld.** Es rechtfertigt die
   Papier-Phase, und zwar über mehrere Märkte gleichzeitig.

## Reproduzieren

```bash
python -m arbcore.app.run_rules backtest --csv records/btcusd_1d_kraken.csv --rule donchian
python -m arbcore.app.run_rules backtest --csv records/btcusd_1d_kraken.csv --rule engulfing
python -m arbcore.app.run_rules robustness --csv records/btcusd_1d_kraken.csv
python -m arbcore.app.run_rules portfolio --csv records/btcusd_1d_kraken.csv \
    records/ethusd_1d_kraken.csv records/solusd_1d_kraken.csv
```

Ohne installiertes Paket, nur mit der Kraken-Antwort als Datei:

```bash
python3 standalone/backtest.py ohlc.json
```
