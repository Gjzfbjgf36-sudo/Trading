#!/usr/bin/env bash
# Einrichtung auf macOS. Einmal ausführen, danach nie wieder.
#
# Warum ein venv: macOS bringt ein System-Python mit, in das man nichts
# hineininstallieren sollte — neuere Versionen weigern sich sogar. Ein venv ist
# ein eigener, wegwerfbarer Ordner, in dem `python` und `pip` existieren. Genau
# deshalb schlägt `pip install` ohne ihn fehl, und `python` gibt es auf dem Mac
# gar nicht: dort heisst es `python3`.
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 fehlt. Im Terminal 'xcode-select --install' ausführen und"
    echo "danach dieses Skript erneut starten."
    exit 1
fi

echo "Python:  $(python3 --version)"
echo

if [ ! -d .venv ]; then
    echo "Lege .venv an ..."
    python3 -m venv .venv
fi

echo "Installiere arbcore und ccxt ..."
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -e . ccxt

if [ ! -f config/decide.yaml ]; then
    cp config/decide.example.yaml config/decide.yaml
    echo
    echo "config/decide.yaml angelegt. Trag dort deinen echten Gebührensatz ein —"
    echo "als Bruchteil: 0,26 % sind 0.0026, nicht 0.26."
fi

echo
echo "Fertig. Ab jetzt in JEDEM neuen Terminal zuerst:"
echo
echo "    cd $(pwd)"
echo "    source .venv/bin/activate"
echo
echo "Danach funktionieren 'python' und 'pip' wie erwartet. Erster Schritt:"
echo
echo "    python -m arbcore.app.run_gate setup"
echo
