# Anleitung: das Entscheidungssystem benutzen

Für den Einstieg geschrieben. Keine Vorkenntnisse nötig.

**Was dieses System tut:** Es prüft ein Signal gegen deine Risikogrenzen,
sagt dir *ob* und *wie groß*, und hält fest, was du vorher behauptet hast.

**Was es nicht tut:** Es platziert keine Orders. Es hat keine Meinung darüber,
ob deine Regel gut ist. Es kann dir keinen Gewinn verschaffen.

---

## Was du am ersten Tag realistisch erreichst

Etwa 45 Minuten Arbeit, und danach ist alles bereit. Was du **nicht** erreichst:
einen Trade. Deine Regel gibt rund 2 Signale im Monat — das erste kann drei
Wochen entfernt sein. Das ist kein Fehler im Aufbau, das ist die Strategie.

| Zeit | Was |
|---|---|
| 10 Min | `config/decide.yaml` anlegen, Gebührensatz nachschlagen und eintragen |
| 5 Min | `run_gate setup` — sagt dir, ob etwas fehlt |
| 5 Min | `run_gate costcheck` — zeigt, was deine Gebühren tragen |
| 20 Min | TradingView: Script laden, Kommission und Slippage setzen, Backtest ansehen |
| 5 Min | Alerts anlegen (Einstieg und Ausstieg), Handy-Push aktivieren |

Danach: warten. Wochenlang, unter Umständen. Das auszuhalten ist der erste Test,
und mehr Leute scheitern daran als an der Regel.

## Phase 0 — mit 0 € anfangen

Du brauchst **kein Börsenkonto**. Nur:

- einen TradingView-Account (kostenloser Tarif genügt)
- dieses Repository

### Einmalig einrichten (macOS)

```bash
git clone https://github.com/Gjzfbjgf36-sudo/Trading.git
cd Trading
./setup-mac.sh
```

Danach in **jedem neuen Terminal** zuerst:

```bash
cd ~/Trading           # oder wohin du geklont hast
source .venv/bin/activate
```

> **Warum das nötig ist:** Auf dem Mac heisst der Befehl `python3`, nicht
> `python` — deshalb sagt das Terminal „command not found: python". Und
> `pip install` ohne venv scheitert, weil man ins System-Python nichts
> hineininstallieren soll. Ein venv ist ein eigener Ordner, in dem `python` und
> `pip` existieren; `source .venv/bin/activate` schaltet ihn ein.

Dann `config/decide.yaml` öffnen und deinen echten Gebührensatz eintragen.

Dann `config/decide.yaml` öffnen und drei Zahlen eintragen: dein gedachtes
Startkapital, den Gebührensatz deiner Börse **als Bruchteil** (0,26 % sind
`0.0026`, nicht `0.26`) und den Mindestauftrag.

Danach nie wieder eintippen — Kapital, Höchststand und Tagesverlust werden ab
jetzt aus dem Journal abgeleitet. Das ist Absicht: Ein Tippfehler bei der
Kontogröße würde die Positionsgröße verfälschen, ohne dass es jemandem auffällt.

Prüfen mit:

```bash
python -m arbcore.app.run_gate setup
```

Das sagt dir, was fehlt und was der nächste Schritt ist — und zwar immer nur
**einer**. Wenn du nicht weiterweißt, ist dieser Befehl die Antwort.

Dauer: mindestens 3 Monate oder 30 abgeschlossene Trades — je nachdem, was
länger dauert. Vorher sagen die Zahlen nichts.

---

## Schritt 1 — Die Regel in TradingView

Es liegen zwei Regeln bei. **Nimm eine.** Beide parallel zu testen ist der
Mehrfachtest-Fehler in klein: Bei zwei Kandidaten sieht einer besser aus, und
zwar auch dann, wenn beide nichts taugen.

| Datei | Was sie macht | Signale/Monat | Parameter |
|---|---|---|---|
| `strategies/donchian_trend.pine` | Ausbruch über das 55-Tage-Hoch | ~2 | 2 |
| `strategies/engulfing_trend.pine` | Bullish-Engulfing im Aufwärtstrend | ~2–4 | 3 |

