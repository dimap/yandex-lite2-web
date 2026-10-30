"""Explicit hardware profiles. Lite 2 keeps its original implementation by default."""
import os
import json
import shlex
import tempfile
import threading
import time
from pathlib import Path

# Функции, которые на других моделях открыты «на пробу» с предупреждением. Когда люди подтвердили, что функция
# работает на модели, — впишите её в tested этой модели: предупреждение пропадёт. Новые функции — сюда же.
# Работа с экраном (заставка, анимации и дисплей часов) — не здесь: у моделей разные экраны, её открывают флаги
# clock/boot только после проверки.
TRIAL_FEATURES = ("assistant", "scenes", "alarm", "anims", "sounds", "say", "yam2", "btout", "btpower")
# ledpanel — LED-панель 88 диодов (/dev/fb0 через штатный led-play): есть только у Станции 2, проверено на устройстве.

PROFILES = {
    # Лайт первого поколения (PR #6): дисплея нет, кольцо из трёх RGB-диодов micro:*:ring1–3 через /dev/fb0, как у Лайт 2.
    'lite': dict(name='Станция Лайт', platform='yandexmicro', led_prefix='micro', clock=False, boot=False, alarms=False,
                 light=True, tested=('alarm', 'sounds', 'say')),   # PR #6: хук, будильник, звуки; Issue #23: «Сказать»
    'lite2': dict(name='Станция Лайт 2', platform='plum', clock=True, boot=True, alarms=True, light=True, tested=TRIAL_FEATURES),
    # Lite 2 без сегментного дисплея (например, Prune): кольцо и остальное как у Lite 2.
    'lite2nc': dict(name='Станция Лайт 2 (без часов)', platform='plum', clock=False, boot=False, alarms=True,
                    light=True, tested=()),
    'station2': dict(name='Станция 2', platform='yandexmidi', clock=False, boot=False, alarms=False, light=True, ledpanel=True),
    'mini3pro': dict(name='Станция Мини 3 Про', platform='pickle', clock=True, boot=False, alarms=False, light=True),
    # Common ADB features and a distinct 3D model; LED layout has not been verified on hardware.
    'mini3': dict(name='Станция Мини 3', platform='bergamot', clock=False, boot=False, alarms=False, light=False, experimental=True),
    # Glagol всё равно берёт тип из common.deviceType; светодиоды называются mini2:*.
    'mini2': dict(name='Станция Мини 2 с часами', platform='yandexmini_2', led_prefix='mini2', clock=True,
                  clock_max=170, bt_volume=False, boot=False, alarms=False, light=True,
                  tested=('say',)),   # «Сказать на колонке» — issue #16
    # Та же плата без clock-сегментов: кольцо 6 RGB, без дисплея.
    'mini2nc': dict(name='Станция Мини 2 (без часов)', platform='yandexmini_2', led_prefix='mini2', clock=False,
                    clock_max=170, bt_volume=False, boot=False, alarms=False, light=True, tested=()),
}

# Мини 2: цифры 1–4 из сегментов A–G без точек H, двоеточие PLUS1/PLUS2, MINUS посередине, DEGREE.
# Остальная панель работает с 36 сегментами Лайт 2, поэтому имена Лайт 2 → сегменты Мини 2.
# PLUS3/PLUS4 в sysfs есть, но прошивка их не использует — не трогаем.
MINI2_CLOCK = {**{f'{s}{p}': f'{s}{p + 1}' for p in range(4) for s in 'ABCDEFG'},
               'DEGREE': 'DEGREE', 'DH': 'PLUS2', 'DL': 'PLUS1', 'DM': 'MINUS'}
MINI2_CLOCK_DIR = '/tmp/webpanel_clock/'
# Кадр .binscled Мини 2: флаг, 32 яркости (цифры по A–G, затем PLUS1, PLUS2, MINUS, DEGREE), мс.
MINI2_FRAME_LEDS = [f'{s}{p}' for p in range(4) for s in 'ABCDEFG'] + ['DL', 'DH', 'DM', 'DEGREE']
MINI2_FRAME = 1 + len(MINI2_FRAME_LEDS) + 2


