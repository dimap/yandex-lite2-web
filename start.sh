#!/bin/sh
# Запуск панели на Linux / macOS: ./start.sh [SSH alias]
cd "$(dirname "$0")" || exit 1
python=${STATION_PYTHON:-${XDG_DATA_HOME:-/home/fleef/.local/share}/station-lite2/venv/bin/python}
if [ ! -x "$python" ]; then
    python=$(command -v python3) || {
    echo "Нужен Python 3.9 или новее: https://www.python.org/downloads/"
    exit 1
    }
fi
if ! "$python" -c 'import plum_sdk' >/dev/null 2>&1; then
    echo "Не найден plum_sdk. Установите SDK в Python-окружение или укажите STATION_PYTHON."
    exit 1
fi
if [ -n "$1" ] && [ "${1#-}" = "$1" ]; then
    alias="$1"; shift
    exec "$python" sdk_server.py --ssh-alias "$alias" "$@"
fi
exec "$python" sdk_server.py "$@"