Donchian hat weniger Stellschrauben und ist damit schwerer zu überanpassen.
Engulfing ist anschaulicher — und eine Regel, die du drei Monate durchhältst,
schlägt eine „bessere", die du nach zwei Wochen aufgibst. Nimm die, bei der du
bleibst.

1. TradingView öffnen → **Pine-Editor** unten.
2. Inhalt der gewählten Datei einfügen → **Zum Chart hinzufügen**.
3. Chart auf **BTC/USD, Tageskerzen** stellen.

Im **Strategie-Tester** siehst du jetzt, wie die Regel historisch gelaufen wäre.

### Bevor du dieser Zahl glaubst

Klick auf **Eigenschaften** und setze:

| Einstellung | Wert | Warum |
|---|---|---|
| Kommission | dein echter Gebührensatz, z. B. 0,26 % | TradingView rechnet sonst **ohne Gebühren** |
| Slippage | mindestens 2 Ticks | Du bekommst nie exakt den Kurs, den du siehst |
| Recalculate on every tick | **aus** | An bedeutet Ergebnisse, die live nicht reproduzierbar sind |

Fast jeder Backtest, der live stirbt, stirbt an diesen drei Zeilen.

### Und noch eine Regel für dich selbst

Jede Parametervariante, die du ausprobierst, zählt. Wenn du 55/20 testest, dann
50/15, dann 60/25, und die dritte sieht am besten aus — dann hast du
wahrscheinlich Rauschen gefunden, keine Strategie. **Schreib jede Variante auf,
die du testest.** Wenn es mehr als drei bis vier werden, glaub dem Ergebnis nicht.

---

---

## Ohne TradingView: die Regel läuft auch in Python

Falls du unabhängig sein willst — kein Tarif, keine Grenzen, kein Dritter:

```bash
pip install ccxt                                     # einmalig
python -m arbcore.app.run_rules fetch --exchange kraken --symbol BTC/USD --out data/btc.csv
python -m arbcore.app.run_rules backtest --csv data/btc.csv --rule donchian
python -m arbcore.app.run_rules signal   --csv data/btc.csv --rule donchian
```

Der Download passiert einmal; danach läuft alles offline aus der CSV.

### Wenn das Terminal nicht ins Netz kommt, der Browser aber schon

Das ist kein Sonderfall — Firmennetze, gefilterte DNS und gesperrte Ausgänge
erzeugen genau das. Dann holst du die Kurse mit dem Browser:

1. Diese Adresse öffnen (Paar und Intervall nach Bedarf ändern):
   `https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1440`
2. Die Antwort als Datei speichern, zum Beispiel `~/Downloads/btc.json`.
3. Umwandeln:

```bash
python -m arbcore.app.run_rules import-kraken --json ~/Downloads/btc.json --out data/btc.csv
```

Ab hier ist die CSV genauso gut wie eine heruntergeladene. `import-kraken`
verwirft die noch laufende Kerze — Kraken sagt im Feld `last`, bis wohin die
Daten abgeschlossen sind, und die Kerze danach ändert sich bis Periodenende
noch. Eine Regel, die darauf feuert, misst eine Kerze, die es so nie gab.

Andere Paare: `ETHUSD`, `SOLUSD`, `XRPUSD`. Intervall in Minuten: `60`
(stündlich), `240` (4h), `1440` (täglich). Kraken gibt pro Anfrage höchstens
720 abgeschlossene Kerzen heraus.

**Dieser Backtest rechnet ehrlicher als der Standard in TradingView**, weil
deine Gebühren und Slippage Pflichtargumente sind statt Voreinstellungen auf
null. Drei Entscheidungen sind bewusst gegen uns getroffen:

- **Kein Look-ahead.** Ein Signal sieht nur Kerzen bis einschliesslich der
  Signalkerze.
- **Stop vor Ziel innerhalb einer Kerze.** Deckt eine Kerze beides ab, kann man
  aus OHLC nicht sagen, was zuerst kam — also wird der Stop angenommen. Das Ziel
  anzunehmen würde genau die mehrdeutigen Fälle schönrechnen.
- **Gebühren auf beiden Seiten**, plus Slippage bei Ein- und Ausstieg.

### Darf ich dem Backtest glauben?

