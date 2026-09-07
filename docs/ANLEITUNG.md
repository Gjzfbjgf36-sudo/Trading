# Anleitung: das Entscheidungssystem benutzen

Für den Einstieg geschrieben. Keine Vorkenntnisse nötig.

**Was dieses System tut:** Es prüft ein Signal gegen deine Risikogrenzen,
sagt dir *ob* und *wie groß*, und hält fest, was du vorher behauptet hast.

**Was es nicht tut:** Es platziert keine Orders. Es hat keine Meinung darüber,
ob deine Regel gut ist. Es kann dir keinen Gewinn verschaffen.

---

## Phase 0 — mit 0 € anfangen

Du brauchst **kein Börsenkonto**. Nur:

- einen TradingView-Account (kostenloser Tarif genügt)
- dieses Repository

Dauer: mindestens 3 Monate oder 30 abgeschlossene Trades — je nachdem, was
länger dauert. Vorher sagen die Zahlen nichts.

---

## Schritt 1 — Die Regel in TradingView

1. TradingView öffnen → **Pine-Editor** unten.
2. Inhalt von `strategies/donchian_trend.pine` einfügen → **Zum Chart hinzufügen**.
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

## Schritt 2 — Signal prüfen lassen

Wenn die Regel ein Signal gibt, fragst du das Gate:

```bash
python -m arbcore.app.run_gate check \
    --symbol BTCUSD --side BUY \
    --entry 60000 --stop 57000 \
    --equity 1000 \
    --source donchian_55_20
```

Antwort entweder:

```
GREEN  BTCUSD  BUY
  size          0.00302645  (~181.59)
  stop          57000   <- sofort setzen
  risking       10.00
```

oder:

```
NO  BTCUSD  BUY
  blocked by fee_share_of_risk [NEGATIVE_NET_PROFIT] — most of what you would
  lose at the stop is fees, not the market.
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

```bash
python -m arbcore.app.run_gate commit \
    --symbol BTCUSD --side BUY --entry 60000 --stop 57000 \
    --equity 1000 --source donchian_55_20 \
    --thesis "Ausbruch über das 55-Tage-Hoch, Trend seit Oktober intakt" \
    --invalidation "Schlusskurs unter dem 20-Tage-Tief beendet die These"
```

Beides ist Pflicht, und beides ist **danach nicht mehr änderbar**. Das ist der
eigentliche Wert: In drei Monaten kannst du nachlesen, was du wirklich gedacht
hast — nicht, was du dich zu denken erinnerst.

`--thesis` muss ein Satz sein. „Sieht gut aus" wird abgelehnt.
`--invalidation` muss etwas **Beobachtbares** sein, kein Gefühl.

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
