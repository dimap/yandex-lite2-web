"""Запуск панели внутри приложения для Android (Chaquopy).

Файлы панели приложение распаковывает в panel_dir; adb — свой, на чистом Python (pyadb), отдельной программы нет.
os.execv в приложении не работает, поэтому перезапуск (смена колонки или профиля) делает служба: сохраняет
аргументы и перезапускает свой процесс.
"""
import json
import os
import sys


def main(panel_dir, args_json, service):
    os.environ["PANEL_PYADB"] = "1"
    os.environ["PANEL_INSTALL"] = "android"
    sys.path.insert(0, panel_dir)
    sys.argv = [os.path.join(panel_dir, "server.py"), *json.loads(args_json)]
    import server
    server.RESTART_HOOK = lambda args: service.restart(json.dumps(args))
    server.main()