Die naheliegende Frage „welcher Parameter ist am besten?" ist die falsche. Wer
zwanzig Varianten testet und die beste nimmt, hat aus zwanzig Ziehungen das
Maximum gewählt — dessen Ergebnis ist systematisch zu gut, ganz ohne Absicht.

```bash
python -m arbcore.app.run_rules robustness --csv data/btc.csv
```

Rechnet 25 Parametersätze durch und sagt dir, ob der gute Bereich ein **Plateau**
oder eine **Spitze** ist:

- **Plateau** — 40/15, 55/20 und 70/25 funktionieren ähnlich → gutes Zeichen.
  Nimm einen Wert aus der Mitte, **nicht den besten**.
- **Spitze** — nur 55/20 funktioniert, Nachbarn verlieren → fast immer
  angepasstes Rauschen. Einen Markt, der zwischen 54 und 55 Tagen umschaltet,
  gibt es nicht.

### Streuung — die einzige belegte Verbesserung ohne Anpassen

```bash
python -m arbcore.app.run_rules portfolio --csv data/BTC.csv data/ETH.csv data/SOL.csv
```

Dieselbe Regel auf mehreren Märkten. Es wird **nichts optimiert** — die Regel
läuft nur öfter. Deshalb erzeugt es keinen Überanpassungs-Verdacht.

Die Ausgabe weist aus, ob die Streuung echt war:

```
Summe der Einzel-Drawdowns: 57.14
Streuungsvorteil:           35.18 %
```

Unter ~15 % heisst: Die Märkte verlieren gleichzeitig. Bei Krypto-Paaren ist
das der Normalfall — fast alle folgen Bitcoin. Fünf Altcoins sind kaum mehr
Streuung als Bitcoin allein.

### Live mitschauen

```bash
python -m arbcore.app.run_rules watch --exchange kraken --symbol BTC/USD
```

Läuft und zeigt fortlaufend, wie weit die Regel vom Auslösen entfernt ist:

```
Kurs 27897.85  |  Auslöser 33233.39  |  noch 19.13 %  |  Kerzenschluss in 6h 12m  |  kein Signal
```

Und wenn sie feuert, mit Ton:

```
   ####  KAUFEN  ####

   Einstieg   130
   Stop       124.72   <- sofort mitsetzen
   Regel      donchian_20_10
   Gesehen    Schluss 130, 20-Tage-Hoch 105.04, ATR 2.64
```

**Wichtig:** Das Signal kommt immer aus **abgeschlossenen** Kerzen. Die laufende
Kerze wird nur angezeigt. Wer auf ihr handelt, handelt eine andere Regel als die
getestete — und zwar eine, die häufiger und schlechter feuert.

Zum Anschauen ohne Netz: `watch --csv data/btc.csv` spielt die letzten Kerzen
durch.

Feuert die Regel, gibst du Einstieg und Stop ins Gate — der Ablauf bleibt
derselbe.

---

## Schritt 2 — Signal prüfen lassen

Am einfachsten geführt — das System fragt dich der Reihe nach alles ab:

```bash
python -m arbcore.app.run_gate wizard
```

Oder direkt, wenn du die Zahlen schon hast:

```bash
python -m arbcore.app.run_gate check \
    --entry 60000 --stop 57000 --source donchian_55_20
```

Antwort entweder:

```
GRÜN  BTCUSD  BUY
  Menge          0.00302645  (~181.59)
  Stop           57000   <- sofort setzen
  Risiko         10.00
```

oder:

```
NEIN  BTCUSD  BUY
  blockiert durch fee_share_of_risk [NEGATIVE_NET_PROFIT] — der grösste Teil
  deines Stop-Verlusts wären Gebühren, nicht der Markt.
```

**Jedes Nein nennt die konkrete Prüfung.** Es gibt kein „irgendwie ungünstig".

### Was das Gate prüft

| Prüfung | Blockiert wenn |
|---|---|
| Signalalter | Das Signal ist älter als 30 Minuten — der Markt ist weitergelaufen |
| Positionsgröße | Ergibt sich aus 1 % Risiko und deinem Stop-Abstand |
| Mindestgröße | Eine korrekt dimensionierte Position wäre zu klein für die Börse |
| Gebührenanteil | Mehr als ein Drittel deines Stop-Verlusts wären Gebühren |
| Tagesverlust | Du hast heute schon 3 % verloren — Feierabend |
| Drawdown | 15 % vom Hoch verloren — alles anhalten und prüfen |
| Chance/Risiko | Dein Ziel zahlt weniger, als du riskierst |

