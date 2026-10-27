"""Explicit hardware profiles. Lite 2 keeps its original implementation by default."""
import os
import json
import re
import shlex
import tempfile
import threading
import time
from pathlib import Path

PROFILES = {
    'lite2': dict(name='Станция Лайт 2', platform='plum', clock=True, boot=True, alarms=True, light=True),
    'station2': dict(name='Станция 2', platform='yandexmidi', clock=False, boot=False, alarms=False, light=True),
    'mini3pro': dict(name='Станция Мини 3 Про', platform='pickle', clock=True, boot=False, alarms=False, light=True),
    # Common ADB features and a distinct 3D model; LED layout has not been verified on hardware.
    'mini3': dict(name='Станция Мини 3', platform='bergamot', clock=False, boot=False, alarms=False, light=False, experimental=True),
    'mini2': dict(name='Станция Мини 2', platform='yandexmini_2', clock=True, boot=False, alarms=False, light=True, experimental=True),
}


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
    profile = dict(PROFILES[model], id=model, auto=auto)
    profile['light_note'] = ('Подсветка этой модели ещё не проверена.' if not profile['light'] else
                             'Анимация Алисы может заменить выбранный цвет.')
    s.GLAGOL_PLATFORM = profile['platform']
    s.LED_PREFIX = profile['platform']
    original_get, original_post = s.Handler.do_GET, s.Handler.do_POST

    def do_get(self):
        if model != 'lite2' and self.path.split('?')[0] == '/manifest.webmanifest':
            if not self._guard('/manifest.webmanifest'):
                manifest = json.loads((s.STATIC/'manifest.webmanifest').read_text(encoding='utf8'))
                manifest.update(name=profile['name'], short_name=profile['name'], description='Панель управления: '+profile['name'])
                self._send(200, json.dumps(manifest,ensure_ascii=False).encode('utf8'), s.MIME['.webmanifest'])
            return
        if self.path.split('?')[0] == '/api/profile':
            if not self._guard('/api/profile'):
                self._send(200, profile)
            return
        original_get(self)

    def do_post(self):
        path = self.path.split('?')[0]
        if model == 'mini2' and path.startswith('/api/yam/'):
            if not self._guard(path):
                self._send(400, {'error': profile['yam_note']})
            return
        disabled = (not profile['boot'] and (path.startswith('/api/clock/boot') or path.startswith('/api/boot/')) or
                    not profile['alarms'] and path.startswith('/api/alarms') or
                    model != 'lite2' and path == '/api/footprint/reset' or
                    model not in ('lite2', 'mini2') and path == '/api/backup/restore' or
                    model == 'mini2' and path.startswith('/api/privacy'))
        if disabled:
            if not self._guard(path):
                self._send(400, {'error': 'Эта операция загрузки/восстановления не поддерживается профилем ' + model})
            return
        original_post(self)

    s.Handler.do_GET, s.Handler.do_POST = do_get, do_post
    if model == 'lite2':
        return  # No changes to the original Lite 2 hardware, boot hook, YAM or alarm code.

    P = s.Panel
    original_shell = s.Adb.shell

    def shell(self, command, timeout=15, check=True):
        # Older adbd can print a root-shell banner and lose the remote exit status.
        marker = '__PANEL_' + os.urandom(8).hex() + '__'
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

    def unsupported(self=None, *args, **kwargs):
        raise s.AdbError('Функция не проверена для профиля ' + model)

    if not profile['clock']:
        for method in ('clock', 'display_set', 'play_clock', 'clock_frames'):
            setattr(P, method, unsupported)
    # Never connect the Light 2 rootkeeper hook to a different model.
    P._ensure_hook = unsupported
    for method in ('yam_install_daemon', 'yam_update_daemon', 'yam_autostart'):
        if hasattr(P, method):
            setattr(P, method, unsupported)
    P.boot_status = lambda self: dict(supported=False, custom=False, mounted=False, sound_supported=False,
                                    sound_custom=False, sound_mounted=False, hook=False, no_boot_sound=True, always=False)

    if model == 'mini2':
        _install_mini2(s, profile)
        return  # Mini 2 has its own sysfs layout; never inherit another model's YAM or framebuffer paths.

    # Existing community YAM installations on the new profiles use this layout.
    old = s.YAM_DIR
    for name, value in list(vars(s).items()):
        if name.startswith('YAM') and isinstance(value, str):
            setattr(s, name, value.replace(old, '/data/local/yam'))

    def patterns(self):
        if model != 'mini3pro':
            return {'ring': [], 'clock': []}
        out, _, _ = self.adb.shell(f'find {s.QUASAR}/ledpatterns/clock -maxdepth 1 -type f 2>/dev/null; true')
        return {'ring': [], 'clock': [Path(line).name for line in out.splitlines()]}
    P.patterns = patterns

    def play_pattern(self, kind, name, seconds):
        if kind != 'clock' or model != 'mini3pro':
            return unsupported()
        if name not in self.patterns()['clock']:
            raise s.AdbError('Нет такой анимации')
        return self.play_clock(s.decode_clock_pattern(name, self.adb.read_bytes(f'{s.QUASAR}/ledpatterns/clock/{name}')), seconds)
    P.play_pattern = play_pattern

    def leds(self):
        if model == 'mini3':
            return {'v': [0] * 36, 'ring': [[0, 0, 0]] * 3}
        if model == 'station2':
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
        if model == 'mini3pro':
            self.adb.shell(mini3pro_ring_script(rgb))
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
        if model == 'mini3':
            return {'ok': True}
        if model == 'mini3pro':
            command = mini3pro_ring_script()
        else:
            command = 'if [ -s /tmp/webpanel_ring.before ]; then cat /tmp/webpanel_ring.before > /dev/fb0; rm -f /tmp/webpanel_ring.before; fi'
        self.adb.shell(command)
        return {'ok': True, 'leds': self.clock_state()['ring']}
    P.ring_reset = reset_ring
    original_stop = P.stop_pattern
    def stop(self, kind=None):
        if kind in (None, 'ring'):
            self.ring_reset()
        if kind in (None, 'clock') and profile['clock']:
            original_stop(self, 'clock')
        return {'ok': True}
    P.stop_pattern = stop

    if model in ("station2", "mini3pro"):
        try:
            import managed_yam
        except ImportError:     # архив обновлён кодом 0.1.3, а он новые модули не копирует
            print('Нет managed_yam.py — обновление yamd из панели для этой модели недоступно')
        else:
            managed_yam.install(s)


