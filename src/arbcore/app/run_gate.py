"""CLI für das Entscheidungssystem. Es platziert keine Orders.

    python -m arbcore.app.run_gate status
    python -m arbcore.app.run_gate check   --entry 60000 --stop 57000 --source donchian_55_20
    python -m arbcore.app.run_gate wizard
    python -m arbcore.app.run_gate close   --ref BTCUSD-123 --exit 62000 --reason TARGET_HIT
    python -m arbcore.app.run_gate review
    python -m arbcore.app.run_gate serve   --token <geheim>

Kapital, Höchststand und Tagesverlust werden aus dem Journal abgeleitet, nicht
eingetippt: ein Tippfehler an dieser Stelle würde die Positionsgröße
verfälschen, ohne dass es auffällt.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..decide.account import AccountLedger
from ..decide.costcheck import report as cost_report
from ..decide.gate import GateConfig, Signal, SignalGate
from ..decide.journal import ExitReason, Journal, PlanIncomplete
from ..decide.reads import ChartRead, Conviction, ReadLog, RuleVerdict, calibration
from ..decide.settings import DEFAULT_PATH, DecideSettings, SettingsError, load_settings
from ..decide.setup_check import report as setup_report
from ..decide.webhook import SignalQueue, make_server
from ..domain.types import Side
from ..review.performance import (
    deviation_comparison,
    review_journal,
    source_comparison,
)


def _gate(settings: DecideSettings) -> SignalGate:
    return SignalGate(
        GateConfig(
            risk=settings.risk,
            fee_rate=settings.fee_rate,
            min_notional=settings.min_notional,
            max_open_positions=settings.max_open_positions,
            min_reward_to_risk=settings.min_reward_to_risk,
        )
    )


def _signal(args: argparse.Namespace, settings: DecideSettings, now: datetime) -> Signal:
    symbol = args.symbol or settings.symbol
    return Signal(
        ref=args.ref or f"{symbol}-{int(now.timestamp())}",
        symbol=symbol,
        side=Side(args.side),
        entry=Decimal(args.entry),
        stop=Decimal(args.stop),
        target=Decimal(args.target) if args.target else None,
        source=args.source,
        emitted_at=now,
    )


def _add_signal_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--entry", required=True)
    parser.add_argument("--stop", required=True)
    parser.add_argument("--target", default=None)
    parser.add_argument("--source", required=True, help="welche Regel hat das Signal erzeugt")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--side", choices=["BUY", "SELL"], default="BUY")
    parser.add_argument("--ref", default=None)


def _ask(prompt: str, *, allow_empty: bool = False) -> str:
    while True:
        answer = input(f"{prompt}\n> ").strip()
        if answer or allow_empty:
            return answer
        print("Bitte etwas eingeben.")


def _ask_decimal(prompt: str) -> Decimal:
    while True:
        try:
            return Decimal(_ask(prompt))
        except InvalidOperation:
            print("Das war keine Zahl. Beispiel: 60000 oder 59750.25")


def wizard(settings: DecideSettings, journal: Journal, now: datetime) -> int:
    """Geführter Ablauf für ein einzelnes Signal."""
    ledger = AccountLedger(journal, settings)
    print(ledger.summary(today=now.date()))
    print()
    print("Ein Signal prüfen. Abbrechen jederzeit mit Strg+C.\n")

    symbol = _ask(f"Symbol [{settings.symbol}]:", allow_empty=True) or settings.symbol
    side = "SELL" if _ask("Long oder Short? [long/short]:").lower().startswith("s") else "BUY"
    entry = _ask_decimal("Einstiegskurs:")
    stop = _ask_decimal(
        "Stop-Kurs — hier gibst du zu, dass du falsch lagst.\n"
        "Er kommt aus deiner Regel, nicht aus dem Bauch:"
    )
    target_raw = _ask("Zielkurs (leer lassen, wenn die Regel keins vorgibt):", allow_empty=True)
    source = _ask("Welche Regel hat das ausgelöst? (z. B. donchian_55_20):")

    signal = Signal(
        ref=f"{symbol}-{int(now.timestamp())}",
        symbol=symbol,
        side=Side(side),
        entry=entry,
        stop=stop,
        target=Decimal(target_raw) if target_raw else None,
        source=source,
        emitted_at=now,
    )
    verdict = _gate(settings).evaluate(signal, ledger.state(today=now.date()), now=now)
    print("\n" + verdict.explain() + "\n")
    if not verdict.green:
        return 1

    print("Jetzt der Teil, der später zählt.\n")
    thesis = _ask(
        "Warum erwartest du, dass das funktioniert? Ein Satz.\n"
        "In drei Monaten liest du das wieder — 'sieht gut aus' hilft dir dann nicht:"
    )
    invalidation = _ask(
        "Was wäre der Beweis, dass du falsch liegst? Etwas Beobachtbares,\n"
        "kein Gefühl (z. B. 'Schlusskurs unter dem 20-Tage-Tief'):"
    )
    try:
        journal.commit_plan(verdict.to_commitment(thesis, invalidation), now=now)
    except PlanIncomplete as exc:
        print(f"\nNicht gespeichert: {exc}")
        return 1
    print(f"\nPlan gespeichert als {signal.ref}. Ab jetzt nicht mehr änderbar.")
    print(f"Stop bei {signal.stop} sofort setzen.")
    return 0


def serve(settings: DecideSettings, journal: Journal, args: argparse.Namespace) -> int:
    queue = SignalQueue(args.queue)
    ledger = AccountLedger(journal, settings)
    server = make_server(
        gate=_gate(settings),
        queue=queue,
        account_provider=lambda: ledger.state(today=datetime.now(UTC).date()),
        token=args.token,
        host=args.host,
        port=args.port,
    )
    print(f"Webhook lauscht auf http://{args.host}:{args.port}/tradingview")
    print("TradingView muss den Header X-Arbcore-Token mitschicken.")
    print("Signale werden geprüft und in die Warteschlange gelegt — nie automatisch")
    print("als Plan gespeichert. Beenden mit Strg+C.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")
    finally:
        server.server_close()
        queue.close()
    return 0


def _read_log_path(settings: DecideSettings) -> str:
    """The read log lives beside the journal; they are one record in two files."""
    return str(Path(settings.journal_path).with_name("chart_reads.sqlite"))


def _context(
    settings: DecideSettings, journal: Journal, ledger: AccountLedger, now: datetime
) -> str:
    """Everything a fresh session needs before it says anything about a chart.

    Written for the case where the assistant has no memory of what it said last
    time. Without this it would give an assessment today, forget it, and give a
    differently-worded one tomorrow on the same chart.
    """
    parts = [
        "=== STAND ===",
        ledger.summary(today=now.date()),
        "",
        "=== OFFENE POSITIONEN ===",
    ]
    open_rows = journal.open_positions()
    if not open_rows:
        parts.append("keine")
    for row in open_rows:
        parts.append(
            f"{row['ref']}  {row['symbol']} {row['side']}  Einstieg {row['entry']}  "
            f"Stop {row['stop']}  Ziel {row['target'] or '-'}"
        )
        parts.append(f"    These: {row['thesis']}")

    log = ReadLog(_read_log_path(settings))
    try:
        parts += ["", "=== WARTENDE BEDINGUNGEN ==="]
        armed_rows = log.armed()
        if not armed_rows:
            parts.append("keine")
        for row in armed_rows:
            parts.append(f"{row['ref']}: WENN {row['trigger_condition']}")

        parts += ["", "=== LETZTE EINSCHÄTZUNGEN ==="]
        recent = log.recent(limit=5)
        if not recent:
            parts.append("keine")
        for row in recent:
            marker = "*" if row["acted"] == "1" else " "
            parts.append(f"{marker} {row['at'][:10]} {row['ref']} ({row['conviction']})")
            parts.append(f"    gesehen:  {row['observed']}")
            parts.append(f"    gesagt:   {row['claude_view']}")

        parts += ["", "=== KALIBRIERUNG ===", calibration(log, settings.journal_path)]
    finally:
        log.close()

    parts += [
        "",
        "=== STEHENDE REGELN ===",
        "Papier, kein Echtgeld. Kein Orderpfad im Code.",
        "Einschätzungen bekommen die Quelle claude_read und werden gegen die",
        "Regel gemessen. Eine Bedingung wird vorher formuliert, nie nachträglich.",
        "Unter 30 abgeschlossenen Trades gibt es kein Urteil.",
    ]
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Entscheidungssystem — es platziert keine Orders"
    )
    parser.add_argument("--config", default=DEFAULT_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Kontostand, Drawdown, offene Positionen")
    sub.add_parser("setup", help="Ist alles eingerichtet? Was ist der nächste Schritt?")
    check = sub.add_parser("check", help="Darf dieser Trade laufen, und wie groß?")
    _add_signal_args(check)
    commit = sub.add_parser("commit", help="Plan für ein grünes Signal festhalten")
    _add_signal_args(commit)
    commit.add_argument("--thesis", required=True)
    commit.add_argument("--invalidation", required=True)
    sub.add_parser("wizard", help="Geführt: prüfen und Plan festhalten")

    close = sub.add_parser("close", help="Ergebnis festhalten")
    close.add_argument("--ref", required=True)
    close.add_argument("--exit", dest="exit_price", required=True)
    close.add_argument(
        "--reason", choices=[str(r) for r in ExitReason], default=str(ExitReason.STOP_HIT)
    )

    sub.add_parser("open", help="offene Positionen")

    read = sub.add_parser(
        "read", help="Chart-Einschätzung festhalten (auch als Bedingung)"
    )
    read.add_argument("--ref", required=True)
    read.add_argument("--symbol", default=None)
    read.add_argument("--timeframe", default="1D")
    read.add_argument("--observed", required=True, help="was faktisch zu sehen ist")
    read.add_argument(
        "--rule", choices=[str(v) for v in RuleVerdict], default=str(RuleVerdict.NOT_DETERMINABLE)
    )
    read.add_argument("--view", required=True, help="die Einschätzung in einem Satz")
    read.add_argument(
        "--conviction", choices=[str(c) for c in Conviction], default=str(Conviction.MEDIUM)
    )
    read.add_argument("--entry", default=None)
    read.add_argument("--stop", default=None)
    read.add_argument("--target", default=None)
    read.add_argument(
        "--when",
        default=None,
        help=(
            "Bedingung, die zuerst eintreten muss, z. B. "
            "'naechste Tageskerze schliesst ueber 60000'"
        ),
    )

    trigger = sub.add_parser("trigger", help="Bedingung ist eingetreten")
    trigger.add_argument("--ref", required=True)

    sub.add_parser("armed", help="Einschätzungen, die auf ihre Bedingung warten")
    sub.add_parser("calibration", help="waren die sicheren Einschätzungen besser?")
    sub.add_parser("context", help="alles, was eine neue Session wissen muss")
    costs = sub.add_parser(
        "costcheck", help="welche Strategieklassen deine Gebühren überhaupt tragen"
    )
    costs.add_argument("--equity", default=None, help="Standard: dein Startkapital")
    costs.add_argument("--fee-rate", dest="fee_rate", default=None)
    sub.add_parser("review", help="was deine eigenen Entscheidungen zeigen")
    pending = sub.add_parser("pending", help="empfangene Webhook-Signale")
    pending.add_argument("--queue", default="journal/signals.sqlite")
    pending.add_argument("--green-only", action="store_true")

    server = sub.add_parser("serve", help="TradingView-Webhook empfangen")
    server.add_argument("--token", required=True, help="mindestens 24 zufällige Zeichen")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8787)
    server.add_argument("--queue", default="journal/signals.sqlite")

    args = parser.parse_args()
    now = datetime.now(UTC)

    if args.command == "setup":
        print(setup_report(args.config))
        return 0

    try:
        settings = load_settings(args.config)
    except SettingsError as exc:
        print(f"Konfiguration: {exc}")
        print("\nTipp: `python -m arbcore.app.run_gate setup` sagt dir, was fehlt.")
        return 2

    if args.command == "costcheck":
        print(
            cost_report(
                equity=Decimal(args.equity) if args.equity else settings.starting_capital,
                fee_rate=Decimal(args.fee_rate) if args.fee_rate else settings.fee_rate,
            )
        )
        return 0

    if args.command == "pending":
        queue = SignalQueue(args.queue)
        rows = queue.pending(green_only=args.green_only)
        if not rows:
            print("Keine offenen Signale.")
        for row in rows:
            print(f"[{row['verdict']}] {row['ref']}  {row['symbol']} {row['side']}  "
                  f"entry {row['entry']}  stop {row['stop']}  ({row['source']})")
        queue.close()
        return 0

    journal = Journal(settings.journal_path)
    ledger = AccountLedger(journal, settings)
    try:
        if args.command == "status":
            print(ledger.summary(today=now.date()))
            return 0
        if args.command == "wizard":
            return wizard(settings, journal, now)
        if args.command == "serve":
            return serve(settings, journal, args)

        if args.command in ("check", "commit"):
            signal = _signal(args, settings, now)
            verdict = _gate(settings).evaluate(
                signal, ledger.state(today=now.date()), now=now
            )
            print(verdict.explain())
            if args.command == "check":
                return 0 if verdict.green else 1
            if not verdict.green:
                print("\nNichts gespeichert: das Gate sagt nein.")
                return 1
            journal.commit_plan(
                verdict.to_commitment(args.thesis, args.invalidation), now=now
            )
            print(f"\nPlan gespeichert als {signal.ref}. Ab jetzt nicht mehr änderbar.")
            return 0

        if args.command == "close":
            outcome = journal.record_outcome(
                args.ref,
                exit_price=Decimal(args.exit_price),
                exit_reason=ExitReason(args.reason),
                now=now,
                fee_rate=settings.fee_rate,
            )
            print(f"Geschlossen {args.ref}: P/L {outcome.pnl} ({outcome.exit_reason})")
            if not outcome.followed_plan:
                print("Als Abweichung vom Plan vermerkt.")
            print()
            print(ledger.summary(today=now.date()))
            return 0

        if args.command == "open":
            rows = journal.open_positions()
            if not rows:
                print("Keine offenen Positionen.")
            for row in rows:
                target = row["target"] or "keins (Ausstieg per Regel)"
                print(f"{row['ref']}  {row['symbol']} {row['side']}  Menge {row['quantity']}")
                print(f"    Einstieg war    {row['entry']}")
                print(f"    VERKAUFEN bei   {row['stop']}   (Stop — Verlust begrenzen)")
                print(f"    ODER bei        {target}   (Ziel)")
                print(f"    ODER wenn       {row['invalidation']}")
                print(f"    These           {row['thesis']}")
                print(f"    Regel           {row['signal_source']}")
                print()
            return 0

        if args.command == "read":
            log = ReadLog(_read_log_path(settings))
            log.record(
                ChartRead(
                    ref=args.ref,
                    symbol=args.symbol or settings.symbol,
                    timeframe=args.timeframe,
                    observed=args.observed,
                    rule_says=RuleVerdict(args.rule),
                    claude_view=args.view,
                    conviction=Conviction(args.conviction),
                    entry=Decimal(args.entry) if args.entry else None,
                    stop=Decimal(args.stop) if args.stop else None,
                    target=Decimal(args.target) if args.target else None,
                    trigger_condition=args.when,
                ),
                now=now,
            )
            log.close()
            if args.when:
                print(f"Bedingung festgehalten als {args.ref}:")
                print(f"  {args.when}")
                print("\nTritt sie ein: `run_gate trigger --ref " + args.ref + "`")
            else:
                print(f"Einschätzung festgehalten als {args.ref}.")
            print("Sie ist ab jetzt nicht mehr änderbar und wird gegen ihr Ergebnis gehalten.")
            return 0

        if args.command == "trigger":
            log = ReadLog(_read_log_path(settings))
            try:
                log.mark_triggered(args.ref, now=now)
                print(f"{args.ref}: Bedingung eingetreten.")
                print("Jetzt `run_gate check` — das Gate entscheidet, ob es trotzdem geht.")
            except (KeyError, ValueError) as exc:
                print(str(exc))
                return 1
            finally:
                log.close()
            return 0

        if args.command == "armed":
            log = ReadLog(_read_log_path(settings))
            rows = log.armed()
            if not rows:
                print("Keine wartenden Bedingungen.")
            for row in rows:
                print(f"{row['ref']}  {row['symbol']} {row['timeframe']}  ({row['conviction']})")
                print(f"    WENN       {row['trigger_condition']}")
                print(f"    dann       Einstieg {row['entry'] or '?'}  Stop {row['stop'] or '?'}")
                print(f"    gesehen    {row['observed']}")
                print(f"    Regel      {row['rule_says']}")
                print()
            log.close()
            return 0

        if args.command == "calibration":
            log = ReadLog(_read_log_path(settings))
            print(calibration(log, settings.journal_path))
            log.close()
            return 0

        if args.command == "context":
            print(_context(settings, journal, ledger, now))
            return 0

        if args.command == "review":
            print(review_journal(journal))
            comparison = deviation_comparison(journal)
            print(f"\nPlan befolgt:    {comparison['followed_plan']}")
            print(f"Abgewichen:      {comparison['discretionary']}")
            by_source = source_comparison(journal)
            if len(by_source) > 1:
                print("\nNach Signalquelle — welche war es wert?")
                for source, summary in by_source.items():
                    print(f"  {source:<24} {summary}")
            return 0
    finally:
        journal.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
