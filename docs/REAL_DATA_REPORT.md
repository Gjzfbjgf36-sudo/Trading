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

## Welcher Hebel wirkt wirklich?

Vier naheliegende Stellschrauben, alle an denselben Daten gemessen statt
behauptet. Portfolio aus drei Märkten, 1000 Startkapital.

**1. Niedrigere Gebühren — wirkt kaum, und beweist etwas Wichtigeres.**

| Gebühr je Seite | Donchian | Engulfing |
|---|---|---|
| 0,26 % (Taker) | −15,81 | +5,96 |
| 0,16 % (Maker) | −14,25 | +10,17 |
| 0,10 % (Großvolumen) | −13,31 | +12,71 |
| **0,00 % (unmöglich)** | **−11,75** | +16,95 |

Donchian verliert **auch bei null Gebühren**. Damit ist die häufigste Erklärung
für schlechte Ergebnisse ("die Kosten fressen die Rendite") hier widerlegt: es
gibt keine Rendite, die gefressen werden könnte. Gebühren zu senken ist richtig,
aber es macht aus einer Regel ohne Vorteil keine mit.

**2. Mehr Risiko je Trade — wirkt nicht, es multipliziert nur.**

| Risiko je Trade | Donchian | Engulfing |
|---|---|---|
| 0,5 % | −7,98 | +3,14 |
| 1,0 % | −15,81 | +5,96 |
| 2,0 % | −31,05 | +10,63 |
| 5,0 % | −73,66 | +16,89 |

Exakt linear in beide Richtungen. Positionsgröße ändert **nie** den Vorteil
einer Regel, nur die Größe des Ausschlags. Deshalb steht in der Spezifikation
des Betreibers "niemals das Risiko erhöhen, weil die Strategie Geld verliert" —
diese Tabelle ist der Beleg dafür.

**3. Mehr Märkte — wirkt, siehe oben.** Rund 50 % weniger Drawdown, geschenkt.

**4. Der Ausstieg — der einzige Hebel, der die Ergebnisse dreht.**

Donchian steigt aus, wenn der Kurs unter das Tief der letzten 20 Tage fällt. Das
dauert lange, und in dieser Zeit gibt die Position einen großen Teil des Gewinns
zurück. Ein zusätzlicher Zeit-Stop schließt die Position nach N Kerzen:

| Zeit-Stop | BTC | ETH | SOL | Summe | Trades |
|---|---|---|---|---|---|
| 5 | +3,60 | +14,75 | +0,53 | +18,88 | 36 |
| 8 | +10,60 | +14,35 | +6,10 | +31,04 | 32 |
| **10** | +9,82 | +20,22 | +8,83 | **+38,87** | 27 |
| 15 | +8,86 | +25,60 | +4,33 | +38,80 | 26 |
| 20 | −1,82 | +21,55 | −5,57 | +14,16 | 20 |
| 30 | −3,12 | +13,85 | −18,98 | −8,25 | 19 |
| kein | −8,14 | +11,31 | −18,98 | −15,81 | 16 |

Das ist kein einzelner Ausreißer, sondern ein **breiter Bereich von 5 bis 15,
der auf allen drei Märkten gleichzeitig positiv ist**, und ein sauberes Gefälle
zu längeren Haltedauern. Drei Märkte sind drei halbwegs unabhängige Bestätigungen.

### Der eigentliche Test: hält es außerhalb des Zeitraums?

Ein Ergebnis, das man durch Suchen gefunden hat, ist wertlos, bis es an Daten
funktioniert, an denen es nicht gefunden wurde. Zwei Jahre in zwei Hälften:

| Zeit-Stop | 1. Hälfte (Märkte +80 bis +99 %) | 2. Hälfte (Märkte −32 bis −57 %) |
|---|---|---|
| 5 | −4,70 | +22,54 |
| **10** | **+10,61** | **+26,31** |
| **15** | **+13,43** | **+28,73** |
| 20 | +18,89 | −8,06 |
| 30 | +5,59 | −11,11 |
| kein | −16,35 | −12,50 |

**10 und 15 sind in beiden Hälften positiv — in einem starken Aufwärtsmarkt und
in einem schweren Abwärtsmarkt.** Ohne Zeit-Stop verliert die Regel in beiden.
Das ist die stärkste Evidenz in diesem ganzen Projekt.

**Was trotzdem dagegen spricht**, und es muss dagegen sprechen, sonst wäre es
keine ehrliche Auswertung:

* 27 Trades im besten Fall, 8 in der zweiten Hälfte. Weiter unter 30.
* Ich habe elf Werte durchprobiert und den besten berichtet. Dass der
  Nachbarwert 15 fast identisch abschneidet, macht es glaubwürdiger — beweist
  es aber nicht.
* Nach Regel des Repositories wird **nicht der beste Wert genommen**, sondern
  einer aus der Mitte des Plateaus: **12**, nicht 10.

**Status: Hypothese, nicht Ergebnis.** Sie gehört in die Papier-Phase, mit
vorher festgelegtem Wert, und wird dort gemessen — nicht rückwirkend bestätigt.

### Was der festgelegte Wert tatsächlich liefert

`time_stop_bars: 12` steht jetzt in der Konfiguration. Damit ergibt derselbe
Portfolio-Befehl:

| | ohne Zeit-Stop | mit Zeit-Stop 12 |
|---|---|---|
| Trades | 16 | **27** |
| Netto | −15,81 | **+21,58** |
| Kombinierter Drawdown | 16,35 | **25,82** |
| Streuungsvorteil | 51,9 % | **9,5 %** |

Zwei Dinge, die gegen die einfache Erfolgsgeschichte sprechen:

**Der Zeit-Stop frisst die Streuung auf.** Von 51,9 % bleiben 9,5 %. Kurze
Haltedauern bedeuten, dass alle drei Märkte zu denselben Zeitpunkten im Markt
sind — Krypto-Ausbrüche passieren nun einmal gleichzeitig. Die Verluste fallen
dadurch wieder zusammen. Die beiden Verbesserungen aus diesem Report heben sich
teilweise gegenseitig auf. Unterm Strich: 37 mehr Gewinn gegen 9,5 mehr
Drawdown — ein guter Tausch, aber eben ein Tausch und kein Geschenk.

**12 liegt in einer Delle.** Zeit-Stop 10 ergibt +38,87, 15 ergibt +38,80 — und
das dazwischenliegende 12 nur +21,58. Ein echtes Plateau ist glatt; dieses ist
es nicht. Das ist kein Grund, doch 10 zu nehmen: es ist ein weiterer Beleg
dafür, dass diese Zahlen auf zu wenigen Trades stehen, um Nachkommastellen
ernst zu nehmen. Wer aus dieser Delle schließt, 12 sei "schlecht", hat den
Punkt verkehrt herum verstanden — die Delle sagt etwas über die Datenmenge,
nicht über den Parameter.

### Und die 48 verworfenen Signale

Donchian hat 16 Trades gemacht und **48 weitere Signale verworfen**, weil bereits
eine Position offen war. Dreiviertel dessen, was die Regel gesehen hat, wurde nie
gehandelt. Das ist der größte ungenutzte Posten überhaupt — und der Grund, warum
der Zeit-Stop so viel bewirkt: er macht Platz. Bei Zeit-Stop 10 steigt die
Trade-Zahl von 16 auf 27, ohne dass die Regel verändert wurde.

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