# Mini 2: separate sysfs clock/ring topology; no boot hooks or YAM installation.
MINI2_CLOCK_DIGITS = [f'{part}{position}' for position in range(1, 5) for part in 'ABCDEFG']
# The firmware frameBuilder uses 32 channels; PLUS3/4 exist in sysfs but not in .binscled.
MINI2_CLOCK_PATTERN = MINI2_CLOCK_DIGITS + ['PLUS1', 'PLUS2', 'MINUS', 'DEGREE']
MINI2_CLOCK_SEGMENTS = MINI2_CLOCK_PATTERN + ['PLUS3', 'PLUS4']
MINI2_CLOCK_NAMES = ['mini2:white:clock' + name for name in MINI2_CLOCK_SEGMENTS]
MINI2_RING_NAMES = [f'mini2:{color}:ring{i}' for i in range(1, 7) for color in ('red', 'green', 'blue')]
MINI2_STATE_DIR = '/tmp/webpanel_mini2'
MINI2_LEASE_SECONDS = 30


def mini2_brightness(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 255 or int(value) != value:
        raise ValueError('Яркость должна быть целым числом от 0 до 255')
    return int(value)


def mini2_segments_from_legacy(values):
    """Keep the existing 36-channel API; refuse dots that Mini 2 does not have."""
    if not isinstance(values, list) or len(values) != 36:
        raise ValueError('В кадре должно быть 36 значений')
    values = [mini2_brightness(v) for v in values]
    if any(values[i * 8 + 7] for i in range(4)):
        raise ValueError('На Мини 2 нет отдельных точек H возле цифр')
    result = {f'{part}{i + 1}': values[i * 8 + j] for i in range(4) for j, part in enumerate('ABCDEFG')}
    result.update(zip(('DEGREE', 'PLUS1', 'PLUS2', 'MINUS'), values[32:]))
    return result


def mini2_legacy_from_segments(segments):
    result = [segments.get(f'{part}{position}', 0) for position in range(1, 5) for part in 'ABCDEFGH']
    return result + [segments.get(name, 0) for name in ('DEGREE', 'PLUS1', 'PLUS2', 'MINUS')]


def mini2_validate_segments(segments):
    if not isinstance(segments, dict) or set(segments) - set(MINI2_CLOCK_SEGMENTS):
        raise ValueError('Неизвестный сегмент дисплея Мини 2')
    return {name: mini2_brightness(segments.get(name, 0)) for name in MINI2_CLOCK_SEGMENTS}


def mini2_decode_clock(name, blob, decompress=None):
    if name.endswith('.zst'):
        if decompress is None:
            raise ValueError('Не задан декодер zstd')
        blob = decompress(blob)
    if '.binscled' not in name or not blob or len(blob) % 35:
        raise ValueError('Неверный формат 32-канальной анимации Мини 2')
    frames = []
    for offset in range(0, len(blob), 35):
        row = blob[offset:offset + 35]
        duration = int.from_bytes(row[-2:], 'big')
        if row[0] not in (0, 1) or not duration:
            raise ValueError('Неверный кадр анимации Мини 2')
        segments = dict(zip(MINI2_CLOCK_PATTERN, row[1:33]))
        frames.append(dict(flag=row[0], segments=segments, v=mini2_legacy_from_segments(segments), ms=duration))
    return frames


def mini2_decode_ring_dump(text):
    """Read led_util --dump-raw; let the firmware expand gradients, smoothing and gamma."""
    frames, loop_at = [], None
    for line in text.splitlines():
        # The utility also writes timestamped Quasar logs; never return these to the browser.
        if re.match(r'^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+\] \[', line):
            continue
        parts = line.split()
        if not parts:
            continue
        if parts == ['loop']:
            if loop_at is not None:
                raise ValueError('Повторная метка цикла в анимации Мини 2')
            loop_at = len(frames)
            continue
        # Native pixels are RRGGBB00, followed by the frame duration in milliseconds.
        if len(parts) != 7 or any(not re.fullmatch('[0-9a-fA-F]{6}00', token) for token in parts[:6]):
            raise ValueError('Неверный дамп шести RGB-зон Мини 2')
        if not parts[-1].isdigit():
            raise ValueError('Некорректная длительность кадра')
        ms = int(parts[-1])
        if not 1 <= ms <= 65535:
            raise ValueError('Некорректная длительность кадра')
        frames.append(dict(c=[[int(token[i:i + 2], 16) for i in (0, 2, 4)] for token in parts[:6]], ms=ms))
        if len(frames) > 4096:
            raise ValueError('Слишком много кадров в анимации Мини 2')
    if not frames:
        raise ValueError('В анимации нет кадров')
    if loop_at is not None and loop_at >= len(frames):
        raise ValueError('После метки цикла нет кадров')
    return dict(frames=frames, loop=loop_at is not None, loop_at=loop_at or 0)


class Mini2Controller:
    def __init__(self, server, adb, leds='/sys/class/leds', state=MINI2_STATE_DIR):
        self.s, self.adb, self.leds, self.state = server, adb, leds, state
        self.lock = threading.RLock()
        self.jobs = set()
        self.closed = threading.Event()
        self.heartbeat = None
        self.maxima = None

    def read(self, writable=False):
        names = MINI2_CLOCK_NAMES + MINI2_RING_NAMES
        root = shlex.quote(self.leds)
        write_check = 'test -w "$D/$n/brightness"; test -w "$D/$n/trigger"; ' if writable else ''
        command = ('set -e; D=' + root + '; for n in ' + ' '.join(names) + '; do ' + write_check +
                   'test -r "$D/$n/max_brightness"; test -r "$D/$n/brightness"; '
                   'printf "%s|%s|%s|%s\\n" "$n" "$(cat "$D/$n/max_brightness")" '
                   '"$(cat "$D/$n/brightness")" "$(cat "$D/$n/trigger")"; done')
        rows = {}
        for line in self.adb.shell(command)[0].splitlines():
            fields = line.split('|')
            if len(fields) != 4 or fields[0] not in names:
                raise self.s.AdbError('Неполное состояние светодиодов Мини 2')
            name, maximum, value, triggers = fields
            trigger = re.search(r'\[([^]]+)\]', triggers)
            if not maximum.isdigit() or not value.isdigit() or not trigger or name in rows:
                raise self.s.AdbError('Некорректное состояние светодиода Мини 2')
            maximum, value = int(maximum), int(value)
            available = triggers.replace('[', '').replace(']', '').split()
            if not 0 < maximum <= 65535 or not 0 <= value <= maximum or 'none' not in available:
                raise self.s.AdbError('Неподдерживаемый LED-интерфейс Мини 2')
            rows[name] = dict(max=maximum, value=value, trigger=trigger.group(1))
        if set(rows) != set(names):
            raise self.s.AdbError('Не найдены все светодиоды Мини 2')
        self.maxima = {name: row['max'] for name, row in rows.items()}
        return rows

    def preflight(self):
        if self.s.detect_model(self.adb)[0] != 'mini2':
            raise self.s.AdbError('Аппаратные операции разрешены только для подтверждённой Мини 2')
        return self.read(writable=True)

    def status(self):
        if self.maxima is None:
            self.read()
        names = MINI2_CLOCK_NAMES + MINI2_RING_NAMES
        # Live polling needs one cat, not a separate max/brightness/trigger process per LED.
        output = self.adb.shell('cat ' + ' '.join(shlex.quote(self.leds + '/' + name + '/brightness') for name in names))[0]
        tokens = output.split()
        if len(tokens) != len(names) or any(not token.isdigit() for token in tokens):
            self.maxima = None
            raise self.s.AdbError('Неполное состояние светодиодов Мини 2')
        raw = dict(zip(names, map(int, tokens)))
        if any(raw[name] > self.maxima[name] for name in names):
            self.maxima = None
            raise self.s.AdbError('Некорректная яркость светодиода Мини 2')
        value = lambda name: round(raw[name] * 255 / self.maxima[name])
        segments = {name: value('mini2:white:clock' + name) for name in MINI2_CLOCK_SEGMENTS}
        ring = [[value(f'mini2:{color}:ring{i}') for color in ('red', 'green', 'blue')] for i in range(1, 7)]
        return dict(v=mini2_legacy_from_segments(segments), segments=segments, ring=ring, ring_all=ring)

    def _base(self, group):
        if group not in ('clock', 'ring'):
            raise ValueError('Неизвестная группа светодиодов')
        return self.state + '/' + group

    def stop(self, group):
        base = shlex.quote(self._base(group))
        # Validate the process command line, never kill a reused PID or somebody else's animation.
        self.adb.shell('B=' + base + '; if [ -s "$B.pid" ]; then p=$(cat "$B.pid"); '
                       'case "$p" in ""|*[!0-9]*) exit 1;; esac; '
                       'if tr "\\000" " " < /proc/$p/cmdline 2>/dev/null | grep -Fq "$B.sh"; then '
                       'kill "$p" 2>/dev/null || true; '
                       'for n in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do '
                       '[ -f "$B.pid" ] || break; usleep 100000; done; '
                       '[ ! -f "$B.pid" ] || { echo "Не удалось остановить анимацию" >&2; exit 1; }; fi; fi; true')
        self.jobs.discard(group)

    def _renew(self):
        while not self.closed.wait(5):
            with self.lock:
                groups = tuple(self.jobs)
            if not groups:
                continue
            try:
                if self.s.detect_model(self.adb)[0] != 'mini2':
                    continue
                for group in groups:
                    base = shlex.quote(self._base(group))
                    self.adb.shell(f'B={base}; if [ -f "$B.pid" ]; then echo $(( $(date +%s) + {MINI2_LEASE_SECONDS} )) > "$B.lease.tmp"; '
                                   'mv "$B.lease.tmp" "$B.lease"; fi', timeout=5)
            except (self.s.AdbError, OSError):
                pass  # Device-side expiry restores our output even when ADB/the host disappears.

    def start(self, group, frames, mode='once', seconds=0, repeat=False, dynamic=None, loop_at=0):
        if not frames or len(frames) > 4096:
            raise self.s.AdbError('Некорректное количество кадров')
        seconds = max(0, min(5999 if mode == 'timer' else 600, int(seconds)))
        names = MINI2_CLOCK_NAMES if group == 'clock' else MINI2_RING_NAMES
        if group not in ('clock', 'ring'):
            raise ValueError('Неизвестная группа светодиодов')
        # Validate the entire request before even stopping a previous valid operation.
        parsed = []
        for item in frames:
            if not isinstance(item, dict) or not isinstance(item.get('values'), dict) or set(item['values']) != set(names):
                raise self.s.AdbError('Неполный кадр Мини 2')
            parsed.append(dict(values={n: mini2_brightness(item['values'][n]) for n in names},
                               ms=max(10, min(65535, int(item.get('ms', 500))))))
        with self.lock:
            rows = self.preflight()  # Complete preflight before push, mkdir, stop or any LED write.
            self.stop(group)
            rows = self.read()      # The previous worker has restored its original state.
            native = []
            for frame in parsed:
                native.append(dict(values={n: round(mini2_brightness(frame['values'][n]) * rows[n]['max'] / 255) for n in names},
                                   ms=max(10, min(65535, int(frame.get('ms', 500))))))
            script = self.script(group, names, rows, native, mode, seconds, repeat, dynamic, loop_at)
            base = self._base(group)
            self.adb.shell('mkdir -p ' + shlex.quote(self.state) + '; rm -f ' + shlex.quote(base + '.ready'))
            with tempfile.TemporaryDirectory() as directory:
                file = Path(directory) / (group + '.sh')
                file.write_text(script)
                self.adb.push(file, base + '.sh')
            self.adb.shell(f'echo $(( $(date +%s) + {MINI2_LEASE_SECONDS} )) > {shlex.quote(base + ".lease")}; '
                           f'start-stop-daemon -S -b -m -p {shlex.quote(base + ".pid")} '
                           f'-x /bin/sh -- {shlex.quote(base + ".sh")}; '
                           f'B={shlex.quote(base)}; '
                           'for n in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do '
                           '[ -f "$B.ready" ] && break; usleep 100000; done; '
                           '[ -f "$B.ready" ] || { echo "Не удалось запустить управление LED" >&2; exit 1; }')
            self.jobs.add(group)
            if self.heartbeat is None:
                self.heartbeat = threading.Thread(target=self._renew, daemon=True, name='mini2-lease')
                self.heartbeat.start()

    def script(self, group, names, rows, frames, mode, seconds, repeat, dynamic, loop_at=0):
        base, root = shlex.quote(self._base(group)), shlex.quote(self.leds)
        lines = ['#!/bin/sh', 'set -eu', f'B={base}', f'D={root}',
                 'START=$(date +%s)', f'END=$(( START + {seconds} ))', 'SOUND_PID=', 'STOP=0',
                 'alive() { [ "$STOP" -eq 0 ] && [ -f "$B.lease" ] && [ "$(cat "$B.lease")" -gt "$(date +%s)" ] '
                 '&& { [ "$END" -eq "$START" ] || [ "$(date +%s)" -lt "$END" ]; }; }',
                 'pause() { remain=$1; while [ "$remain" -gt 0 ]; do alive || return 1; '
                 'step=$remain; [ "$step" -le 100000 ] || step=100000; usleep "$step"; remain=$((remain-step)); done; }',
                 # A concurrent stop must not terminate an expiry-triggered restoration halfway.
                 'restore() { trap "" TERM INT HUP; trap - 0;',
                 '  [ -z "$SOUND_PID" ] || kill "$SOUND_PID" 2>/dev/null || true']
        # A clock snapshot must advance across minute boundaries, rather than restoring stale time.
        clock = ''.join(''.join(p for p in 'ABCDEFG' if rows[f'mini2:white:clock{p}{i}']['value']) + '|' for i in range(1, 5))
        lookup = {''.join(sorted(v)): k for k, v in self.s.SEGMENTS.items() if k.isdigit()}
        digits = [lookup.get(''.join(sorted(v)), '') for v in clock.rstrip('|').split('|')]
        is_time = group == 'clock' and len(digits) == 4 and all(digits) and int(''.join(digits[:2])) < 24 and int(''.join(digits[2:])) < 60
        if is_time:
            total = int(''.join(digits[:2])) * 60 + int(''.join(digits[2:]))
            level = max(rows[n]['value'] for n in MINI2_CLOCK_NAMES)
            lines += [f'  t=$(( ({total} + $(date +%s) / 60 - START / 60) % 1440 )); '
                      'h=$(printf "%02d%02d" "$((t / 60))" "$((t % 60))")']
        for name in names:
            row = rows[name]
            lines += [f'  want={row["value"]}']
            if is_time and name[len('mini2:white:clock'):] in MINI2_CLOCK_DIGITS:
                segment, position = name[-2], int(name[-1])
                choices = '|'.join(k for k, parts in self.s.SEGMENTS.items() if k.isdigit() and segment in parts)
                lines += [f'  digit=$(printf "%s" "$h" | cut -c {position}); '
                          f'case "$digit" in {choices}) want={level};; *) want=0;; esac']
            lines += [f'  last=$(sed -n "s/^{re.escape(name)} //p" "$B.last" 2>/dev/null || true)',
                      f'  if [ -n "$last" ] && [ "$(cat "$D/{name}/brightness")" = "$last" ]; then',
                      f'    echo "$want" > "$D/{name}/brightness"',
                      f'    echo {shlex.quote(row["trigger"])} > "$D/{name}/trigger"', '  fi']
        lines += ['  rm -f "$B.pid" "$B.mode" "$B.last" "$B.last.tmp" "$B.lease" "$B.lease.tmp" "$B.ready" "$B.sh"',
                  '  exit 0', '}',
                  # Finish the current frame before restoring: its ledger must match actual writes.
                  'trap restore 0', 'trap \'STOP=1\' TERM INT HUP', f'echo {shlex.quote(mode)} > "$B.mode"']
        if mode == 'timer':
            lines += ['echo "timer $END" > "$B.mode"']
        # A setup error can occur before frame 0: record ownership before detaching triggers.
        lines += ["printf '%s\\n' " + ' '.join(shlex.quote(f'{n} {rows[n]["value"]}') for n in names) + ' > "$B.last"']
        for name in names:
            lines += [f'test -r "$D/{name}/brightness"', f'echo none > "$D/{name}/trigger"']
        previous = {}
        for index, frame in enumerate(frames):
            lines += [f'f{index}() {{', 'cat > "$B.last" <<\'FRAME\'']
            lines += [f'{name} {frame["values"][name]}' for name in names]
            lines += ['FRAME']
            for name, value in frame['values'].items():
                if previous.get(name) != value or repeat:
                    lines += [f'echo {value} > "$D/{name}/brightness"']
            lines += ['[ -f "$B.ready" ] || : > "$B.ready"', f'pause {frame["ms"] * 1000}', '}']
            previous = frame['values']
        if dynamic:
            lines.extend(self.dynamic_script(dynamic, rows))
        elif repeat:
            lines += [f'alive && f{i}' for i in range(loop_at)]
            lines += ['while alive; do']
            lines += [f'alive || break; f{i}' for i in range(loop_at, len(frames))]
            lines += ['done']
        else:
            lines += [f'alive && f{i}' for i in range(len(frames))]
            lines += ['while alive; do usleep 200000; done']
        lines += ['restore']
        return '\n'.join(lines) + '\n'

    def dynamic_script(self, mode, rows):
        level = mini2_brightness(mode['brightness'])
        kind = mode['mode']
        lines = ['while alive; do']
        if kind == 'date':
            lines += ['h=$(date +%d%m)']
        elif kind == 'temp':
            lines += ['t=$(cat /sys/class/thermal/thermal_zone0/temp); h=$(printf "%4d" "$((t / 1000))")']
        elif kind == 'timer':
            lines += ['left=$((END - $(date +%s))); h=$(printf "%02d%02d" "$((left / 60))" "$((left % 60))")']
        else:
            raise ValueError('Неизвестный режим дисплея')
        lines += [': > "$B.last.tmp"']
        # Generate only known Mini 2 paths. Input never becomes a shell fragment or sysfs name.
        for position in range(1, 5):
            choices = ' '.join(f'{shlex.quote(char)}) parts={parts or "empty"};;' for char, parts in self.s.SEGMENTS.items()
                                if char.isdigit() or char in (' ', '-'))
            lines += [f'digit=$(printf "%s" "$h" | cut -c {position}); case "$digit" in {choices} *) parts=empty;; esac']
            for segment in 'ABCDEFG':
                name = f'mini2:white:clock{segment}{position}'
                value = round(level * rows[name]['max'] / 255)
                lines += [f'case "$parts" in *{segment}*) v={value};; *) v=0;; esac',
                          f'echo "{name} $v" >> "$B.last.tmp"']
        for name in MINI2_CLOCK_SEGMENTS[28:]:
            full = 'mini2:white:clock' + name
            on = kind == 'temp' and name == 'DEGREE' or kind == 'date' and name == 'PLUS2' or kind == 'timer' and name in ('PLUS1', 'PLUS2')
            value = round(level * rows[full]['max'] / 255) if on else 0
            lines += [f'echo "{full} {value}" >> "$B.last.tmp"']
        lines += ['mv "$B.last.tmp" "$B.last"',
                  'while read n v; do echo "$v" > "$D/$n/brightness"; done < "$B.last"',
                  '[ -f "$B.ready" ] || : > "$B.ready"',
                  'pause 500000 || break', 'done']
        if kind == 'timer' and mode.get('sound'):
            lines += ['if [ "$(date +%s)" -ge "$END" ] && [ -f "$B.lease" ] '
                      '&& [ "$(cat "$B.lease")" -gt "$(date +%s)" ]; then',
                      'aplay -q ' + shlex.quote(mode['sound']) + ' >/dev/null 2>&1 & SOUND_PID=$!',
                      'wait "$SOUND_PID" || true; SOUND_PID=', 'fi']
        return lines

    def close(self):
        self.closed.set()
        with self.lock:
            for group in tuple(self.jobs):
                try:
                    self.stop(group)
                except (self.s.AdbError, OSError):
                    pass


def _install_mini2(s, profile):
    profile['clock_layout'] = dict(id='mini2', segments=MINI2_CLOCK_SEGMENTS, ring_zones=6)
    profile['light_note'] = 'Шесть RGB-зон Мини 2. Анимация Алисы может заменить выбранный цвет.'
    boot_status = s.Panel.boot_status
    s.Panel.boot_status = lambda self: dict(boot_status(self), editor_only=True)
    profile['yam_note'] = 'Для Мини 2 пока проверены статус и каталог YAM. Установка, обновление и управление пакетами требуют отдельной проверки с откатом.'
    original_yam_status = s.Panel.yam_status

    def yam_status(self):
        status = original_yam_status(self)
        # An existing external startup script is not the Lite 2 rootkeeper hook.
        external = self.adb.shell('test -r /etc/init.d/S99yam && '
                                  'grep -Fq "prefix=/data/quazar/yam" /etc/init.d/S99yam && '
                                  'echo external; true')[0].strip() == 'external'
        return dict(status, autostart=external and status['installed'], read_only=True,
                    read_only_reason=profile['yam_note'], autostart_external=external)

    s.Panel.yam_status = yam_status
    original_footprint = s.Panel.footprint
    s.Panel.footprint = lambda self: dict(original_footprint(self), backup_ui_only=True, reset_supported=False)
    original_backup = s.Panel.backup
    s.Panel.backup = lambda self, ui: original_backup(self, ui, device_files=False)

    def backup_restore(self, body):
        files, manifest = self._backup_contents(body)
        # Browser preferences are portable; Lite 2's device files and reset hook are not.
        if files:
            raise s.AdbError('Для Мини 2 можно восстановить только оформление и раскладку: копия содержит системные файлы панели')
        ui = manifest.get('ui') or {}
        if not isinstance(ui, dict):
            raise s.AdbError('Некорректные настройки браузера в резервной копии')
        ui = {k: str(v) for k, v in ui.items() if re.fullmatch(r'(ui|ov|ch|snd)\.[a-z.]{1,20}', k)}
        return dict(restored=['настройки браузера'] if ui else [], ui=ui, created=manifest.get('created'),
                    firmware=manifest.get('firmware'), footprint=self.footprint())

    s.Panel.backup_restore = backup_restore
    # Mini 2 uses linear Master percentages, not Lite 2's platformToAlsaMap.
    # Round halves up to match Quasar (30% -> 77); keep the existing UI's step of 5.
    s.level_to_alsa = lambda level: int(max(0.0, min(100.0, float(level))) * 255 / 100 + .5)
    s.alsa_to_level = lambda value: int(max(0, min(255, int(value or 0))) * 20 / 255 + .5) * 5
    s.TIMER_SOUND = MINI2_STATE_DIR + '/timer.wav'

    def control(panel):
        if not hasattr(panel, '_mini2'):
            panel._mini2 = Mini2Controller(s, panel.adb)
        return panel._mini2

    def frame(values, ms=500):
        return dict(values={'mini2:white:clock' + name: value for name, value in mini2_validate_segments(values).items()}, ms=ms)

    def clock(self, data):
        if 'segments' in data:
            segments = mini2_validate_segments(data['segments'])
        elif 'v' in data:
            segments = mini2_segments_from_legacy(data['v'])
        else:
            level = mini2_brightness(data.get('brightness', 160))
            segments, position = {}, 0
            for part in str(data.get('text', '')).upper():
                if part == ':':
                    segments.update(PLUS1=level, PLUS2=level)
                    continue
                if part == '.':
                    raise s.AdbError('На Мини 2 нет отдельных точек возле цифр')
                position += 1
                if position > 4:
                    break
                for segment in s.SEGMENTS.get(part, ''):
                    segments[f'{segment}{position}'] = level
            for key, name in (('dh', 'PLUS1'), ('dl', 'PLUS2'), ('dm', 'MINUS'), ('degree', 'DEGREE')):
                if data.get(key):
                    segments[name] = level
        control(self).start('clock', [frame(segments)])
        return dict(ok=True)

    def ring(self, data):
        rgb = [mini2_brightness(data.get(color, 0)) for color in ('red', 'green', 'blue')]
        values = dict(zip(MINI2_RING_NAMES, rgb * 6))
        control(self).start('ring', [dict(values=values, ms=20)])
        return dict(ok=True)

    def reset_ring(self):
        c = control(self)
        c.preflight()
        with c.lock:
            c.stop('ring')
        return dict(ok=True, leds=c.status()['ring'])

    def stop(self, kind=None):
        c = control(self)
        if kind not in (None, 'clock', 'ring'):
            raise s.AdbError('Неизвестная анимация')
        c.preflight()
        with c.lock:
            for group in (('clock', 'ring') if kind is None else (kind,)):
                c.stop(group)
        return dict(ok=True)

    def patterns(self):
        result = {}
        for kind, directory in s.PATTERNS.items():
            out = self.adb.shell('find ' + shlex.quote(directory) + ' -maxdepth 1 -type f; true')[0]
            result[kind] = sorted(Path(line).name for line in out.splitlines()
                                  if line.startswith(directory + '/') and (line.endswith('.led') if kind == 'ring' else '.binscled' in line))
        return result

    def clock_frames(self, name):
        if name not in patterns(self)['clock']:
            raise s.AdbError('Нет такой анимации')
        return mini2_decode_clock(name, self.adb.read_bytes(s.PATTERNS['clock'] + '/' + name), s.zstd_decompress)

    def ring_frames(self, name):
        if s.detect_model(self.adb)[0] != 'mini2':
            raise s.AdbError('Разбор анимаций разрешён только для подтверждённой Мини 2')
        if name not in patterns(self)['ring']:
            raise s.AdbError('Нет такой анимации')
        # This existing firmware utility only dumps frames in this mode; it does not draw them.
        path = shlex.quote(s.PATTERNS['ring'] + '/' + name)
        output = self.adb.shell(f'{s.QRUN}/led_util {path} --dump-raw 2>/dev/null', timeout=20)[0]
        return mini2_decode_ring_dump(s.strip_ansi(output))

    def play_clock(self, frames, seconds):
        if not isinstance(frames, list) or not frames or len(frames) > 4096 or any(not isinstance(f, dict) for f in frames):
            raise s.AdbError('Некорректный список кадров')
        parsed = [frame(mini2_segments_from_legacy(f['v']) if 'v' in f else f.get('segments'), f.get('ms', 100)) for f in frames]
        loop_at = next((i for i, f in enumerate(frames) if f.get('flag')), None)
        control(self).start('clock', parsed, mode='animation', seconds=seconds, repeat=loop_at is not None, loop_at=loop_at or 0)
        return dict(ok=True, frames=len(parsed))

    def play_pattern(self, kind, name, seconds):
        if kind == 'clock':
            return play_clock(self, clock_frames(self, name), seconds)
        if kind != 'ring':
            raise s.AdbError('Неизвестная анимация')
        animation = ring_frames(self, name)
        frames = [dict(values=dict(zip(MINI2_RING_NAMES, [v for rgb in f['c'] for v in rgb])), ms=f['ms']) for f in animation['frames']]
        control(self).start('ring', frames, mode='animation', seconds=seconds, repeat=animation['loop'],
                            loop_at=animation['loop_at'])
        return dict(ok=True)

    def display_set(self, data):
        mode = data.get('mode')
        level = mini2_brightness(data.get('brightness', 160))
        c = control(self)
        if mode == 'frame':
            values = mini2_validate_segments(data['segments']) if 'segments' in data else mini2_segments_from_legacy(data['v'])
            c.start('clock', [frame(values)], mode='frame', repeat=True)
        elif mode in ('date', 'temp', 'timer'):
            seconds = int(data.get('seconds', 0))
            if mode == 'timer' and not 1 <= seconds <= 5999:
                raise s.AdbError('Таймер должен быть от 1 секунды до 99:59')
            dynamic = dict(mode=mode, brightness=level)
            if mode == 'timer':
                c.preflight()
                if not self.ffmpeg:
                    raise s.AdbError('Для звука таймера нужен ffmpeg на сервере')
                with tempfile.TemporaryDirectory() as directory:
                    self._prepare_timer_sound(Path(directory))
                dynamic['sound'] = s.TIMER_SOUND
            c.start('clock', [frame({})], mode=mode, seconds=seconds if mode == 'timer' else 0, dynamic=dynamic)
        else:
            raise s.AdbError('Неизвестный режим дисплея')
        return dict(mode=mode, **(dict(left=seconds) if mode == 'timer' else {}))

    def prepare_timer_sound(self, directory):
        if 'have' in self.adb.shell('test -s ' + shlex.quote(s.TIMER_SOUND) + ' && echo have; true')[0]:
            return
        names = self.sounds()
        name = next((name for name in names if re.fullmatch(r'common/timer\.(wav|opus|ogg|mp3)', name)), None)
        if name is None:
            raise s.AdbError('Штатный звук таймера Мини 2 не найден')
        target = directory / 'timer.wav'
        result = s.subprocess.run([self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', 'pipe:0',
                                   '-ac', '2', '-ar', '48000', '-sample_fmt', 's16', '-y', str(target)],
                                  input=self.adb.read_bytes(s.SOUNDS_DIR + '/' + name), capture_output=True, timeout=60)
        if result.returncode:
            raise s.AdbError('Не удалось подготовить звук таймера: ' + result.stderr.decode('utf8', 'replace')[-300:])
        self.adb.shell('mkdir -p ' + shlex.quote(MINI2_STATE_DIR))
        self.adb.push(target, s.TIMER_SOUND)

    def display_status(self):
        base = shlex.quote(control(self)._base('clock'))
        out = self.adb.shell('B=' + base + '; if [ -f "$B.mode" ] && [ -f "$B.pid" ]; then '
                             'cat "$B.mode"; date +%s; fi; true')[0].split()
        if not out or out[0] in ('once', 'animation'):
            return dict(mode=None)
        if out[0] == 'timer' and len(out) == 3:
            return dict(mode='timer', left=max(0, int(out[1]) - int(out[2])))
        return dict(mode=out[0])

    def display_stop(self):
        stop(self, 'clock')
        return dict(mode=None)

    class LedStream:
        def __init__(self, adb):
            self.controller = Mini2Controller(s, adb)
            self.lock, self.at, self.cached = threading.Lock(), 0, dict(v=None)

        def get(self):
            with self.lock:
                if time.monotonic() - self.at > .4:
                    self.cached, self.at = self.controller.status(), time.monotonic()
                return self.cached

    s.Panel.clock = clock
    s.Panel.clock_state = lambda self: control(self).status()
    s.Panel.ring = ring
    s.Panel.ring_reset = reset_ring
    s.Panel.stop_pattern = stop
    s.Panel.patterns = patterns
    s.Panel.clock_frames = clock_frames
    s.Panel.ring_frames = ring_frames
    s.Panel.play_clock = play_clock
    s.Panel.play_pattern = play_pattern
    s.Panel.display_set = display_set
    s.Panel.display_status = display_status
    s.Panel.display_stop = display_stop
    s.Panel._prepare_timer_sound = prepare_timer_sound
    s.Panel.close_mini2 = lambda self: control(self).close()
    s.LedStream = LedStream
