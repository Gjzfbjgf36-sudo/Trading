# Der Morgen — eine Seite, fünf Minuten

Das hier ist der ganze Ablauf. Wenn du etwas nicht auf dieser Seite findest,
gehört es nicht in deinen Morgen.

## Einmal am Tag, nach Tagesschluss (nach 2 Uhr nachts UTC)

### 1. Kurse holen (Browser)

Drei Adressen öffnen, jede Antwort speichern:

```
https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1440
https://api.kraken.com/0/public/OHLC?pair=ETHUSD&interval=1440
https://api.kraken.com/0/public/OHLC?pair=SOLUSD&interval=1440
```

### 2. Umwandeln (Terminal)

```bash
cd ~/Trading
python -m arbcore.app.run_rules import-kraken --json ~/Downloads/btc.json --out records/btcusd_1d_kraken.csv
python -m arbcore.app.run_rules import-kraken --json ~/Downloads/eth.json --out records/ethusd_1d_kraken.csv
python -m arbcore.app.run_rules import-kraken --json ~/Downloads/sol.json --out records/solusd_1d_kraken.csv
```

### 3. Der eine Blick

```bash
python -m arbcore.app.run_gate board --csv records/*.csv
```

Das ist der Bildschirm, auf dem alles steht: Kapital, Drawdown, offene
Positionen mit Countdown, alle Märkte mit Abstand zum Auslöser — und unten,
was jetzt zu tun ist.

Unten steht immer genau **eine** Anweisung, nie zwei. Die Rangfolge ist:

1. **DATEN VERALTET** — erst neu laden. Alles andere wäre auf alten Kerzen
   gerechnet.
2. **HEUTE SCHLIESSEN** — eine Position hat ihren Zeit-Stop erreicht. Das geht
   vor jedem Kauf. Auch wenn gleichzeitig ein Signal da ist.
3. **SIGNAL** — ein Markt feuert und es ist Platz.
4. **SIGNAL, ABER KEIN PLATZ** — drei Positionen sind das Limit. Nichts tun.
5. **NICHTS ZU TUN** — der Normalfall.

An den meisten Tagen steht dort Nummer 5. Dann bist du fertig.

## Wenn SIGNAL dasteht

```bash
python -m arbcore.app.run_gate check --symbol BTCUSD --side buy \
    --entry <einstieg> --stop <stop> --target <ziel> --ref BTC-1
```

Sagt das Gate nein, ist es vorbei. Nicht verhandeln, nicht die Zahlen ändern,
bis es ja sagt — das Gate prüft dein Risiko, nicht deine Geduld.

Sagt es ja, **vor dem Kauf** den Plan festhalten:

```bash
python -m arbcore.app.run_gate commit --symbol BTCUSD --side buy \
    --entry <einstieg> --stop <stop> --target <ziel> --ref BTC-1 \
    --thesis "warum du glaubst, dass das läuft" \
    --invalidation "woran du merkst, dass du falsch lagst"
```

Erst danach kaufst du bei Kraken. Der Plan ist ab dann nicht mehr änderbar —
das ist der Sinn.

Notiere dir das Ausstiegsdatum, das `signal` dir nennt. Es ist der einzige Teil
des Plans, den du selbst im Kalender brauchst.

## Wenn HEUTE SCHLIESSEN dasteht

Bei Kraken verkaufen, dann eintragen:

```bash
python -m arbcore.app.run_gate close --ref BTC-1 \
    --exit-price <was du bekommen hast> --reason time_stop
```

Gründe: `time_stop`, `stop`, `target`, `rule_exit`, `manual`. Trag den echten
ein, auch `manual`. Eine Auswertung, in der jede Abweichung als Regelausstieg
verbucht ist, misst nichts.

## Einmal pro Woche

```bash
python -m arbcore.app.run_gate export   # Historie sichern, mitcommitten
python -m arbcore.app.run_gate review   # was deine Entscheidungen zeigen
```

`journal/` ist git-ignoriert und der Rechner kann kaputtgehen. Ohne `export`
ist deine Historie beim nächsten Rechnerwechsel weg — und die Historie ist das
Einzige, was dieses Projekt am Ende wert ist.

## Was du an keinem Morgen tust

* Eine Position aufmachen, weil ein Markt "gut aussieht". Der Scanner zeigt
  Abstände, keine Empfehlungen.
* Den Zeit-Stop verlängern, weil die Position gerade im Plus ist. Genau das ist
  die Variante, die in beiden Zeithälften verloren hat.
* `config/decide.yaml` anfassen. Die Werte stehen fest, damit die Papier-Phase
  etwas misst. Ein Parameter, den man während der Messung dreht, macht die
  Messung wertlos — nicht besser.
* Öfter als einmal am Tag nachschauen. Die Regel arbeitet auf Tageskerzen. Was
  zwischendurch passiert, ist für sie nicht vorhanden.

## Nach 30 abgeschlossenen Trades

Dann — und keinen Trade früher — sagt `run_gate review` etwas, das ein Urteil
ist statt einer Zahl. Bis dahin sammelst du Beobachtungen. Das ist die ganze
Aufgabe.