---

## Schritt 3 — Plan festhalten (vor dem Einstieg)

Der `wizard` macht das direkt im Anschluss. Manuell geht es so:

```bash
python -m arbcore.app.run_gate commit \
    --entry 60000 --stop 57000 --source donchian_55_20 \
    --thesis "Ausbruch über das 55-Tage-Hoch, Trend seit Oktober intakt" \
    --invalidation "Schlusskurs unter dem 20-Tage-Tief beendet die These"
```

Beides ist Pflicht, und beides ist **danach nicht mehr änderbar**. Das ist der
eigentliche Wert: In drei Monaten kannst du nachlesen, was du wirklich gedacht
hast — nicht, was du dich zu denken erinnerst.

`--thesis` muss ein Satz sein. „Sieht gut aus" wird abgelehnt.
`--invalidation` muss etwas **Beobachtbares** sein, kein Gefühl.

---

## Wann verkaufe ich? — steht immer schon fest

```bash
python -m arbcore.app.run_gate open
```

```
BTC-1  BTCUSD BUY  Menge 0.00302645
    Einstieg war    60000
    VERKAUFEN bei   57000   (Stop — Verlust begrenzen)
    ODER bei        66000   (Ziel)
    ODER wenn       Schlusskurs unter 57000 oder 10 Kerzen ohne Bewegung
```

Die Antwort auf „soll ich jetzt verkaufen?" steht dort, seit du eingestiegen
bist. Sie wird nicht dadurch besser, dass du sie neu überdenkst, während die
Position im Minus ist.

**Ausstiegsmeldungen laufen nie durch das Gate.** Das Gate verhindert, dass du
neues Risiko eingehst — ein Ausstieg *reduziert* Risiko. Eine Prüfung, die
einen Ausstieg blockieren könnte, wäre eine Prüfung, die dich in einer Position
festhält. Nichts in diesem System darf zwischen dir und der Tür stehen.

---

## Schritt 4 — Position schließen

```bash
python -m arbcore.app.run_gate close --ref BTCUSD-1735689600 \
    --exit 62000 --reason TARGET_HIT
```

Gründe: `STOP_HIT`, `TARGET_HIT`, `SIGNAL_EXIT`, `INVALIDATED`, `DISCRETIONARY`.

**`DISCRETIONARY` heißt: du bist aus einem Grund ausgestiegen, der nicht im Plan
stand.** Sei hier ehrlich. Diese Zeile ist später die aufschlussreichste im
ganzen System — sie misst, was dich das Übergehen deiner eigenen Regel kostet.

---

## Alerts mit Bedingung — „kauf, wenn die nächste Kerze grün wird"

Das ist die bessere Form einer Einschätzung, weil die Bedingung **vorher**
feststeht und hinterher niemand darüber streiten muss:

```bash
python -m arbcore.app.run_gate read --ref BTC-W1 \
    --observed "Kurs 59800, über EMA200, gestern rote Kerze" \
    --view "Nimmt die nächste Kerze das gestrige Hoch, ist das ein Engulfing im Trend" \
    --when "nächste Tageskerze schließt grün über 60000" \
    --entry 60000 --stop 57000 --target 66000
```

Wartende Bedingungen ansehen: `run_gate armed`.
Tritt sie ein: `run_gate trigger --ref BTC-W1`, dann `check`.

Tritt sie **nicht** ein, war die Einschätzung nicht falsch — sie kam nur nie zur
Anwendung. Das wird getrennt gezählt, statt als Treffer oder Fehlschlag.

## Meine Einschätzung mitlaufen lassen — und messen

Du kannst mich einen Chart ansehen lassen und fragen, ob du kaufen sollst. Ich
gebe dir eine Antwort. Aber schreib sie mit einer eigenen Quelle ins Journal:

```bash
python -m arbcore.app.run_gate commit ... --source claude_read
```

Dann steht sie neben der Regel, unter denselben Limits und derselben Disziplin.
`review` vergleicht sie nach genug Trades:

