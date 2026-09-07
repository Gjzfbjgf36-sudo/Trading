"""Start-up check: is this actually ready to use?

Written for someone setting the system up for the first time. It looks at what
is configured, says what is missing, and names the single next step — rather
than failing later with a message that assumes you already know the answer.

It never says the setup is "good". It says whether it is *complete*, which is a
different and answerable question.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .journal import Journal
from .settings import DEFAULT_PATH, DecideSettings, SettingsError, load_settings

#: Fee rates below this are almost certainly a percent written as a fraction by
#: mistake, or a tier the operator does not actually have.
IMPLAUSIBLY_LOW_FEE = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class CheckLine:
    ok: bool
    label: str
    detail: str = ""

    def render(self) -> str:
        mark = "OK  " if self.ok else "FEHLT"
        line = f"[{mark}] {self.label}"
        return f"{line}\n         {self.detail}" if self.detail else line


def run_checks(config_path: str = DEFAULT_PATH) -> tuple[list[CheckLine], str]:
    """Return the checks and the one next step that matters most."""
    lines: list[CheckLine] = []

    if not Path(config_path).exists():
        lines.append(
            CheckLine(
                False,
                "Konfiguration",
                f"{config_path} fehlt. Anlegen mit:\n"
                f"         cp config/decide.example.yaml {config_path}",
            )
        )
        return lines, f"Lege {config_path} an und trage deine Zahlen ein."

    try:
        settings = load_settings(config_path)
    except (SettingsError, ValueError) as exc:
        lines.append(CheckLine(False, "Konfiguration lesbar", str(exc)))
        return lines, f"Korrigiere {config_path}."

    lines.append(
        CheckLine(True, "Konfiguration gelesen", f"Startkapital {settings.starting_capital}")
    )
    return _content_checks(settings, lines)


def _content_checks(
    settings: DecideSettings, lines: list[CheckLine]
) -> tuple[list[CheckLine], str]:
    next_step = ""

    # The fee rate is the number most often wrong, and it silently decides
    # which strategies are affordable at all.
    if settings.fee_rate <= IMPLAUSIBLY_LOW_FEE:
        lines.append(
            CheckLine(
                False,
                "Gebührensatz",
                f"{settings.fee_rate} ist unplausibel niedrig. Schlag den echten "
                f"Taker-Satz deiner Börse nach — er entscheidet mit, welche "
                f"Strategien überhaupt tragbar sind.",
            )
        )
        next_step = next_step or "Trage deinen echten Gebührensatz ein."
    else:
        pct = (settings.fee_rate * Decimal(100)).quantize(Decimal("0.001"))
        lines.append(CheckLine(True, "Gebührensatz", f"{pct} % pro Seite"))

    lines.append(
        CheckLine(
            True,
            "Risikorahmen",
            f"{(settings.risk.risk_per_trade * 100).quantize(Decimal('0.1'))} % pro Trade, "
            f"{(settings.risk.daily_loss_limit * 100).quantize(Decimal('0.1'))} % Tageslimit, "
            f"{(settings.risk.max_drawdown * 100).quantize(Decimal('0.1'))} % Drawdown-Stopp",
        )
    )

    # Paper is the only correct answer at this stage, and the check says so
    # rather than merely reporting the value.
    if settings.real_money:
        lines.append(
            CheckLine(
                False,
                "Modus",
                "ECHTGELD. Die vier Bedingungen aus docs/ANLEITUNG.md müssen "
                "vorher alle erfüllt sein — insbesondere 30 abgeschlossene "
                "Papier-Trades mit positivem Erwartungswert.",
            )
        )
        next_step = next_step or "Auf real_money: false zurückstellen."
    else:
        lines.append(CheckLine(True, "Modus", "Papier — richtig für den Anfang"))

    journal_path = Path(settings.journal_path)
    try:
        journal = Journal(journal_path)
        closed = journal.conn.execute(
            "SELECT COUNT(*) AS n FROM commitments WHERE closed_at IS NOT NULL"
        ).fetchone()["n"]
        open_count = len(journal.open_positions())
        journal.close()
    except Exception as exc:  # noqa: BLE001 - any failure here blocks everything
        lines.append(CheckLine(False, "Journal", f"nicht beschreibbar: {exc}"))
        return lines, "Prüfe den Pfad journal_path in der Konfiguration."

    lines.append(
        CheckLine(
            True,
            "Journal",
            f"{closed} abgeschlossen, {open_count} offen ({journal_path})",
        )
    )

    # Resolved from the package location, not the working directory: someone
    # setting this up will run it from wherever they happen to be, and a check
    # that reports "no rules" because of a cd is worse than no check.
    strategy_dir = Path(__file__).resolve().parents[3] / "strategies"
    strategies = sorted(strategy_dir.glob("*.pine")) if strategy_dir.is_dir() else []
    if strategies:
        names = ", ".join(p.name for p in strategies)
        lines.append(CheckLine(True, "Regeln vorhanden", f"{names} — EINE auswählen"))
    else:
        lines.append(CheckLine(False, "Regeln", "keine .pine-Datei in strategies/"))
        next_step = next_step or "Regel-Dateien fehlen im Repository."

    if not next_step:
        if closed == 0 and open_count == 0:
            next_step = (
                "Alles bereit. Nächster Schritt: eine Regel in TradingView laden, "
                "Kommission und Slippage setzen (docs/ANLEITUNG.md Schritt 1), "
                "dann auf das erste Signal warten. Das kann Wochen dauern — bei "
                "2 Signalen im Monat ist das normal, nicht kaputt."
            )
        elif closed < 30:
            next_step = (
                f"{closed} von 30 Trades aufgezeichnet. Weiter aufzeichnen; "
                f"unter 30 sagt die Auswertung bewusst nichts."
            )
        else:
            next_step = "30+ Trades vorhanden. `run_gate review` zeigt, was sie ergeben."
    return lines, next_step


def report(config_path: str = DEFAULT_PATH) -> str:
    lines, next_step = run_checks(config_path)
    body = "\n".join(line.render() for line in lines)
    return f"{body}\n\nNÄCHSTER SCHRITT\n  {next_step}"
