#!/bin/sh
# Быстрые правки без пересборки APK (только отладочная сборка: нужен run-as).
# Кладёт файлы панели в распакованную копию внутри приложения и перезапускает сервер.
#   android/dev-push.sh static/index.html server.py …    (пути — от корня репозитория)
# Правки живут до следующей установки APK: при обновлении приложение распаковывает свои файлы заново.
set -e
cd "$(dirname "$0")/.."
ADB=${ADB:-adb}
PKG=io.github.dimap.yandexstation
[ $# -gt 0 ] || { echo "укажите файлы, например: $0 static/index.html server.py" >&2; exit 1; }
restart=
for f in "$@"; do
    $ADB push "$f" "/data/local/tmp/webpanel-dev" >/dev/null
    $ADB shell "run-as $PKG sh -c 'mkdir -p files/panel/$(dirname "$f") && cat /data/local/tmp/webpanel-dev > files/panel/$f'"
    echo "→ $f"
    case "$f" in *.py) restart=1 ;; esac
done
$ADB shell rm -f /data/local/tmp/webpanel-dev
if [ -n "$restart" ]; then   # Python перечитывается только при запуске: убиваем процесс сервера, окно поднимет его заново
    $ADB shell "run-as $PKG sh -c 'kill \$(pidof $PKG:panel)'" 2>/dev/null || true
    echo "сервер перезапускается"
fi
echo "обновите страницу в приложении (или откройте его заново)"