```
Nach Signalquelle — welche war es wert?
  claude_read              4 Trades, 1 Gewinner, Ø -3.18 pro Trade
  donchian_55_20           3 Trades, 1 Gewinner, Ø -0.94 pro Trade
```

**Das ist der ehrliche Umgang mit dem Thema.** Statt darüber zu streiten, ob
eine KI-Einschätzung etwas taugt, lässt du sie antreten und schaust nach.
Gewinnt sie über 30 Trades gegen die Regel, ist das ein Ergebnis, das ich
akzeptieren müsste. Verliert sie, hat es dich nichts gekostet.

Wichtig: **Nicht mischen.** Ein Trade hat eine Quelle. Wenn du dir mein Urteil
holst und dann doch der Regel folgst, weißt du hinterher nicht, was du gemessen
hast.

---

## Schritt 5 — Auswertung

```bash
python -m arbcore.app.run_gate review
```

Unter 30 abgeschlossenen Trades bekommst du **kein Urteil**, nur den Hinweis,
dass die Stichprobe zu klein ist. Das ist Absicht: Aus 12 Trades einen Edge
abzuleiten ist der teuerste Fehler in diesem ganzen Bereich.

Ab 30 Trades sagt dir die Auswertung eines von zwei Dingen:

- **Erwartungswert positiv** → Hinweis, kein Beweis. Regel unverändert lassen,
  weiter aufzeichnen. Die nächsten 30 Trades sind der eigentliche Test.
- **Erwartungswert negativ** → Die Regel kostet Geld. Ehrliche Optionen:
  aufhören, oder mit einer anderen Idee zurück in die Forschung. **Nicht** die
  Parameter drehen, bis die Vergangenheit besser aussieht.

---

---

## Welche Strategie passt zu deinen Gebühren?

Bevor du dich für einen Ansatz entscheidest:

```bash
python -m arbcore.app.run_gate costcheck
```

Bei 1000 € Konto und 0,26 % Gebühr:

```
Strategie                  Trades/Mon  Gebühr/Mon  % Konto  Urteil
Scalping                         2000     2080.00  208.00%  NEIN
Momentum                          120      124.80   12.48%  NEIN
News                               40       41.60    4.16%  NEIN
Breakout (Intraday)                40       41.60    4.16%  NEIN
Pullback                           20       20.80    2.08%  grenzwertig
Trendfolge (Tageskerzen)            2        2.08    0.21%  tragbar
```

Scalping würde bei diesem Konto **mehr als das doppelte Kapital pro Jahr** an
Gebühren kosten, bevor ein einziger Kursgewinn entsteht. Das ist kein Argument
gegen Scalping als Methode — es ist ein Argument gegen Scalping *bei diesen
Gebühren und dieser Kontogröße*.

Die Zeilen sind Größenordnungen zum Vergleich. Das Verhältnis zwischen ihnen
stimmt trotzdem, und es entscheidet mehr als jede Indikatorwahl.

### Chance-Risiko-Verhältnis

Das Gate verlangt standardmäßig **1,5** (einstellbar über `min_reward_to_risk`).
Grund: Bei einem CRV von 1,0 zahlt das Ziel genau das, was du riskierst — dafür
bräuchtest du über 50 % Trefferquote. Trendfolge liegt bei 35–45 %. Ein
abgelehntes Signal sagt dir die nötige Trefferquote direkt:

```
blockiert durch reward_to_risk [BELOW_SAFETY_MARGIN] — CRV 1.00 — bei diesem
Verhältnis brauchst du 50 % Trefferquote, nur um auf null zu kommen
```

---

## Optional: TradingView automatisch anbinden

Statt Signale von Hand einzutippen, kann TradingView sie schicken. Wichtig:
Das System **legt daraus nie selbst einen Plan an**. Es prüft und legt das
Ergebnis in eine Warteschlange — entscheiden und die These schreiben musst du.

**1. Token erzeugen** (mindestens 24 zufällige Zeichen):

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

**2. Empfänger starten:**

```bash
python -m arbcore.app.run_gate serve --token DEIN_TOKEN
```

**3. Token ins Pine-Script eintragen.** In den Einstellungen der Strategie das
Feld „Webhook token" auf denselben Wert setzen.

