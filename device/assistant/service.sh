#!/bin/sh
# Installed in /data, called by our own /etc/init.d entry. No changes to Quasar.
BASE=/data/quasar/webpanel/assistant
PID=/tmp/webpanel-assistant-supervisor.pid
case "${1:-start}" in
  start)
    [ -x "$BASE/station-assistant" ] || exit 1
    start-stop-daemon -S -b -m -p "$PID" -x /bin/sh -- "$BASE/service.sh" run
    ;;
  run)
    child=
    trap '[ -z "$child" ] || kill "$child" 2>/dev/null; [ -z "$child" ] || wait "$child"; rm -f "$PID"; exit 0' TERM INT
    while :; do
      GOMEMLIMIT=24MiB GOMAXPROCS=1 "$BASE/station-assistant" serve </dev/null >/dev/null 2>&1 &
      child=$!
      wait "$child"
      child=
      sleep 5 &
      child=$!
      wait "$child"
    done
    ;;
  stop)
    if [ -f "$PID" ]; then
      pid=$(cat "$PID")
      case "$pid" in ''|*[!0-9]*) exit 1;; esac
      if ! kill -0 "$pid" 2>/dev/null; then rm -f "$PID"; exit 0; fi
      cmd=$(tr '\000' ' ' < "/proc/$pid/cmdline" 2>/dev/null)
      case "$cmd" in *"$BASE/service.sh run"*) kill "$pid";; *) exit 1;; esac
      i=0
      while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 15 ]; do sleep 1; i=$((i + 1)); done
      ! kill -0 "$pid" 2>/dev/null
    fi
    ;;
  *) exit 1;;
esac