def mini2_clock_dir_script(leds='/sys/class/leds', target=MINI2_CLOCK_DIR):
    """Каталог с именами Лайт 2: ссылки на сегменты Мини 2, точкам H — обычные файлы (их на дисплее нет)."""
    d = target.rstrip('/')
    links = ' '.join(f'{name}:{physical}' for name, physical in MINI2_CLOCK.items())
    return (f'[ -d {d} ] || {{ T={d}.$$; rm -rf $T; mkdir -p $T && '
            f'for e in {links}; do ln -s {leds}/mini2:white:clock${{e#*:}} $T/${{e%%:*}}; done && '
            f'for p in 0 1 2 3; do mkdir $T/H$p; echo 0 > $T/H$p/brightness; done && mv $T {d} || rm -rf $T; }}')


def mini2_decode_clock_pattern(s, name, data):
    if name.endswith('.zst'):
        data = s.zstd_decompress(data)
    index = {name: i for i, name in enumerate(MINI2_FRAME_LEDS)}
    frames = []
    for i in range(0, len(data) - MINI2_FRAME + 1, MINI2_FRAME):
        b = data[i:i + MINI2_FRAME]
        values = b[1:-2]
        frames.append({'flag': b[0], 'v': [values[index[n]] if n in index else 0 for n in s.CLOCK_LEDS],
                       'ms': int.from_bytes(b[-2:], 'big')})
    return frames


def mini2_encode_clock_pattern(s, frames):
    out = bytearray()
    for f in frames:
        v = [max(0, min(PROFILES['mini2']['clock_max'], int(x))) for x in f['v']]
        if len(v) != len(s.CLOCK_LEDS):
            raise s.AdbError(f'в кадре должно быть {len(s.CLOCK_LEDS)} значений')
        by_name = dict(zip(s.CLOCK_LEDS, v))
        ms = max(10, min(65535, int(f.get('ms', 100))))
        out += bytes([1 if f.get('flag') else 0, *(by_name[n] for n in MINI2_FRAME_LEDS)]) + ms.to_bytes(2, 'big')
    if not out:
        raise s.AdbError('нет ни одного кадра')
    return bytes(out)


# led_util, запущенный панелью для анимации кольца
MINI2_STOP_RING = ('for f in /tmp/webpanel_ring.pid /tmp/webpanel_ring.sd; do '
                   '[ -f $f ] && kill $(cat $f) 2>/dev/null; rm -f $f; done; true')


def mini2_ring_script(rgb=None, leds='/sys/class/leds', state='/tmp/webpanel_ring.before'):
    """Кольцо Мини 2 — 6 RGB без триггеров: Quasar сам пишет brightness. Перед своим цветом запоминаем
    прежний, сброс возвращает его."""
    names = ' '.join(f'mini2:{color}:ring{i}' for i in range(1, 7) for color in ('red', 'green', 'blue'))
    lines = ['D=' + shlex.quote(leds), 'S=' + shlex.quote(state)]
    if rgb is not None:
        lines.append(f'[ -s "$S" ] || for n in {names}; do echo "$n $(cat "$D/$n/brightness")"; done > "$S"')
        lines += [f'echo {max(0, min(255, int(value)))} > "$D/mini2:{color}:ring{i}/brightness"'
                  for i in range(1, 7) for color, value in zip(('red', 'green', 'blue'), rgb)]
    else:
        lines.append('if [ -s "$S" ]; then while read n v; do echo "$v" > "$D/$n/brightness"; done < "$S"; '
                     f'else for n in {names}; do echo 0 > "$D/$n/brightness"; done; fi; rm -f "$S"')
    return '\n'.join(lines)