> TradingView kann keine eigenen HTTP-Header senden, deshalb reist der Token im
> Nachrichtentext mit. Damit steht er in deinem Alert: behandle das Script von
> da an als Geheimnis und tausche den Token aus, wenn du es je weitergibst.

**4. Alert anlegen.** Bedingung: die Strategie. In „Webhook URL" deine Adresse
eintragen, ins Nachrichtenfeld `{{strategy.order.alert_message}}`.

**5. Alerts anlegen — zwei Stück.** Eines für den Einstieg, eines für den
Ausstieg. Beide zeigen auf dieselbe URL, ins Nachrichtenfeld kommt jeweils
`{{strategy.order.alert_message}}`.

Aktiviere in TradingView zusätzlich die **Push-Benachrichtigung aufs Handy**.
Dann sieht es genau so aus, wie du es dir vorstellst:

```
     Handy vibriert
          ↓
GRÜN  BTCUSD  BUY
  Einstieg       60000
  Stop           57000   <- sofort setzen
  Menge          0.00302645
          ↓
     … Tage später, Handy vibriert wieder
          ↓
VERKAUFEN  BTCUSD
  Kurs jetzt     66000
  Grund          SIGNAL_EXIT
```

**6. Empfangene Signale ansehen:**

```bash
python -m arbcore.app.run_gate pending
```

> Der Empfänger lauscht standardmäßig nur lokal (`127.0.0.1`). Damit
> TradingView ihn erreicht, brauchst du eine öffentliche Adresse **mit TLS**
> (z. B. über einen Reverse Proxy). Ohne Verschlüsselung ginge der Token im
> Klartext über die Leitung. Und: eine öffentlich erreichbare Adresse sind
> wieder laufende Kosten, die dein Edge tragen muss.

---

---

## Wichtig: dein Journal sichern

`journal/` ist git-ignoriert und liegt nur auf dem Rechner, auf dem du
arbeitest. Wenn du über Claude Code im Browser arbeitest, ist der Container
**flüchtig** — nach der Session ist alles weg. Drei Monate Aufzeichnung wären
verloren.

Nach jedem Trade:

```bash
python -m arbcore.app.run_gate export
```

Das schreibt `records/journal-export.json`. **Diese Datei gehört ins
Repository** — sie ist nicht ignoriert und überlebt jeden Rechnerwechsel.

Wiederherstellen:

```bash
python -m arbcore.app.run_gate import
```

Vorhandene Einträge werden dabei **übersprungen, nie überschrieben**. Ein
Restore kann keinen Plan und kein Ergebnis nachträglich umschreiben — das ist
die eine Garantie, auf der das ganze Journal steht, und ein Wiederherstellen ist
davon keine Ausnahme.

---

## Wann du an echtes Geld denken darfst

Alle vier Punkte müssen erfüllt sein:

- [ ] Mindestens 30 abgeschlossene Paper-Trades
- [ ] Erwartungswert positiv über diese 30
- [ ] Weniger als 20 % Abweichungen vom Plan (Disziplin vorhanden)
- [ ] Du hast einen Zeitraum mit 5+ Verlusten in Folge durchgehalten, **ohne**
      die Regel zu ändern

Der letzte ist der schwerste und der wichtigste. Wer das nicht durchhält,
verliert mit echtem Geld genau dort.

Dann: mit einem Betrag anfangen, dessen Totalverlust dir egal wäre. Nicht
„wenig", sondern **egal**.

---

## Die häufigsten Fehler, die dieses System abfängt

1. **Position nach Kontogröße statt nach Risiko wählen.** Wird durch die
   Größenberechnung verhindert.
2. **Ohne Stop einsteigen.** Wird abgelehnt — ohne Stop gibt es kein Risiko,
   gegen das dimensioniert werden kann.
3. **Nach Verlusten größer werden.** Tagesverlust- und Drawdown-Grenzen greifen.
4. **Die Regel nach ein paar Verlusten ändern.** Das Journal zeigt dir, dass du
   es getan hast.
5. **Zu kleines Konto.** Wird mit Zahlen abgelehnt, statt dich Gebühren zahlen
   zu lassen, bis nichts mehr da ist.

## Was es nicht abfängt

Dass die Regel schlecht ist. Das kann nur die Zeit zeigen — deshalb Paper,
deshalb 30 Trades, deshalb 0 €.
