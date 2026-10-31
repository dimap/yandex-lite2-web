# Мини 2 и Миди: проверка изменения

Результаты на устройствах: [Миди](midi.md), [Мини 2](mini2.md).
Наборы Python проверяют профили, транспорт factory-9, чтение метаданных,
границы аппаратных операций, сохранение/возврат LED и загрузочные маршруты.
Проверки shell используют временные каталоги, а не реальную колонку.

```sh
python3 -m unittest discover -s tests -q
node tests/test_renderer.mjs
node tests/test_paths.mjs
node tests/test_model_studio.cjs
node tests/test_model_midi.mjs
node tests/test_catalog.cjs
node tests/test_update_ui.cjs
node tests/test_voice_input.mjs
node tests/test_midi_ui.cjs
node tests/test_midi_color_ui.cjs
node tests/test_midi_clock_ui.cjs
python3 -m compileall -q server.py profiles.py midi.py managed_yam.py multi.py tools/midi_readonly.py
python3 server.py --help
python3 multi.py --help
sh -n start.sh
sh -n start.command
git diff --check
```

Фактический результат прогона и ограничения окружения приводятся в описании PR.
Go/Docker-сборки не следует считать выполненными по результатам Python/Node.