def mini3pro_ring_script(rgb=None, leds='/sys/class/leds', state='/tmp/webpanel_ring.state'):
    """Snapshot brightness AND triggers; reset repairs the old detached `none` state.

    Pickle RGB outputs use ledfb-ring. Separate pickle:ring:sync[01] LEDs use
    ledfb-ring-sync and are deliberately never written here.
    """
    names = [f'pickle:{color}:ring{i}' for i in range(16) for color in ('red','green','blue')]
    lines = ['set -e', 'D='+shlex.quote(leds), 'S='+shlex.quote(state), 'N='+shlex.quote(' '.join(names)),
             'for n in $N; do test -r "$D/$n/brightness"; '
             'case " $(cat "$D/$n/trigger") " in *" ledfb-ring "*|*" [ledfb-ring] "*) ;; *) exit 1;; esac; done']
    if rgb is not None:
        lines += ['if [ ! -s "$S" ]; then', 'T="$S.$$"; trap \'rm -f "$T"\' 0',
                  'for n in $N; do v=$(cat "$D/$n/brightness"); '
                  't=$(sed -n \'s/.*\\[\\([^]]*\\)\\].*/\\1/p\' "$D/$n/trigger"); '
                  'test -n "$t"; printf "%s %s %s\\n" "$n" "$v" "$t"; done > "$T"',
                  '[ "$(wc -l < "$T")" -eq 48 ]; mv "$T" "$S"', 'fi']
        for i in range(16):
            for color,value in zip(('red','green','blue'),rgb):
                lines.append(f'echo {max(0,min(255,int(value)))} > "$D/pickle:{color}:ring{i}/brightness"')
    else:
        lines += ['if [ -s "$S" ]; then [ "$(wc -l < "$S")" -eq 48 ];',
                  '  for n in $N; do row=$(awk -v name="$n" \'$1 == name {print $2, $3}\' "$S"); '
                  'set -- $row; [ "$#" -eq 2 ]; case "$1" in \'\'|*[!0-9]*) exit 1;; esac; [ "$1" -le 255 ]; '
                  't=$2; [ "$t" != none ] || t=ledfb-ring; '
                  'case " $(cat "$D/$n/trigger") " in *" $t "*|*" [$t] "*) ;; *) exit 1;; esac; done; fi',
                  'for n in $N; do',
                  '  t=ledfb-ring',
                  '  if [ -s "$S" ]; then',
                  '    row=$(awk -v name="$n" \'$1 == name {print $2, $3}\' "$S"); set -- $row; [ "$#" -eq 2 ]',
                  '    v=$1; t=$2; case "$v" in \'\'|*[!0-9]*) exit 1;; esac; [ "$v" -le 255 ]',
                  '    echo "$v" > "$D/$n/brightness"',
                  '    case "$t" in none) t=ledfb-ring;; esac',
                  '  fi',
                  '  echo "$t" > "$D/$n/trigger"',
                  'done', 'rm -f "$S"']
    return '\n'.join(lines)


