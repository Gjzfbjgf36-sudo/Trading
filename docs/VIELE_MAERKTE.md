# Alle Coins scannen — was das bringt und was es kaputt macht

Der Scanner nimmt beliebig viele Märkte. `fetch-many` lädt sie, `board --dir`
zeigt sie. Technisch ist das gelöst. Dieser Text handelt davon, warum die
naheliegende Erwartung trotzdem falsch ist.

## Die Zahl, um die es geht

Gemessen an den drei vorhandenen Märkten über zwei Jahre: **Donchian 55/20
feuert an 3,02 % aller Markttage** (58 Signale auf 1.923 Marktage).

Hochgerechnet:

| Märkte im Scan | Signale pro Tag (Schnitt) |
|---|---|
| 3 | 0,1 |
| 50 | 1,5 |
| 200 | 6,0 |
| 500 | 15,1 |
| 1.000 | 30,2 |

Bei drei Märkten wartest du im Schnitt zehn Tage auf ein Signal. Bei tausend
Märkten hast du **dreißig am Tag**.

## Warum das ein Problem ist und keine Lösung

Dein Limit sind **drei offene Positionen**. Bei dreißig Signalen musst du also
27 ablehnen. Und genau da passiert der Schaden:

**Die Auswahl, die du dann triffst, wurde nie gemessen.**

Die gemessene Regel lautet: „Kaufe *jeden* Ausbruch über das 55-Tage-Hoch."
Was du bei dreißig Kandidaten tatsächlich machst, ist: „Kaufe die drei, die mir
am besten gefallen." Das ist eine andere Regel. Sie hat keine Trefferquote, weil
sie nie getestet wurde, und sie hat einen Namen: Auswahl nach dem Ergebnis.

Der Backtest über drei Märkte sagt über einen Scan über tausend Märkte
**nichts**. Nicht „ungefähr dasselbe" — nichts.

## Der zweite Effekt: mehr Signale sind nicht mehr Information

Bei tausend Märkten feuern an jedem Tag welche. Das fühlt sich nach einem
aktiven, funktionierenden System an. Tatsächlich sind die meisten davon
dasselbe Ereignis: Krypto-Paare laufen fast alle mit Bitcoin. Dreißig Signale
an einem Tag sind meistens **ein** Marktereignis in dreißig Verkleidungen.

Genau das hat die Streuungsmessung gezeigt: Der Vorteil fiel von 51,9 % auf
9,5 %, sobald die Haltedauern kürzer und die Einstiege gleichzeitiger wurden.
Mehr Märkte verstärken das, sie beheben es nicht.

## Was stattdessen funktioniert

**Eine feste, vorher festgelegte Liste.** Zehn bis zwanzig Märkte, ausgewählt
nach Kriterien, die nichts mit dem Kursverlauf zu tun haben — Liquidität,
Gebühren, dass es das Paar seit Jahren gibt. Die Liste wird **einmal**
festgelegt und dann nicht mehr angefasst.

Warum das der Unterschied ist: Wenn immer dieselben zwanzig Märkte gescannt
werden und du jedes Signal nimmst, bis das Limit voll ist, dann handelst du die
Regel, die gemessen wurde. Bei tausend wechselnden Kandidaten handelst du
deinen Geschmack.

Bei zwanzig Märkten sind es rund 0,6 Signale pro Tag — genug, um in drei
Monaten die 30 Trades zu erreichen, und selten genug, dass du fast nie
auswählen musst.

**Wenn doch mehr feuern als Platz ist:** vorher entscheiden, wie ausgewählt
wird, und die Regel aufschreiben. Alphabetisch. Oder der mit dem größten
Handelsvolumen. Irgendetwas, das nichts mit „sieht gut aus" zu tun hat und das
in drei Monaten noch genauso lautet.

## Aktien

Aktien gehen mit diesem Programm **noch nicht**, und zwar nicht aus Faulheit:

* **Andere Datenquelle.** `ccxt` kann nur Krypto. Aktien brauchen einen anderen
  Anbieter, und dieses Repository rät keine Schnittstellen — die müsste ich
  erst gegen die echte Dokumentation bauen.
* **Andere Kerzen.** Aktien haben Handelszeiten, Wochenenden und
  Eröffnungslücken. Ein Stop, der über Nacht übersprungen wird, ist bei Krypto
  die Ausnahme und bei Aktien Alltag. Der Backtest würde die Verluste
  systematisch zu klein rechnen.
* **Andere Kosten.** Bei 0,26 % Gebühr rechnet dieses Programm für Kraken.
  Neobroker nehmen 1 € pro Order — bei 200 € Positionsgröße sind das 0,5 %.
  Dieselbe Regel, andere Wirtschaftlichkeit.
* **Dividenden und Splits.** Unbereinigte Kurse erzeugen Signale, die es nie
  gab.

Keines dieser Probleme ist unlösbar. Aber ein Aktien-Scan, der die ersten drei
Punkte ignoriert, liefert Zahlen, die besser aussehen als die Wirklichkeit —
und das ist schlimmer als kein Aktien-Scan.

## Zusammengefasst

Der Scanner kann tausend Märkte. Du solltest ihm zwanzig geben. Der Grund ist
nicht Rechenzeit, sondern dass die Messung, auf der alles beruht, bei tausend
Kandidaten nicht mehr gilt.
