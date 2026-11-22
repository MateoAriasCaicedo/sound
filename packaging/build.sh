#!/usr/bin/env bash
# Compila Sound para Linux con PyInstaller (onedir) y deja el .tar.gz en dist/.
#
# Requisitos en la máquina que compila: Python 3.11+ con PyGObject (`python3-gi`) y los
# plugins de GStreamer. El entorno virtual debe crearse con --system-site-packages para que
# PyInstaller vea `gi`; si no, la build sale sin reproductor.
#
# Uso:
#   python3 -m venv --system-site-packages .venv
#   .venv/bin/pip install --ignore-installed -e ".[dev]" -c constraints.txt
#   packaging/build.sh                 # con el Python del entorno activo
#   PYTHON=.venv/bin/python packaging/build.sh
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

PYTHON="${PYTHON:-python3}"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' app/__init__.py)"
ARCH="$(uname -m)"
TARBALL="dist/Sound-${VERSION}-linux-${ARCH}.tar.gz"

echo "Sound ${VERSION} · Linux ${ARCH}"

# Sin GStreamer la app abre, pero no reproduce: mejor fallar aquí que publicar una build muda.
"${PYTHON}" - <<'PY'
import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

Gst.init(None)
missing = [name for name in ("playbin", "autoaudiosink", "souphttpsrc")
           if Gst.ElementFactory.find(name) is None]
if missing:
    raise SystemExit(
        "Faltan plugins de GStreamer: " + ", ".join(missing) +
        "\nInstala gstreamer1.0-plugins-base/good/bad/ugly y gir1.2-gstreamer-1.0.")
PY

"${PYTHON}" packaging/make_icon.py
"${PYTHON}" -m PyInstaller --noconfirm --clean sound.spec

test -x "dist/Sound/Sound" || { echo "No se generó dist/Sound/Sound" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
SOUND_DATA_DIR="$TMP/data" SOUND_MUSIC_DIR="$TMP/music" SOUND_NO_MIGRATE=1 \
    SOUND_LOG="$TMP/sound.log" ./dist/Sound/Sound --self-test

cat > dist/LEEME.txt <<TXT
Sound ${VERSION} para Linux (${ARCH})
=====================================

1. Descomprime este archivo.
2. Ejecuta Sound/Sound (o crea un acceso directo que apunte a él).

Los datos se guardan en ~/.local/share/sound y la música en ~/Music/Sound.
La build oficial incluye Python, Tk, GStreamer y Node.js; necesita un escritorio con
PipeWire, PulseAudio o ALSA para el sonido. Si la compilaste tú sin packaging/node/node,
ten Node.js en el PATH para que yt-dlp resuelva los enlaces de YouTube.
TXT

tar -czf "$TARBALL" -C dist Sound LEEME.txt
( cd dist && sha256sum "$(basename "$TARBALL")" ) > "$TARBALL.sha256"
rm -f dist/LEEME.txt

echo "Listo: $TARBALL"