def install(s, model='lite2', auto=False):
    family = {'lite2nc': 'lite2', 'mini2nc': 'mini2'}.get(model, model)
    profile = dict(PROFILES[model], id=model, auto=auto)
    profile['tested'] = list(profile.get('tested', ()))   # подтверждено людьми — без предупреждения
    profile['ledpanel'] = bool(profile.get('ledpanel'))
    profile['light_note'] = ('Подсветка этой модели ещё не проверена.' if not profile['light'] else
                             'Анимация Алисы может заменить выбранный цвет.')
    s.GLAGOL_PLATFORM = profile['platform']
    s.LED_PREFIX = profile.get('led_prefix', profile['platform'])
    original_get, original_post = s.Handler.do_GET, s.Handler.do_POST

    def do_get(self):
        if family != 'lite2' and self.path.split('?')[0] == '/manifest.webmanifest':
            if not self._guard('/manifest.webmanifest'):
                manifest = json.loads((s.STATIC/'manifest.webmanifest').read_text(encoding='utf8'))
                manifest.update(name='Моя станция', short_name='Моя станция',
                                description='Панель управления: ' + profile['name'])
                self._send(200, json.dumps(manifest,ensure_ascii=False).encode('utf8'), s.MIME['.webmanifest'])
            return
        if self.path.split('?')[0] == '/api/profile':
            if not self._guard('/api/profile'):
                self._send(200, profile)
            return
        if not profile['ledpanel'] and self.path.split('?')[0].startswith('/api/led/'):
            if not self._guard(self.path.split('?')[0]):
                self._send(400, {'error': 'LED-панель есть только у Станции 2 (профиль ' + model + ')'})
            return
        original_get(self)

    def do_post(self):
        path = self.path.split('?')[0]
        # Запуск и будильник на других моделях — на пробу; заставка часов рассчитана на дисплей Лайт 2
        disabled = (not profile['boot'] and path in ('/api/clock/boot', '/api/clock/boot/reset') or
                    family != 'lite2' and path.startswith('/api/radio') and path != '/api/radio/alice' or
                    family != 'lite2' and path in ('/api/footprint/reset', '/api/backup/restore'))
        if disabled:
            if not self._guard(path):
                self._send(400, {'error': 'Эта операция загрузки/восстановления не поддерживается профилем ' + model})
            return
        if not profile['ledpanel'] and (path == '/api/led' or path.startswith('/api/led/')):
            if not self._guard(path):
                self._send(400, {'error': 'LED-панель есть только у Станции 2 (профиль ' + model + ')'})
            return
        original_post(self)

    s.Handler.do_GET, s.Handler.do_POST = do_get, do_post

    def unsupported(self=None, *args, **kwargs):
        raise s.AdbError('Функция не проверена для профиля ' + model)

    P = s.Panel
    if family == 'lite2':
        if model == 'lite2':
            return  # No changes to the original Lite 2 hardware, boot hook, YAM or alarm code.
        # Lite 2 без часов: кольцо и остальное как у Lite 2, но без segment-дисплея.
        for method in ('clock', 'display_set', 'play_clock', 'clock_frames'):
            setattr(P, method, unsupported)

        def clock_state(self):
            names = [f'plum:{color}:ring{i}' for i in (1, 2, 3) for color in ('red', 'green', 'blue')]
            out, _, _ = self.adb.shell('cd /sys/class/leds; ' + '; '.join(f'cat {name}/brightness' for name in names))
            values = [int(value) for value in out.split() if value.isdigit()]
            if len(values) != len(names):
                raise s.AdbError('Неполное состояние светодиодов')
            ring = [values[i * 3:i * 3 + 3] for i in range(3)]
            return {'v': [0] * 36, 'ring': ring, 'ring_all': ring}

        P.clock_state = clock_state

        class LedStream:
            def __init__(self, adb):
                self.adb, self.at, self.cached, self.lock = adb, 0, {'v': [0] * 36, 'ring': [[0, 0, 0]] * 3}, threading.Lock()

            def get(self):
                with self.lock:
                    if time.monotonic() - self.at > .4:
                        self.cached, self.at = clock_state(self), time.monotonic()
                    return self.cached

        s.LedStream = LedStream
        return
    if model == 'mini2':
        s.CLOCK_PATH = MINI2_CLOCK_DIR
        s.decode_clock_pattern = lambda name, data: mini2_decode_clock_pattern(s, name, data)
        s.encode_clock_pattern = lambda frames: mini2_encode_clock_pattern(s, frames)

    if family == 'mini2':
        original_set_volume = P.set_volume

        def set_volume(self, data):
            if 'bt' in data:
                raise s.AdbError('У Mini 2 нет отдельного Bluetooth-регулятора; используйте общую громкость')
            return original_set_volume(self, data)

        P.set_volume = set_volume
        if model == 'mini2':
            top = profile['clock_max']   # предел яркости дисплея часов (max_brightness)
            original_clock, original_display_set, original_play_clock = P.clock, P.display_set, P.play_clock

            def clock(self, data):
                data = dict(data)
                if 'v' in data:
                    data['v'] = [max(0, min(top, int(value))) for value in data['v']]
                if 'brightness' in data:
                    data['brightness'] = max(0, min(top, int(data['brightness'])))
                return original_clock(self, data)

            def display_set(self, data):
                data = dict(data)
                if 'v' in data:
                    data['v'] = [max(0, min(top, int(value))) for value in data['v']]
                data['brightness'] = max(1, min(top, int(data.get('brightness', 160))))
                return original_display_set(self, data)

            def play_clock(self, frames, seconds):
                frames = [{**frame, 'v': [max(0, min(top, int(value))) for value in frame['v']]}
                          for frame in frames]
                return original_play_clock(self, frames, seconds)

            P.clock, P.display_set, P.play_clock = clock, display_set, play_clock
    original_shell = s.Adb.shell

    def shell(self, command, timeout=15, check=True):
        # Older adbd can print a root-shell banner and lose the remote exit status.
        marker = '__PANEL_' + os.urandom(8).hex() + '__'
        if family == 'mini2' and MINI2_CLOCK_DIR in command:
            # /tmp чистится при перезагрузке колонки — каталог сегментов создаём при первой нужде.
            command = mini2_clock_dir_script() + '\n' + command
        wrapped = (f"printf '{marker}\\n'; sh -c {shlex.quote(command)}; rc=$?; "
                   f"printf '\\n{marker}RC=%s\\n' \"$rc\"")
        out, err, _ = original_shell(self, wrapped, timeout, False)
        if marker + '\n' not in out:
            raise s.AdbError('Нет ответа shell колонки')
        body = out.split(marker + '\n', 1)[1]
        body, separator, result = body.rpartition('\n' + marker + 'RC=')
        if not separator or not result.strip().isdigit():
            raise s.AdbError('Команда колонки не завершилась')
        code = int(result.strip())
        if check and code:
            raise s.AdbError(s.strip_ansi(err or body).strip()[-500:] or f'Код завершения: {code}')
        return body, err, code

    s.Adb.shell = shell

    def alive(self):
        try:
            return self.adb.shell('echo ok', timeout=4)[0].strip() == 'ok'
        except (s.AdbError, s.subprocess.TimeoutExpired):
            return False
    s.Connection._alive = alive

    def version(self):
        if self._version is None:
            out, _, _ = self.adb.shell('cat /system/vendor/quasar/version_full')
            device, _, _ = self.adb.shell(f'{s.QRUN}/maind --device-id 2>/dev/null | tail -1', timeout=20)
            did = device.replace('Device ID:', '').strip()
            self._version = {'quasar_version': out.strip(), 'device_id': did, 'model': model}
            self._configure_glagol(did)
        return self._version
    P.version = version

    if not profile['clock']:
        for method in ('clock', 'display_set', 'play_clock', 'clock_frames'):
            setattr(P, method, unsupported)
    # Хук в rootkeeper.sh (звук запуска, автозапуск будильника и ассистента) — только если файл есть или его
    # вызывает штатный onboot.sh, иначе _ensure_hook сам откажет. На Мини 3 оборудование не сверено — не трогаем.
    if profile.get('experimental'):
        P._ensure_hook = unsupported
    if family != 'lite':    # официальный установщик старого yam знал Лайт (yandexmicro) и ставил его туда же, что на Лайт 2
        for method in ('yam_install_daemon', 'yam_update_daemon', 'yam_autostart'):
            if hasattr(P, method):
                setattr(P, method, unsupported)
    # Помощник проверен только на Лайт 2: здесь — на пробу, без хука автозапуска Лайт 2 (до перезагрузки колонки)
    original_assistant_status = P.assistant_status
    P.assistant_status = lambda self: {**original_assistant_status(self), 'experimental': 'assistant' not in profile['tested'],
                                         'model_name': profile['name']}
    original_radio_status = P.radio_status
    P.radio_status = lambda self: {**original_radio_status(self), 'supported': False}
    original_boot_status = P.boot_status

    def boot_status(self):
        st = original_boot_status(self)
        if not profile['boot']:   # без заставки часов: звук запуска и «когда показывать» остаются
            st.update(supported=False, custom=False, mounted=False)
            st.pop('default', None), st.pop('frames', None)
        return st
    P.boot_status = boot_status

    # Existing community YAM installations on the new profiles use this layout; Mini 2 keeps the Lite 2 one.
    old = s.YAM_DIR
    for name, value in list(vars(s).items()):
        if family not in ('mini2', 'lite') and name.startswith('YAM') and isinstance(value, str):
            setattr(s, name, value.replace(old, '/data/local/yam'))

    def patterns(self):
        if family not in ('mini3pro', 'mini2'):
            return {'ring': [], 'clock': []}
        out, _, _ = self.adb.shell(f'find {s.QUASAR}/ledpatterns/clock -maxdepth 1 -type f 2>/dev/null; true')
        result = {'ring': [], 'clock': [Path(line).name for line in out.splitlines()]}
        if family == 'mini2':   # кольцо Мини 2 штатный led_util рисует сам
            out, _, _ = self.adb.shell(f"find {s.PATTERNS['ring']} -maxdepth 1 -name '*.led' -type f 2>/dev/null; true")
            result['ring'] = sorted(Path(line).name for line in out.splitlines())
            if model == 'mini2nc':
                result['clock'] = []
        return result
    P.patterns = patterns

    original_play = P.play_pattern

    def play_pattern(self, kind, name, seconds):
        if kind == 'ring' and family == 'mini2':
            return original_play(self, kind, name, seconds)
        if kind != 'clock' or model not in ('mini3pro', 'mini2'):
            return unsupported()
        if name not in self.patterns()['clock']:
            raise s.AdbError('Нет такой анимации')
        return self.play_clock(s.decode_clock_pattern(name, self.adb.read_bytes(f'{s.QUASAR}/ledpatterns/clock/{name}')), seconds)
    P.play_pattern = play_pattern

    if family == 'lite':
        s.RING_LEDS = [f'micro:{c}:ring{i}' for i in (1, 2, 3) for c in ('red', 'green', 'blue')]
        s.RING_TRIGGER_CMD = ('for n in ' + ' '.join(s.RING_LEDS) + "; do grep -q '\\[led_ring_fb\\]' " + s.LEDS
                              + '/$n/trigger 2>/dev/null || echo led_ring_fb > ' + s.LEDS + '/$n/trigger 2>/dev/null; done; true')

    def leds(self):
        if family == 'lite':
            out, _, _ = self.adb.shell('cd /sys/class/leds; ' + '; '.join(f'cat {name}/brightness' for name in s.RING_LEDS))
            values = [int(value) for value in out.split() if value.isdigit()]
            if len(values) != len(s.RING_LEDS):
                raise s.AdbError('Неполное состояние светодиодов')
            ring = [values[i * 3:i * 3 + 3] for i in range(3)]
            return {'v': [0] * 36, 'ring': ring, 'ring_all': ring}
        if family == 'mini3':
            return {'v': [0] * 36, 'ring': [[0, 0, 0]] * 3}
        if family == 'station2':
            temporary = '/tmp/webpanel_fb_' + os.urandom(8).hex()
            try:
                self.adb.shell(f'head -c 356 /dev/fb0 > {temporary}')
                blob = self.adb.read_bytes(temporary)
            finally:
                self.adb.shell(f'rm -f {temporary}', check=False)
            if len(blob) != 356:
                raise s.AdbError('Неожиданный размер световой панели')
            # gowin_led_device consumes RGBX despite its advertised BGRX offsets.
            pixels = [[blob[i], blob[i + 1], blob[i + 2]] for i in range(0, 356, 4)]
            representative = [[round(sum(pixel[k] for pixel in pixels[a:b]) / (b-a)) for k in range(3)]
                              for a, b in ((0, 30), (30, 60), (60, 89))]
            return {'v': [0] * 36, 'ring': representative, 'ring_all': pixels}
        if family == 'mini2':
            ring_names = [f'mini2:{color}:ring{i}' for i in range(1, 7) for color in ('red', 'green', 'blue')]
            names = ring_names
            if model == 'mini2':
                names = [f'mini2:white:clock{MINI2_CLOCK[name]}' for name in s.CLOCK_LEDS if name in MINI2_CLOCK] + ring_names
            out, _, _ = self.adb.shell('cd /sys/class/leds; ' + '; '.join(f'cat {name}/brightness' for name in names))
            values = [int(value) for value in out.split() if value.isdigit()]
            if len(values) != len(names):
                raise s.AdbError('Неполное состояние светодиодов')
            if model == 'mini2nc':
                ring = [values[i * 3:i * 3 + 3] for i in range(6)]
                return {'v': [0] * 36, 'ring': ring, 'ring_all': ring}
            clock = iter(values[:len(MINI2_CLOCK)])
            ring = [values[len(MINI2_CLOCK) + i * 3:len(MINI2_CLOCK) + i * 3 + 3] for i in range(6)]
            return {'v': [next(clock) if name in MINI2_CLOCK else 0 for name in s.CLOCK_LEDS], 'ring': ring, 'ring_all': ring}
        names = [f'pickle:white:clock{name}' for name in s.CLOCK_LEDS]
        names += [f'pickle:{color}:ring{i}' for i in range(16) for color in ('red', 'green', 'blue')]
        out, _, _ = self.adb.shell('cd /sys/class/leds; ' + '; '.join(f'cat {name}/brightness' for name in names))
        values = [int(value) for value in out.split() if value.isdigit()]
        if len(values) != 84:
            raise s.AdbError('Неполное состояние светодиодов')
        ring = [values[36+i*3:39+i*3] for i in range(16)]
        return {'v': values[:36], 'ring': [ring[i] for i in (0, 5, 10)], 'ring_all': ring}
    P.clock_state = leds

    class LedStream:
        def __init__(self, adb):
            self.adb, self.at, self.cached, self.lock = adb, 0, {'v': None}, threading.Lock()
        def get(self):
            with self.lock:
                if time.monotonic() - self.at > .4:
                    self.cached, self.at = leds(self), time.monotonic()
                return self.cached
    s.LedStream = LedStream

    def ring(self, data):
        if not profile['light']:
            return unsupported()
        rgb = [max(0, min(255, int(data.get(color, 0)))) for color in ('red', 'green', 'blue')]
        if family == 'lite':
            self.adb.shell(s.RING_TRIGGER_CMD + '; ' + s.ring_fb_cmd([rgb] * 3))
        elif family == 'mini3pro':
            self.adb.shell(mini3pro_ring_script(rgb))
        elif family == 'mini2':
            self.adb.shell(MINI2_STOP_RING + '\n' + mini2_ring_script(rgb))
        else:
            self.adb.shell('test "$(cat /sys/class/graphics/fb0/virtual_size)" = "89,1" && '
                           'test "$(cat /sys/class/graphics/fb0/bits_per_pixel)" = 32 || exit 1; '
                           '[ -f /tmp/webpanel_ring.before ] || head -c 356 /dev/fb0 > /tmp/webpanel_ring.before')
            with tempfile.TemporaryDirectory() as directory:
                frame = Path(directory) / 'frame'
                frame.write_bytes(bytes((rgb[0], rgb[1], rgb[2], 0)) * 89)
                self.adb.push(frame, '/tmp/webpanel_ring.frame')
            self.adb.shell('cat /tmp/webpanel_ring.frame > /dev/fb0')
        return {'ok': True}
    P.ring = ring

    def reset_ring(self):
        if family == 'mini3':
            return {'ok': True}
        if family == 'lite':
            command = s.RING_TRIGGER_CMD + '; ' + s.ring_fb_cmd([(0, 0, 0)] * 3)
        elif family == 'mini3pro':
            command = mini3pro_ring_script()
        elif family == 'mini2':
            command = mini2_ring_script()
        else:
            command = 'if [ -s /tmp/webpanel_ring.before ]; then cat /tmp/webpanel_ring.before > /dev/fb0; rm -f /tmp/webpanel_ring.before; fi'
        self.adb.shell(command)
        return {'ok': True, 'leds': self.clock_state()['ring']}
    P.ring_reset = reset_ring
    original_stop = P.stop_pattern
    def stop(self, kind=None):
        if kind in (None, 'ring'):
            if family == 'mini2':
                self.adb.shell(MINI2_STOP_RING)
            self.ring_reset()
        if kind in (None, 'clock') and profile['clock']:
            original_stop(self, 'clock')
        return {'ok': True}
    P.stop_pattern = stop

    if family in ("station2", "mini3pro"):
        try:
            import managed_yam
        except ImportError:     # архив обновлён кодом 0.1.3, а он новые модули не копирует
            print('Нет managed_yam.py — обновление yamd из панели для этой модели недоступно')
        else:
            managed_yam.install(s)
