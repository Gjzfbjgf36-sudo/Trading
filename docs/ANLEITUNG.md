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

### Einmalig einrichten

```bash
cp config/decide.example.yaml config/decide.yaml
```

Dann `config/decide.yaml` öffnen und drei Zahlen eintragen: dein gedachtes
Startkapital, den Gebührensatz deiner Börse **als Bruchteil** (0,26 % sind
`0.0026`, nicht `0.26`) und den Mindestauftrag.

Danach nie wieder eintippen — Kapital, Höchststand und Tagesverlust werden ab
jetzt aus dem Journal abgeleitet. Das ist Absicht: Ein Tippfehler bei der
Kontogröße würde die Positionsgröße verfälschen, ohne dass es jemandem auffällt.

Prüfen mit:

```bash
python -m arbcore.app.run_gate status
```

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

**5. Empfangene Signale ansehen:**

```bash
python -m arbcore.app.run_gate pending
```

> Der Empfänger lauscht standardmäßig nur lokal (`127.0.0.1`). Damit
> TradingView ihn erreicht, brauchst du eine öffentliche Adresse **mit TLS**
> (z. B. über einen Reverse Proxy). Ohne Verschlüsselung ginge der Token im
> Klartext über die Leitung. Und: eine öffentlich erreichbare Adresse sind
> wieder laufende Kosten, die dein Edge tragen muss.

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
