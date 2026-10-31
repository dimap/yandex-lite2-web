"""Cucumber interfaces. No framebuffer format or clock segment map is assumed.

The sysfs names are documented by https://hackalice.me/devices/station-midi/.
Writes remain behind the profile's hardware flags until physical validation.
All shell generators are also exercised against a temporary, simulated sysfs.
"""
import re
import shlex
import os
import tempfile
import hashlib
import json
import threading
import time
import base64
from pathlib import Path


COLORS = ('red', 'green', 'blue')
COMPONENTS = {'ring': 24, 'logo': 6}
TRIAL_COLORS = {'red': (8, 0, 0), 'green': (0, 8, 0), 'blue': (0, 0, 8), 'yellow': (8, 8, 0)}
CLOCK_TRIAL_BRIGHTNESS = 255  # Normalized 100%; scaled to the selected node's verified maximum.
# Observed screen locations, never an assumed mapping of Cucumber channel names.
CLOCK_POSITIONS = {
    f'd{digit}-{letter}': f'Цифра {digit} слева: {label}'
    for digit in range(1, 5)
    for letter, label in zip('ABCDEFGH', ('верхняя полоса', 'верхняя правая полоса',
        'нижняя правая полоса', 'нижняя полоса', 'нижняя левая полоса',
        'верхняя левая полоса', 'средняя полоса', 'точка справа внизу'))
}
CLOCK_POSITIONS.update({'colon-top':'Двоеточие: верхняя точка',
                        'colon-bottom':'Двоеточие: нижняя точка',
                        'minus':'Средний дефис', 'degree':'Знак градуса'})
IDENTITY_CHECK = r"""test "$(id -u)" = 0 || exit 1
case " $(tr '\000' ' ' < /proc/device-tree/compatible) " in
  *" yandex,cucumber "*|*" yandex,cucumber-dvt2 "*) ;;
  *) echo 'Expected Cucumber device tree' >&2; exit 1;;
esac"""

LED_READ_SCRIPT = r"""printf 'compatible\t%s\n' "$(tr '\000' ' ' < /proc/device-tree/compatible)"
for d in /sys/class/leds/cucumber:*; do
  [ -r "$d/brightness" ] || continue
  n=${d##*/}
  printf 'led\t%s\t%s\t%s\t%s\n' "$n" "$(cat "$d/brightness")" \
    "$(cat "$d/max_brightness")" "$(cat "$d/trigger" 2>/dev/null)"
done
"""
# Deliberately excludes configuration, logs, device IDs and network identifiers.
# No executable from the speaker's application is started, even with --version.
READ_SCRIPT = r"""set -e
printf 'uid\t%s\n' "$(id -u)"
printf 'model\t%s\n' "$(tr '\000' ' ' < /proc/device-tree/model)"
printf 'kernel\t%s\n' "$(uname -r)"
printf 'arch\t%s\n' "$(uname -m)"
printf 'firmware\t%s\n' "$(cat /system/vendor/quasar/version_full 2>/dev/null)"
""" + LED_READ_SCRIPT + r"""
for d in /sys/class/graphics/fb0 /sys/class/graphics/fb1; do
  [ -d "$d" ] || continue
  printf 'fb\t%s\t%s\t%s\t%s\n' "${d##*/}" "$(cat "$d/name")" \
    "$(cat "$d/virtual_size")" "$(cat "$d/bits_per_pixel")"
done
for t in amixer aplay mount start-stop-daemon bluetoothctl; do
  printf 'tool\t%s\t%s\n' "$t" "$(command -v "$t" || true)"
done
printf 'quasar\t%s\n' "$(pidof quasar_launcher2 || true)"
printf 'maind\t%s\n' "$(pidof maind || true)"
for p in /data /tmp; do df -Pk "$p"; done
grep ' / ' /proc/mounts
"""


def names_for(component):
    if component not in COMPONENTS:
        raise ValueError('unknown Midi LED component')
    return [f'cucumber:{c}:{component}{i}' for i in range(COMPONENTS[component]) for c in COLORS]


def parse_probe(output):
    """Strict metadata parsing; an incomplete/foreign inventory never enables writes."""
    result = {'leds': {}, 'framebuffers': {}, 'tools': {}}
    scalars = {'uid', 'compatible', 'model', 'kernel', 'arch', 'firmware', 'quasar', 'maind'}
    for line in output.splitlines():
        fields = line.split('\t')
        if fields[0] in scalars and len(fields) == 2:
            if fields[0] in result:
                raise ValueError('duplicate probe field')
            result[fields[0]] = fields[1]
        elif fields[0] == 'led':
            if len(fields) != 5:
                raise ValueError('incomplete LED metadata')
            _, name, value, maximum, triggers = fields
            if not re.fullmatch(r'cucumber:(?:red|green|blue|white|logo|ringA|ringB|sync):[a-zA-Z0-9-]+', name):
                raise ValueError('unexpected LED name')
            if name in result['leds'] or not value.isdecimal() or not maximum.isdecimal():
                raise ValueError('invalid LED metadata')
            value, maximum = int(value), int(maximum)
            if not 0 <= value <= maximum <= 65535:
                raise ValueError('invalid LED brightness')
            active = re.findall(r'\[([a-zA-Z0-9_-]+)\]', triggers)
            result['leds'][name] = {'value': value, 'max': maximum,
                                    'trigger': active[0] if len(active) == 1 else None}
        elif fields[0] == 'fb' and len(fields) == 5:
            result['framebuffers'][fields[1]] = dict(zip(('name', 'size', 'bpp'), fields[2:]))
        elif fields[0] == 'tool' and len(fields) == 3:
            result['tools'][fields[1]] = fields[2] or None
    compatible = result.get('compatible', '').split()
    if not {'yandex,cucumber', 'yandex,cucumber-dvt2'}.intersection(compatible):
        raise ValueError('device is not Cucumber')
    result['root'] = result['uid'] == '0' if result.get('uid', '').isdecimal() else None
    result['components'] = {
        c: all(n in result['leds'] and result['leds'][n]['max'] > 0 and result['leds'][n]['trigger'] is not None for n in names_for(c))
        for c in COMPONENTS}
    result['clock_segments'] = sorted(n for n in result['leds'] if n.startswith('cucumber:white:clock-'))
    result['clock_mapping_verified'] = False
    return result


def led_script(component, rgb=None, *, leds='/sys/class/leds', state_dir='/tmp', identity=IDENTITY_CHECK,
               recover_stale=False):
    """Set/restore one component, preserving every channel and active trigger.

    rgb=None only restores a saved snapshot. All nodes, limits and snapshot rows
    are validated before the first LED write. A failed set attempts rollback and
    keeps the original snapshot when rollback cannot complete. There is no eval,
    no guessed trigger, no framebuffer access and no process signalling.
    """
    names = names_for(component)
    if recover_stale and rgb is not None:
        raise ValueError('stale-lock recovery is only available for restore')
    if rgb is not None:
        if not isinstance(rgb, (list, tuple)) or len(rgb) != 3 or any(type(v) is not int or not 0 <= v <= 255 for v in rgb):
            raise ValueError('RGB must contain three integers in 0..255')
    snapshot = state_dir.rstrip('/') + '/webpanel_midi_' + component + '.before'
    lines = ['set -eu', identity, 'umask 077',
             'D=' + shlex.quote(leds), 'S=' + shlex.quote(snapshot),
             'N=' + shlex.quote(' '.join(names)),
             'K="$S.lock"',
             'if ! mkdir "$K" 2>/dev/null; then']
    if recover_stale:
        lines += ['  [ -d "$K" ] && [ ! -L "$K" ]',
                  '  [ -f "$K/owner" ] && [ ! -L "$K/owner" ]',
                  '  owner=$(cat "$K/owner")',
                  '  case "$owner" in \'\'|*[!0-9]*) exit 1;; esac',
                  '  [ "$owner" -gt 0 ]; if kill -0 "$owner" 2>/dev/null; then exit 1; fi',
                  '  rm -f "$K/snapshot" "$K/owner"; rmdir "$K"; mkdir "$K"']
    else:
        lines += ['  echo "Midi component is already locked" >&2; exit 1']
    lines += ['fi',
             'T="$K/snapshot"',
             'trap \'rm -f "$T" "$K/owner"; rmdir "$K"\' 0',
             "trap 'exit 1' 1 2 15",
             'printf "%s\\n" "$$" > "$K/owner"',
             'num() { case "$1" in \'\'|*[!0-9]*) return 1;; esac; [ "${#1}" -le 5 ]; }',
             'valid() {',
             '  F=$1',
             '  [ -f "$1" ] && [ ! -L "$1" ] && [ "$(wc -l < "$1")" -eq ' + str(len(names)) + ' ] || return 1',
             '  for n in $N; do',
             '    row=$(awk -v n="$n" \'$1 == n && NF == 4 {print $2, $3, $4}\' "$F")',
             '    set -- $row; [ "$#" -eq 3 ] || return 1',
             '    num "$1" && num "$2" && [ "$1" -le "$2" ] && [ "$2" -gt 0 ] && [ "$2" -le 65535 ] || return 1',
             '    [ -r "$D/$n/max_brightness" ] && [ -w "$D/$n/brightness" ] && [ -w "$D/$n/trigger" ] || return 1',
             '    [ "$(cat "$D/$n/max_brightness")" = "$2" ] || return 1',
             '    case "$3" in \'\'|*[!a-zA-Z0-9_-]*) return 1;; esac',
             '    case " $(cat "$D/$n/trigger") " in *" $3 "*|*" [$3] "*) ;; *) return 1;; esac',
             '    case " $(cat "$D/$n/trigger") " in *" none "*|*" [none] "*) ;; *) return 1;; esac',
             '  done',
             '}',
             'restore() {',
             '  restored=0',
             '  while read -r n v m t; do',
             '    printf "%s\\n" "$v" > "$D/$n/brightness" || restored=1',
             '    printf "%s\\n" "$t" > "$D/$n/trigger" || restored=1',
             '  done < "$S"',
             '  return "$restored"',
             '}']
    if rgb is None:
        lines += ['if [ -e "$S" ]; then', 'valid "$S"', 'restore', 'rm -f "$S"', 'fi']
    else:
        lines += ['if [ ! -e "$S" ] && [ ! -L "$S" ]; then',
                  '  for n in $N; do',
                  '    printf "%s %s %s %s\\n" "$n" "$(cat "$D/$n/brightness")" "$(cat "$D/$n/max_brightness")" "$(sed -n \'s/.*\\[\\([^]]*\\)\\].*/\\1/p\' "$D/$n/trigger")"',
                  '  done > "$T"', '  valid "$T"', '  mv "$T" "$S"', 'fi', 'valid "$S"']
        # RGB values use 0..255 at the API and each channel's actual range in sysfs.
        lines += ['apply() {']
        for i in range(COMPONENTS[component]):
            for color, value in zip(COLORS, rgb):
                name = f'cucumber:{color}:{component}{i}'
                lines.append(f'  m=$(cat "$D/{name}/max_brightness")')
                lines.append(f'  printf "none\\n" > "$D/{name}/trigger" || return 1')
                lines.append(f'  printf "%s\\n" "$((({value} * m + 127) / 255))" > "$D/{name}/brightness" || return 1')
        lines += ['}', "trap 'restore || true; exit 1' 1 2 15",
                  'if ! apply; then restore || true; exit 1; fi']
    # A restore may itself run inside its caller's EXIT trap. Some shells do
    # not run nested EXIT traps then, so successful cleanup must be explicit.
    lines += ['rm -f "$T" "$K/owner"; rmdir "$K"', 'trap - 0']
    return '\n'.join(lines) + '\n'


def clock_segment_test_script(name, brightness=8, seconds=1, *, off_seconds=0, leds='/sys/class/leds',
                              state_dir='/tmp', identity=IDENTITY_CHECK):
    """Bounded, self-restoring single-segment probe for mapping the actual clock.

    This is a diagnostic generator, not a text renderer or a binscled codec.
    Its execution requires the owner's approval, just like any other LED write.
    """
    if not isinstance(name, str) or not re.fullmatch(r'cucumber:white:clock-[a-z][a-z0-9-]*', name):
        raise ValueError('expected a Cucumber clock segment')
    if type(brightness) is not int or not 1 <= brightness <= 255:
        raise ValueError('test brightness must be in 1..255')
    if type(seconds) is not int or not 1 <= seconds <= 3:
        raise ValueError('test duration must be in 1..3 seconds')
    if type(off_seconds) is not int or not 0 <= off_seconds <= 1 or seconds + off_seconds > 3:
        raise ValueError('dark phase must be 0..1 seconds; total trial must not exceed 3 seconds')
    lines = [
        'set -eu', identity, 'umask 077',
        'D=' + shlex.quote(leds.rstrip('/') + '/' + name),
        'K=' + shlex.quote(state_dir.rstrip('/') + '/webpanel_midi_clock_test.lock'),
        'mkdir "$K" || exit 1', 'trap \'rm -f "$K/owner"; rmdir "$K"\' 0', "trap 'exit 1' 1 2 15",
        'printf "%s\\n" "$$" > "$K/owner"',
        'test -r "$D/brightness"; test -w "$D/brightness"; test -w "$D/trigger"',
        'v=$(cat "$D/brightness"); m=$(cat "$D/max_brightness")',
        't=$(sed -n \'s/.*\\[\\([^]]*\\)\\].*/\\1/p\' "$D/trigger")',
        'case "$v:$m" in *[!0-9:]*|:*|*:) exit 1;; esac',
        '[ "$v" -le "$m" ]; [ "$m" -gt 0 ]; [ "$m" -le 65535 ]',
        'case "$t" in \'\'|*[!a-zA-Z0-9_-]*) exit 1;; esac',
        'case " $(cat "$D/trigger") " in *" none "*|*" [none] "*) ;; *) exit 1;; esac',
        'restore() { err=0; printf "%s\\n" "$v" > "$D/brightness" || err=1; printf "%s\\n" "$t" > "$D/trigger" || err=1; return "$err"; }',
        'printf "%s %s %s %s\\n" ' + shlex.quote(name) + ' "$v" "$m" "$t" > "$K/state"',
        'trap \'restore && rm -f "$K/state" "$K/owner" && rmdir "$K"\' 0',
        f'b=$(({brightness} * m / 255)); [ "$b" -gt 0 ] || b=1',
        'printf "none\\n" > "$D/trigger"',
    ]
    if off_seconds:
        lines += ['printf "0\\n" > "$D/brightness"', f'sleep {off_seconds}']
    lines += ['printf "%s\\n" "$b" > "$D/brightness"', f'sleep {seconds}']
    return '\n'.join(lines) + '\n'


def clock_segment_restore_script(name, *, leds='/sys/class/leds', state_dir='/tmp', identity=IDENTITY_CHECK):
    """Explicit recovery of the one-segment test, using its persisted snapshot."""
    if not isinstance(name, str) or not re.fullmatch(r'cucumber:white:clock-[a-z][a-z0-9-]*', name):
        raise ValueError('expected a Cucumber clock segment')
    return '\n'.join([
        'set -eu', identity,
        'D=' + shlex.quote(leds.rstrip('/') + '/' + name),
        'K=' + shlex.quote(state_dir.rstrip('/') + '/webpanel_midi_clock_test.lock'),
        '[ -e "$K/state" ] || exit 0',
        '[ -d "$K" ] && [ ! -L "$K" ]; [ -f "$K/owner" ] && [ ! -L "$K/owner" ]',
        'owner=$(cat "$K/owner"); case "$owner" in \'\'|*[!0-9]*) exit 1;; esac',
        '[ "$owner" -gt 0 ]; if kill -0 "$owner" 2>/dev/null; then exit 1; fi',
        '[ -f "$K/state" ] && [ ! -L "$K/state" ] && [ "$(wc -l < "$K/state")" -eq 1 ]',
        'set -- $(cat "$K/state"); [ "$#" -eq 4 ]',
        '[ "$1" = ' + shlex.quote(name) + ' ]',
        'v=$2; m=$3; t=$4',
        'case "$v:$m" in *[!0-9:]*|:*|*:) exit 1;; esac',
        '[ "$v" -le "$m" ]; [ "$m" -gt 0 ]; [ "$m" -le 65535 ]',
        '[ "$(cat "$D/max_brightness")" = "$m" ]',
        'case "$t" in \'\'|*[!a-zA-Z0-9_-]*) exit 1;; esac',
        'case " $(cat "$D/trigger") " in *" $t "*|*" [$t] "*) ;; *) exit 1;; esac',
        'test -w "$D/brightness"; test -w "$D/trigger"',
        'printf "%s\\n" "$v" > "$D/brightness"',
        'printf "%s\\n" "$t" > "$D/trigger"',
        'rm -f "$K/state" "$K/owner"; rmdir "$K"',
    ]) + '\n'


def _clock_group_script(probe, frames=(), *, recover_stale=False,
                        leds='/sys/class/leds', state_dir='/tmp', identity=IDENTITY_CHECK):
    """Bounded white-channel frames sharing one complete snapshot and recovery."""
    names = probe.get('clock_segments', [])
    if (not names or len(names) > 64 or len(set(names)) != len(names) or
            any(not isinstance(n, str) or not re.fullmatch(r'cucumber:white:clock-[a-z][a-z0-9-]*', n) for n in names)):
        raise ValueError('a complete real clock catalog is required')
    if not recover_stale and (not frames or len(frames) > 10 or
            any(type(seconds) is not int or not 1 <= seconds <= 10 or set(levels) != set(names) or
                any(type(p) is not int or not 0 <= p <= 100 for p in levels.values())
                for seconds, levels in frames) or sum(seconds for seconds, _ in frames) > 10):
        raise ValueError('complete clock frames of at most ten seconds are required')
    specs = []
    for name in names:
        maximum = probe['leds'].get(name, {}).get('max')
        if type(maximum) is not int or not 0 < maximum <= 65535:
            raise ValueError('invalid clock maximum')
        specs.append(f'{name} {maximum}')
    cases = '|'.join(f'{n}:{probe["leds"][n]["max"]}' for n in names)
    lines = ['set -eu', identity, 'umask 077',
        'D=' + shlex.quote(leds.rstrip('/')),
        'K=' + shlex.quote(state_dir.rstrip('/') + '/webpanel_midi_clock_photo.lock'),
        'validate() {',
        '  [ -f "$K/state" ] && [ ! -L "$K/state" ]',
        f'  [ "$(wc -l < "$K/state")" -eq {len(names)} ] || return 1',
        '  seen=" "',
        '  while read -r n v m t extra; do',
        '    [ -z "$extra" ] || return 1',
        f'    case "$n:$m" in {cases}) ;; *) return 1;; esac',
        '    case "$seen" in *" $n "*) return 1;; esac; seen="$seen$n "',
        '    case "$v" in \'\'|*[!0-9]*) return 1;; esac; [ "$v" -le "$m" ] || return 1',
        '    case "$t" in \'\'|*[!a-zA-Z0-9_-]*) return 1;; esac',
        '    [ "$(cat "$D/$n/max_brightness")" = "$m" ] || return 1',
        '    test -w "$D/$n/brightness" && test -w "$D/$n/trigger" || return 1',
        '    case " $(cat "$D/$n/trigger") " in *" $t "*|*" [$t] "*) ;; *) return 1;; esac',
        '  done < "$K/state"', '}',
        'restore() {', '  validate || return 1', '  err=0',
        '  while read -r n v m t; do',
        '    printf "%s\\n" "$v" > "$D/$n/brightness" || err=1',
        '    printf "%s\\n" "$t" > "$D/$n/trigger" || err=1',
        '  done < "$K/state"', '  return "$err"', '}',
    ]
    if recover_stale:
        lines += ['[ -e "$K/state" ] || exit 0',
            '[ -d "$K" ] && [ ! -L "$K" ]; [ -f "$K/owner" ] && [ ! -L "$K/owner" ]',
            'owner=$(cat "$K/owner"); case "$owner" in \'\'|*[!0-9]*) exit 1;; esac',
            '[ "$owner" -gt 0 ]; if kill -0 "$owner" 2>/dev/null; then exit 1; fi',
            'restore', 'rm -f "$K/state" "$K/owner"; rmdir "$K"']
    else:
        lines += ['mkdir "$K" || exit 1',
            'trap \'rm -f "$K/state.tmp" "$K/owner"; rmdir "$K"\' 0', "trap 'exit 1' 1 2 15",
            'printf "%s\\n" "$$" > "$K/owner"',
            'while read -r n m; do',
            '  [ "$(cat "$D/$n/max_brightness")" = "$m" ]',
            '  v=$(cat "$D/$n/brightness"); t=$(sed -n \'s/.*\\[\\([^]]*\\)\\].*/\\1/p\' "$D/$n/trigger")',
            '  printf "%s %s %s %s\\n" "$n" "$v" "$m" "$t"',
            'done > "$K/state.tmp" <<\'CLOCK_PHOTO_NODES\'', *specs, 'CLOCK_PHOTO_NODES',
            'mv "$K/state.tmp" "$K/state"',
            'trap \'restore && rm -f "$K/state" "$K/owner" && rmdir "$K"\' 0',
            'validate', 'while read -r n v m t; do',
            '  printf "none\\n" > "$D/$n/trigger"',
            'done < "$K/state"']
        for index, (seconds, levels) in enumerate(frames):
            lines += ['while read -r n m p; do',
                '  printf "%s\\n" "$(((p * m + 50) / 100))" > "$D/$n/brightness"',
                f"done <<'CLOCK_FRAME_{index}'",
                *(f'{name} {probe["leds"][name]["max"]} {levels[name]}' for name in names),
                f'CLOCK_FRAME_{index}', f'sleep {seconds}']
    return '\n'.join(lines) + '\n'


def clock_photo_script(probe, dim_segment=None, *, recover_stale=False, **options):
    """Ten-second photo, optionally dimming one channel; restore all white LEDs."""
    if dim_segment is not None and dim_segment not in probe.get('clock_segments', []):
        raise ValueError('unknown photo segment')
    levels = {name: 10 if name == dim_segment else 60 for name in probe.get('clock_segments', [])}
    return _clock_group_script(probe, [(10, levels)], recover_stale=recover_stale, **options)


# Diagnostic output is bound to the owner's H10 map, documented in
# docs/support/midi-clock-map.factory-9.json. This does not enable normal clocks.
DISPLAY_TEST_FIRMWARE = '0.9.1.3.2572674166.20240919.factory-9'
DISPLAY_TEST_REFERENCE = '7372317ae821cce700f1963607a70187f648d0424569df6361a098f739b0ad3f'
DISPLAY_TEST_TEXTS = ('0123', '4567', '8901', '12:34')
DISPLAY_CHECKS = {
    'digits': (DISPLAY_TEST_TEXTS, (60, 60, 60, 60)),
    'digits_low': (('0000', '1111', '2222', '3333'), (60, 60, 60, 60)),
    'digits_high': (('4444', '5555', '6666', '7777'), (60, 60, 60, 60)),
    'degree': (('8888', '9999', '12:34°'), (60, 60, 60)),
    'brightness': (('12:34', '12:34', '12:34'), (20, 60, 100)),
}
DISPLAY_DIGITS = ('ABCDEF', 'BC', 'ABDEG', 'ABCDG', 'BCFG',
                  'ACDFG', 'ACDEFG', 'ABC', 'ABCDEFG', 'ABCDFG')


def clock_display_layout(report):
    """Require the complete owner-observed map on the same report and firmware."""
    prefix = 'cucumber:white:clock-'
    digit_nodes = {f'd{d}-{s.upper()}': f'{prefix}{s}{d}' for d in range(1,5) for s in 'abcdefg'}
    expected = set(digit_nodes.values()) | {prefix+'deg'}
    expected |= {f'{prefix}{s}{d}' for s in 'hlm' for d in (1,2)}
    expected |= {f'{prefix}{s}{d}' for s in 'pq' for d in range(1,6)}
    latest = {t.get('segment'): t for t in report.get('trials', []) if t.get('component') == 'clock'}
    if (report.get('firmware') != DISPLAY_TEST_FIRMWARE or
            len(report.get('clock_segments', [])) != 45 or set(report['clock_segments']) != expected or
            any(latest.get(n, {}).get('state') != 'mapped' or
                latest[n].get('restored') is not True or latest[n].get('observation') != 'located' or
                latest[n].get('returncode') != 0 for n in expected) or
            any(latest[n].get('position') != position for position, n in digit_nodes.items()) or
            latest[prefix+'deg'].get('position') != 'degree'):
        raise ValueError('Нужна полная подтверждённая карта H10 для этой прошивки')
    colon = []
    for suffix, x, y in (('p3', .4773, .3555), ('q3', .4854, .6928)):
        name = prefix+suffix
        if latest[name].get('photo_point') != {'x':x, 'y':y, 'reference':DISPLAY_TEST_REFERENCE}:
            raise ValueError('Положение точек двоеточия не подтверждено по принятому фото H10')
        colon.append(name)
    return digit_nodes, colon


def clock_display_frames(probe, layout, check='digits'):
    """Short, complete frames derived only from the owner's accepted map."""
    if not isinstance(check, str) or check not in DISPLAY_CHECKS:
        raise ValueError('Неизвестная проверка дисплея')
    digit_nodes, colon = layout
    frames = []
    texts, brightness = DISPLAY_CHECKS[check]
    for text, percent in zip(texts, brightness):
        active = {digit_nodes[f'd{d}-{s}'] for d, value in enumerate(text.replace(':','').replace('°',''), 1)
                  for s in DISPLAY_DIGITS[int(value)]}
        if ':' in text:
            active.update(colon)
        if '°' in text:
            active.add('cucumber:white:clock-deg')
        frames.append((2, {name: percent if name in active else 0 for name in probe['clock_segments']}))
    return frames


def clock_display_values(probe, report):
    """Read the accepted digits/colon/degree into the shared visual API.

    The per-digit dots and minus of other models have no accepted Cucumber
    equivalent. They stay dark; all 45 native readings remain in clock_raw.
    This read adapter does not grant any clock write capability.
    """
    digits, colon = clock_display_layout(report)
    if (probe.get('firmware') != DISPLAY_TEST_FIRMWARE or
            set(probe['clock_segments']) != set(report['clock_segments'])):
        raise ValueError('Дисплей или прошивка отличаются от принятой карты')
    values = [0] * 36
    mapping = {**{(d - 1) * 8 + i: digits[f'd{d}-{letter}']
                  for d in range(1, 5) for i, letter in enumerate('ABCDEFG')},
               32: 'cucumber:white:clock-deg', 33: colon[0], 34: colon[1]}
    for index, name in mapping.items():
        led = probe['leds'][name]
        if not led['max']:
            raise ValueError('Неизвестен диапазон яркости сегмента')
        values[index] = (led['value'] * 255 + led['max'] // 2) // led['max']
    return values


def display_controls_ready(report):
    latest = {t.get('display_check', 'digits'): t for t in report.get('trials', [])
              if t.get('component') == 'clock_display'}
    return all(latest.get(check, {}).get('state') == 'passed' and
               latest[check].get('restored') is True and
               latest[check].get('returncode', 0) == 0 and
               latest[check].get('observation') == 'matched' for check in DISPLAY_CHECKS)


def clock_text_frames(probe, layout, text, brightness):
    if (not isinstance(text, str) or not re.fullmatch(r'(?:[0-9 ]{4}|[0-9]{2}[:.][0-9]{2})°?', text)
            or not any(c in '0123456789' for c in text)):
        raise ValueError('Введите 1234, 12:34 или 30.10; для пустой позиции используйте пробел, можно добавить °')
    if type(brightness) is not int or not 20 <= brightness <= 100:
        raise ValueError('Яркость должна быть целым числом от 20 до 100%')
    digits, colon = layout
    active = {digits[f'd{d}-{s}'] for d, value in enumerate(text.replace(':','').replace('.','').replace('°',''),1)
              if value != ' ' for s in DISPLAY_DIGITS[int(value)]}
    if ':' in text:active.update(colon)
    if '.' in text:active.add(colon[1])
    if '°' in text:active.add('cucumber:white:clock-deg')
    return [(8,{name:brightness if name in active else 0 for name in probe['clock_segments']})]


def clock_timer_frames(probe, layout, seconds, brightness):
    """First owner check: eight one-second steps, then zero for two seconds.

    Keep the same ten-second restore envelope as the accepted photo tests.
    This visual check does not grant persistent modes or timer audio.
    """
    if type(seconds) is not int or seconds != 8:
        raise ValueError('Для первой проверки таймера нужен отсчёт 8 секунд')
    return [(1 if left else 2, clock_text_frames(probe, layout, f'00:{left:02d}', brightness)[0][1])
            for left in range(seconds, -1, -1)]


def rgb_trial_script(component, rgb=(8, 0, 0), seconds=2, *, leds='/sys/class/leds',
                     state_dir='/tmp', identity=IDENTITY_CHECK):
    """One brief trial, with an EXIT/signal restore and no persistent service.

    It refuses an existing manual-control snapshot instead of overwriting it.
    Power loss or SIGKILL cannot run a shell trap; snapshots remain available
    for an explicit restore while the device stays booted.
    """
    if type(seconds) is not int or not 1 <= seconds <= 3:
        raise ValueError('test duration must be in 1..3 seconds')
    if not isinstance(rgb, (tuple, list)) or len(rgb) != 3 or any(type(v) is not int or not 0 <= v <= 16 for v in rgb):
        raise ValueError('test RGB values must be in 0..16')
    options = dict(leds=leds, state_dir=state_dir, identity=identity)
    forward = led_script(component, rgb, **options)
    rollback = led_script(component, recover_stale=True, **options)
    snapshot = state_dir.rstrip('/') + '/webpanel_midi_' + component + '.before'
    return ('set -eu\nS=' + shlex.quote(snapshot) + '\n[ ! -e "$S" ] && [ ! -L "$S" ]\n'
            'rollback() (\n' + rollback + ')\ntrap rollback 0\ntrap \'exit 1\' 1 2 15\n(\n' +
            forward + f')\nsleep {seconds}\n')


def reviewed_trial_scripts(probe):
    """Prepare firmware-bound trials only from a complete read-only Midi audit.

    This returns source text, never executes it. Final owner confirmation is
    still required. Old unbound generators are useful for simulation/drafts.
    """
    if (not isinstance(probe, dict) or probe.get('root') is not True or probe.get('uid') != '0' or
            probe.get('arch') != 'aarch64' or not isinstance(probe.get('firmware'), str) or
            not probe['firmware'].strip() or probe['firmware'].strip() == '?' or
            not {'yandex,cucumber', 'yandex,cucumber-dvt2'}.intersection(probe.get('compatible', '').split())):
        raise ValueError('verified root, Cucumber, architecture and current firmware are required')
    leds = probe.get('leds', {})
    clock = 'cucumber:white:clock-a1'
    selected = {**{c: names_for(c) for c in COMPONENTS}, 'clock': [clock]}
    guards = {}
    for component, names in selected.items():
        commands = ['set -eu', IDENTITY_CHECK,
                    'test "$(cat /system/vendor/quasar/version_full)" = ' + shlex.quote(probe['firmware'].strip())]
        for name in names:
            led = leds.get(name, {})
            if (type(led.get('max')) is not int or not 0 < led['max'] <= 65535 or
                    not isinstance(led.get('trigger'), str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', led['trigger'])):
                raise ValueError('missing or invalid physical LED metadata: ' + name)
            commands += [f'test "$(cat /sys/class/leds/{name}/max_brightness)" = {led["max"]}']
        guards[component] = '\n'.join(commands) + '\n'
    result = {}
    for component in COMPONENTS:
        for color, rgb in TRIAL_COLORS.items():
            result[f'{component}-{color}-2s.sh'] = guards[component] + rgb_trial_script(component, rgb, 2)
    result['clock-a1-1s.sh'] = guards['clock'] + clock_segment_test_script(clock, 8, 1)
    return result


def rgb_colors_ready(report, component):
    # A repeated probe without an observation does not revoke accepted evidence.
    # An explicit mismatch does; failed restoration is guarded by report.blocked.
    latest = {t.get('color'): t for t in report.get('trials', [])
              if t.get('component') == component and t.get('state') in ('passed','failed')}
    return all(latest.get(color, {}).get('state') == 'passed' and
               latest[color].get('restored') is True and latest[color].get('observation') == 'matched' and
               latest[color].get('returncode', 0) == 0 for color in TRIAL_COLORS)


def rgb_controls_ready(report, component):
    latest = next((t for t in reversed(report.get('trials', []))
                   if t.get('component') == component + '_controls' and t.get('state') in ('passed','failed')), {})
    return (rgb_colors_ready(report, component) and latest.get('state') == 'passed' and
            latest.get('restored') is True and latest.get('observation') == 'matched' and
            latest.get('returncode', 0) == 0)


def rgb_control_trial_script(probe, component, *, leds='/sys/class/leds', state_dir='/tmp',
                             identity=IDENTITY_CHECK, firmware_path='/system/vendor/quasar/version_full'):
    """Exercise the ordinary set/off/set/reset writer with one original snapshot."""
    reviewed_trial_scripts(probe)  # Validate root, identity, firmware and channel metadata.
    options = dict(leds=leds, state_dir=state_dir, identity=identity)
    snapshot = state_dir.rstrip('/') + '/webpanel_midi_' + component + '.before'
    guard = [identity, 'test "$(cat ' + shlex.quote(firmware_path) + ')" = ' + shlex.quote(probe['firmware'])]
    for name in names_for(component):
        guard.append('test "$(cat ' + shlex.quote(leds+'/'+name+'/max_brightness') + ')" = ' + str(probe['leds'][name]['max']))
    # No writes near a minute boundary: the post-check also verifies all white LEDs.
    phase = 'phase=$(date +%S)\ncase "$phase" in 00|01|02|4[2-9]|5[0-9]) exit 42;; [0-5][0-9]) ;; *) exit 43;; esac\n'
    source = ('set -eu\n' + '\n'.join(guard) + '\n' + phase +
              'S=' + shlex.quote(snapshot) + '\n[ ! -e "$S" ] && [ ! -L "$S" ]\n' +
              'rollback() (\n' + led_script(component, recover_stale=True, **options) + ')\n' +
              "trap rollback 0\ntrap 'exit 1' 1 2 15\n")
    for rgb, seconds in (((8,0,0),2), ((0,0,0),1), ((0,8,0),2)):
        source += '(\n' + led_script(component, rgb, **options) + f')\nsleep {seconds}\n'
    return source


class ColorTests:
    """One owner-operated LED test at a time, with a private durable report.

    This does not enable the normal ring/logo hardware flags. The existing
    authenticated API and the owner's colour-button click authorize each trial.
    """
    def __init__(self, panel, server):
        self.panel, self.server = panel, server
        self.lock = threading.Lock()
        self.path = server.DATA_DIR / '.midi_tests.json'
        serial = str(getattr(panel.adb, 'serial', ''))
        self.target = hashlib.sha256(serial.encode()).hexdigest()

    def _load(self):
        if not self.path.exists():
            return {'version': 1, 'target': self.target, 'firmware': None, 'blocked': False, 'trials': []}
        try:
            report = json.loads(self.path.read_text())
            if (report['version'] != 1 or report['target'] != self.target or
                    not isinstance(report['trials'], list)):
                raise ValueError('wrong report')
            return report
        except (OSError, ValueError, KeyError, TypeError):
            raise self.server.AdbError('Отчёт проверок повреждён или принадлежит другой колонке; он сохранён без изменений') from None

    def _save(self, report):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.midi_tests-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as file:
                json.dump(report, file, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _public(trial):
        fields = ('id', 'component', 'color', 'rgb', 'state', 'restored', 'observation',
                  'initial_mismatch', 'created', 'message', 'source', 'segment', 'description', 'position', 'no_write',
                  'brightness', 'brightness_max', 'pattern', 'off_seconds', 'on_seconds',
                  'photo_seconds', 'brightness_percent', 'dim_percent', 'channels', 'photo_point',
                  'display_texts', 'display_seconds', 'display_check', 'display_brightness',
                  'target_component', 'control_seconds', 'display_mode', 'timer_seconds')
        return {key: trial[key] for key in fields if key in trial}

    def status(self):
        report = self._load()
        try:
            clock_display_layout(report)
            display_ready, display_reason = True, ''
        except ValueError as error:
            display_ready, display_reason = False, str(error)
        return {'firmware': report['firmware'], 'busy': self.lock.locked(),
                'blocked': report['blocked'], 'clock_segments': report.get('clock_segments', []),
                'display_test_ready': display_ready, 'display_test_reason': display_reason,
                'display_controls_ready': display_ready and not report['blocked'] and display_controls_ready(report),
                'rgb_controls': {c: {'colors_ready':rgb_colors_ready(report,c),
                    'ready':not report['blocked'] and rgb_controls_ready(report,c),
                    'can_reset':any(t.get('component')==c+'_controls' and t.get('state')=='passed'
                        and t.get('restored') and t.get('observation')=='matched' for t in report['trials'])}
                    for c in COMPONENTS},
                'clock_references': report.get('clock_references', {}),
                'trials': [self._public(t) for t in report['trials']]}

    def clock_catalog(self):
        if not self.lock.acquire(blocking=False):
            raise self.server.AdbError('Дождитесь завершения текущей пробы')
        try:
            report = self._load()
            probe = self._probe(report)
            report['clock_segments'] = probe['clock_segments']
            report['firmware'] = probe['firmware']
            self._save(report)
        finally:
            self.lock.release()
        result = self.status()
        result['clock_reference'] = self.clock_reference()
        return result

    def clock_reference(self):
        """Private owner photo, served locally with the existing test catalog."""
        try:
            config = json.loads((self.server.DATA_DIR / '.midi-clock-reference.json').read_text())
            width, height, crop = config['width'], config['height'], config['crop']
            if (type(width) is not int or type(height) is not int or not 1 <= width <= 8192 or not 1 <= height <= 8192 or
                    not isinstance(crop, dict) or any(type(crop.get(k)) not in (int,float) or not 0 <= crop[k] <= 1 for k in ('x','y','width','height')) or
                    not (0 <= crop['x'] < 1 and 0 <= crop['y'] < 1 and 0 < crop['width'] <= 1-crop['x'] and 0 < crop['height'] <= 1-crop['y'])):
                return None
            source_hash = config['source_sha256']
            if not isinstance(source_hash,str) or not re.fullmatch('[0-9a-f]{64}',source_hash):
                return None
            image = (self.server.DATA_DIR / ('.midi-clock-reference-' + source_hash + '.jpg')).read_bytes()
            if not image.startswith(b'\xff\xd8') or len(image)>5*1024*1024 or hashlib.sha256(image).hexdigest()!=source_hash:
                return None
            geometry = {'width':width,'height':height,'crop':crop}
            reference = hashlib.sha256(image + json.dumps(geometry,sort_keys=True).encode()).hexdigest()
            return {**geometry,'id':reference,'image':'data:image/jpeg;base64,'+base64.b64encode(image).decode('ascii')}
        except (OSError,ValueError,KeyError,TypeError):
            return None

    def _probe(self, report):
        probe = self.panel.midi_hardware()
        reviewed_trial_scripts(probe)  # Root, exact Cucumber identity and all real LED limits.
        if report['firmware'] and report['firmware'] != probe['firmware']:
            raise self.server.AdbError('Прошивка изменилась: начните новый проверенный отчёт перед пробами')
        return probe

    def _verify(self, before, extra=()):
        names = names_for('ring') + names_for('logo') + list(extra)
        consecutive, initial_mismatch, after = 0, False, None
        for attempt in range(4):
            after = self.panel.midi_hardware()
            matched = (after.get('root') is True and after.get('firmware') == before['firmware'] and
                       after.get('compatible') == before['compatible'] and
                       all(before['leds'].get(n) == after.get('leds', {}).get(n) for n in names))
            if attempt == 0:
                initial_mismatch = not matched
            consecutive = consecutive + 1 if matched else 0
            if consecutive >= 2:
                return True, initial_mismatch, after
            if attempt != 3:
                time.sleep(.35)
        return False, initial_mismatch, after

    def run(self, data):
        control = isinstance(data, dict) and data.get('component') in ('ring_controls', 'logo_controls')
        photo = isinstance(data, dict) and data.get('component') == 'clock_photo'
        custom = isinstance(data, dict) and data.get('component') == 'clock_text'
        timer = isinstance(data, dict) and data.get('component') == 'clock_timer'
        display = isinstance(data, dict) and data.get('component') in ('clock_display', 'clock_text', 'clock_timer')
        clock = isinstance(data, dict) and data.get('component') in ('clock', 'clock_photo', 'clock_display', 'clock_text', 'clock_timer')
        check = data.get('check', 'digits') if display else None
        if custom and data.get('mode','text') not in ('text','date','temp'):
            raise self.server.AdbError('Неизвестный режим краткого показа')
        if display and (not isinstance(check, str) or check not in DISPLAY_CHECKS):
            raise self.server.AdbError('Неизвестная проверка дисплея')
        if clock:
            if (not photo and not display and not isinstance(data.get('segment'), str)) or (photo and data.get('segment') is not None and not isinstance(data['segment'], str)):
                raise self.server.AdbError('Выберите реальный сегмент дисплея')
        elif not control and (not isinstance(data, dict) or not isinstance(data.get('component'), str) or
              not isinstance(data.get('color'), str) or data['component'] not in COMPONENTS or data['color'] not in TRIAL_COLORS):
            raise self.server.AdbError('Выберите кольцо/логотип и красный, зелёный, синий или жёлтый цвет')
        if not self.lock.acquire(blocking=False):
            raise self.server.AdbError('Дождитесь завершения текущей пробы')
        try:
            report = self._load()
            if report['blocked']:
                raise self.server.AdbError('Пробы остановлены до подтверждения восстановления')
            if control and not rgb_colors_ready(report, data['component'].removesuffix('_controls')):
                raise self.server.AdbError('Сначала подтвердите четыре цвета этого компонента')
            if display:
                try:
                    layout = clock_display_layout(report)
                    if custom or timer:
                        if not display_controls_ready(report):
                            raise ValueError('Сначала завершите проверки цифр, градуса и яркости')
                        # Validate user input before reading or writing the device.
                        if timer:
                            clock_timer_frames({'clock_segments':report['clock_segments']},layout,data.get('seconds'),data.get('brightness'))
                        else:
                            clock_text_frames({'clock_segments':report['clock_segments']},layout,data.get('text'),data.get('brightness'))
                except ValueError as error:
                    raise self.server.AdbError(str(error)) from None
            if clock or control:
                info = getattr(getattr(self.panel, 'glagol', None), 'info', None)
                music = info() if callable(info) else {}
                if (music.get('state') or {}).get('playing') is True:
                    raise self.server.AdbError('Перед проверкой сегментов поставьте музыку на паузу')
            before = self._probe(report)
            component = data['component']
            if photo or display:
                segment = data.get('segment')
                if segment is not None and segment not in before['clock_segments']:
                    raise self.server.AdbError('Сегмента нет в текущем снимке Миди')
                guard = IDENTITY_CHECK + '\ntest "$(cat /system/vendor/quasar/version_full)" = ' + shlex.quote(before['firmware'])
                phase = ('phase=$(date +%S)\ncase "$phase" in 00|01|02|4[2-9]|5[0-9]) exit 42;; '
                         '[0-5][0-9]) ;; *) exit 43;; esac\n')
                if display and (before['firmware'] != DISPLAY_TEST_FIRMWARE or
                                set(before['clock_segments']) != set(report['clock_segments'])):
                    raise self.server.AdbError('Текущий дисплей отличается от принятой карты H10')
                frames = (clock_timer_frames(before,layout,data['seconds'],data['brightness']) if timer else
                          clock_text_frames(before,layout,data['text'],data['brightness']) if custom else
                          clock_display_frames(before,layout,check) if display else None)
                body = (_clock_group_script(before, frames) if display else
                        clock_photo_script(before, segment))
                source = 'set -eu\n' + guard + '\n' + phase + body
                extra = before['clock_segments']
                params = ({'display_texts': DISPLAY_CHECKS[check][0], 'display_seconds': 2 * len(DISPLAY_CHECKS[check][0]),
                           'display_check': check, 'display_brightness': DISPLAY_CHECKS[check][1],
                           'brightness_percent': DISPLAY_CHECKS[check][1][0], 'channels': len(extra)} if display else
                          {'segment': segment, 'photo_seconds': 10, 'brightness_percent': 60,
                           'dim_percent': 10 if segment else None, 'channels': len(extra)})
                if custom:
                    params = {'display_texts':[data['text']], 'display_seconds':8,
                              'brightness_percent':data['brightness'], 'channels':len(extra),
                              'display_mode':data.get('mode','text')}
                if timer:
                    params = {'display_texts':[f'00:{left:02d}' for left in range(8,-1,-1)],
                              'display_seconds':10, 'timer_seconds':8,
                              'brightness_percent':data['brightness'], 'channels':len(extra)}
            elif clock:
                segment = data['segment']
                if segment not in before['clock_segments']:
                    raise self.server.AdbError('Сегмента нет в текущем снимке Миди')
                led = before['leds'][segment]
                if not led.get('trigger') or not led.get('max'):
                    raise self.server.AdbError('Параметры сегмента не подтверждены')
                guard = (IDENTITY_CHECK + '\ntest "$(cat /system/vendor/quasar/version_full)" = ' + shlex.quote(before['firmware']) +
                         '\ntest "$(cat /sys/class/leds/' + segment + '/max_brightness)" = ' + str(led['max']))
                # A minute boundary could legitimately change the original clock
                # value while testing. Skip before touching any snapshot or LED.
                phase = ('phase=$(date +%S)\ncase "$phase" in 00|01|02|54|55|56|57|58|59) exit 42;; '
                         '[0-5][0-9]) ;; *) exit 43;; esac\n')
                source = 'set -eu\n' + guard + '\n' + phase + clock_segment_test_script(segment, CLOCK_TRIAL_BRIGHTNESS, 2, off_seconds=1)
                params = {'segment': segment, 'brightness': CLOCK_TRIAL_BRIGHTNESS * led['max'] // 255,
                          'brightness_max': led['max'], 'pattern': 'off-on', 'off_seconds': 1, 'on_seconds': 2}
                extra = (segment,)
            elif control:
                target = component.removesuffix('_controls')
                source = rgb_control_trial_script(before, target)
                params = {'target_component':target, 'control_seconds':5}
                extra = before['clock_segments']
            else:
                color = data['color']
                source = reviewed_trial_scripts(before)[f'{component}-{color}-2s.sh']
                params = {'color': color, 'rgb': TRIAL_COLORS[color]}
                extra = ()
            trial = {'id': os.urandom(12).hex(), 'component': component, **params,
                     'created': time.time(), 'state': 'running',
                     'restored': False, 'before': before}
            report['firmware'], report['blocked'] = before['firmware'], True
            report['trials'].append(trial)
            self._save(report)  # The initial snapshot survives a server/browser disconnect.
            try:
                _, _, rc = self.panel.adb.shell(source, timeout=30, check=False)
                trial['returncode'] = rc
                if (clock or control) and rc in (42, 43):
                    trial.update(state='not_run', restored=True, no_write=True,
                                 message='Проба пропущена перед сменой минуты. Повторите через несколько секунд.' if rc == 42 else 'Не удалось проверить время колонки; сегмент не менялся.')
                    report['blocked'] = False
                    self._save(report)
                    return {'trial': self._public(trial)}
                restored, initial, after = self._verify(before, extra)
                trial.update(restored=restored, initial_mismatch=initial, after=after)
                trial['state'] = ('completed' if custom else 'photo_ready' if photo else 'awaiting_observation') if restored and rc == 0 else 'error'
                trial['message'] = (('Дисплей восстановлен. Прикрепите фото в чат вместе с именем выделенного канала.' if photo else
                                    'Состояние восстановлено. Отметьте, что увидели.') if restored and rc == 0 else
                                    'Проба завершилась с ошибкой. Проверьте восстановление.')
                report['blocked'] = not restored
            except Exception as exc:
                trial.update(state='error', message='Не удалось подтвердить восстановление. Новые пробы остановлены.', error=str(exc))
            self._save(report)
            return {'trial': self._public(trial)}
        finally:
            self.lock.release()

    def observe(self, data):
        if not isinstance(data, dict) or data.get('observation') not in ('matched', 'wrong', 'missed', 'located', 'outside_map'):
            raise self.server.AdbError('Отметьте: цвет совпал, другой цвет или не заметил')
        if not self.lock.acquire(blocking=False):
            raise self.server.AdbError('Дождитесь завершения текущей пробы')
        try:
            report = self._load()
            trial = next((t for t in report['trials'] if t['id'] == data.get('id')), None)
            if not trial or not trial.get('restored') or trial.get('returncode', 0) != 0:
                raise self.server.AdbError('Сначала необходимо подтвердить техническое завершение пробы')
            if trial['component'] == 'clock_photo':
                raise self.server.AdbError('Фотопроба не сопоставляет каналы автоматически; приложите фото в чат')
            if trial['component'] == 'clock':
                if data['observation'] not in ('located', 'missed', 'outside_map'):
                    raise self.server.AdbError('Для сегмента запишите расположение или отметьте «не заметил»')
                if data['observation'] == 'located':
                    if 'photo_point' in data:
                        point=data['photo_point'];reference=self.clock_reference()
                        if (not isinstance(point,dict) or not reference or point.get('reference')!=reference['id'] or
                                any(type(point.get(k)) not in (int,float) or not 0 <= point[k] <= 1 for k in ('x','y'))):
                            raise self.server.AdbError('Выберите место на актуальном фото дисплея')
                        point={k:round(point[k],4) for k in ('x','y')};point['reference']=reference['id']
                        description=f'Место на фото: {point["x"]*100:.1f}% слева, {point["y"]*100:.1f}% сверху'
                        trial['photo_point']=point
                        report.setdefault('clock_references',{})[reference['id']]={k:reference[k] for k in ('id','width','height','crop')}
                        trial.pop('position',None)
                    elif 'position' in data:
                        position = data['position']
                        if not isinstance(position, str) or position not in CLOCK_POSITIONS:
                            raise self.server.AdbError('Выберите сегмент на схеме дисплея')
                        description = CLOCK_POSITIONS[position]
                        trial['position'] = position
                        trial.pop('photo_point',None)
                    else:
                        description = data.get('description')
                        if not isinstance(description, str) or not 1 <= len(description.strip()) <= 160:
                            raise self.server.AdbError('Выберите место на схеме или опишите другой элемент')
                        trial.pop('position', None)
                        trial.pop('photo_point',None)
                    trial['description'] = description.strip()
                elif data['observation'] == 'outside_map':
                    description = data.get('description', '')
                    if not isinstance(description, str) or len(description.strip()) > 160:
                        raise self.server.AdbError('Описание другого элемента должно быть до 160 символов')
                    trial.pop('position', None)
                    trial.pop('photo_point',None)
                    trial['description'] = description.strip() or 'Элемент вне схемы; расположение не установлено'
            elif data['observation'] in ('located', 'outside_map'):
                raise self.server.AdbError('Расположение записывается только для сегментов дисплея')
            trial['observation'] = data['observation']
            trial['state'] = {'matched': 'passed', 'wrong': 'failed', 'missed': 'not_observed',
                              'located': 'mapped', 'outside_map': 'unmapped'}[data['observation']]
            self._save(report)
            return {'trial': self._public(trial)}
        finally:
            self.lock.release()

    def restore(self):
        if not self.lock.acquire(blocking=False):
            raise self.server.AdbError('Дождитесь завершения текущей пробы')
        try:
            report = self._load()
            if not report['blocked'] or not report['trials']:
                return {'ok': True}
            trial = report['trials'][-1]
            self._probe(report)
            guard = ('set -eu\n' + IDENTITY_CHECK + '\ntest "$(cat /system/vendor/quasar/version_full)" = ' +
                     shlex.quote(report['firmware']) + '\n')
            clock = trial['component'] == 'clock'
            photo = trial['component'] == 'clock_photo'
            group = trial['component'] in ('clock_photo', 'clock_display', 'clock_text', 'clock_timer')
            control = trial['component'] in ('ring_controls', 'logo_controls')
            restore = (clock_photo_script(trial['before'], recover_stale=True) if group else
                       clock_segment_restore_script(trial['segment']) if clock else led_script(trial['target_component'] if control else trial['component'], recover_stale=True))
            self.panel.adb.shell(guard + restore, timeout=30)
            extra = trial['before']['clock_segments'] if group or control else (trial['segment'],) if clock else ()
            restored, initial, after = self._verify(trial['before'], extra)
            trial.update(restored=restored, after=after, recovery_initial_mismatch=initial)
            report['blocked'] = not restored
            if restored and trial.get('returncode') == 0:
                trial['state'] = 'photo_ready' if photo else 'awaiting_observation'
            trial['message'] = 'Восстановление подтверждено.' if restored else 'Восстановление ещё не подтверждено; пробы остановлены.'
            self._save(report)
            return {'ok': restored, 'trial': self._public(trial)}
        finally:
            self.lock.release()


def install(s, profile):
    """Called only for Midi, after the shared model and audio adapters."""
    P = s.Panel
    original_shell = s.Adb.shell

    def shell(self, command, timeout=15, check=True):
        # factory-9 adbd rejects long shell service names. Transfer source with
        # sync, then use the existing exit-status wrapper for its execution.
        if len(command.encode('utf-8')) < 2500:
            return original_shell(self, command, timeout, check)
        remote = '/tmp/webpanel_cmd_' + os.urandom(12).hex() + '.sh'
        try:
            with tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / 'command.sh'
                source.write_text(command, encoding='utf-8')
                source.chmod(0o600)
                self.push(source, remote, timeout=timeout)
            return original_shell(self, 'sh ' + shlex.quote(remote), timeout, check)
        finally:
            try:
                original_shell(self, 'rm -f ' + shlex.quote(remote), 4, False)
            except (s.AdbError, OSError, s.subprocess.TimeoutExpired):
                pass

    s.Adb.shell = shell

    def version(self):
        if self._version is None:
            firmware, _, _ = self.adb.shell('cat /system/vendor/quasar/version_full')
            output, _, _ = self.adb.shell(f'{s.QRUN}/maind --device-id 2>/dev/null', timeout=20)
            match = re.search(r'^Device ID:\s*([A-Za-z0-9_-]+)\s*$', output, re.M)
            did = match[1] if match else ''
            self._version = {'quasar_version': firmware.strip(), 'device_id': did, 'model': 'midi'}
            self._configure_glagol(did)
        return self._version

    P.version = version

    def hardware(self):
        out, _, _ = self.adb.shell(READ_SCRIPT, timeout=30)
        try:
            return parse_probe(out)
        except ValueError as exc:
            raise s.AdbError('Не удалось проверить аппаратуру Миди: ' + str(exc)) from None

    def write(self, component, data=None):
        flag = 'light' if component == 'ring' else 'logo'
        try:
            if data is not None and not isinstance(data, dict):
                raise ValueError('Ожидается цвет компонента')
            rgb = None if data is None else [data.get(c, 0) for c in COLORS]
            script = led_script(component, rgb, recover_stale=data is None)
        except (TypeError, ValueError) as exc:
            raise s.AdbError(str(exc)) from None
        if profile.get(flag):
            self.adb.shell(script, timeout=30)
        else:
            manager = tests(self)
            if not manager.lock.acquire(blocking=False):
                raise s.AdbError('Дождитесь завершения текущего действия')
            try:
                report = manager._load()
                ready = rgb_controls_ready(report, component)
                can_reset = any(t.get('component') == component+'_controls' and t.get('state') == 'passed'
                    and t.get('restored') and t.get('observation') == 'matched' for t in report['trials'])
                if report['blocked'] or not (ready if data is not None else can_reset):
                    raise s.AdbError('Управление ' + component + ' Миди ещё не проверено на колонке')
                if rgb is not None and tuple(rgb) not in (*TRIAL_COLORS.values(), (0,0,0)):
                    raise s.AdbError('Пока доступны проверенные цвета на малой яркости')
                before = manager._probe(report)
                guard = IDENTITY_CHECK + '\ntest "$(cat /system/vendor/quasar/version_full)" = ' + shlex.quote(before['firmware']) + '\n'
                self.adb.shell(guard + script, timeout=30)
            finally:
                manager.lock.release()
        return {'ok': True, 'leds': state(self)['ring']} if component == 'ring' and data is None else {'ok': True}

    def state(self):
        out, _, _ = self.adb.shell("printf 'firmware\\t%s\\n' \"$(cat /system/vendor/quasar/version_full)\"\n" + LED_READ_SCRIPT, timeout=20)
        try:
            hw = parse_probe(out)
        except ValueError as exc:
            raise s.AdbError('Не удалось прочитать светодиоды Миди: ' + str(exc)) from None
        def pixels(component):
            if not hw['components'][component]:
                return []
            values = [hw['leds'][n]['value'] * 255 // max(1, hw['leds'][n]['max']) for n in names_for(component)]
            return [values[i:i + 3] for i in range(0, len(values), 3)]
        ring, logo = pixels('ring'), pixels('logo')
        values = None
        try:
            values = clock_display_values(hw, ColorTests(self, s)._load())
        except (ValueError, KeyError, TypeError, OSError, s.AdbError):
            pass  # Keep unknown data unknown when no valid owner map is available.
        return {'v': values, 'ring': [ring[i] for i in (0, 8, 16)] if ring else [],
                'ring_all': ring, 'logo': logo, 'unverified': values is None,
                'clock_raw': {n: hw['leds'][n] for n in hw['clock_segments']}}

    P.midi_hardware = hardware
    P.ring = lambda self, data: write(self, 'ring', data)
    P.ring_reset = lambda self: write(self, 'ring')
    P.logo = lambda self, data: write(self, 'logo', data)
    P.logo_reset = lambda self: write(self, 'logo')
    P.clock_state = state
    manager_lock = threading.Lock()

    def tests(self):
        with manager_lock:
            if not hasattr(self, '_midi_color_tests'):
                self._midi_color_tests = ColorTests(self, s)
            return self._midi_color_tests

    P.midi_tests = lambda self: tests(self).status()
    P.midi_clock_tests = lambda self: tests(self).clock_catalog()
    P.midi_test_run = lambda self, data: tests(self).run(data)
    P.midi_test_observe = lambda self, data: tests(self).observe(data)
    P.midi_test_restore = lambda self: tests(self).restore()

    class LedStream:
        # The shared stream delegates to Panel.clock_state for this model; it
        # must never start Lite 2's on-device polling process.
        def __init__(self, adb):
            import threading
            self.adb, self.cached, self.at, self.lock = adb, {'v': None}, 0, threading.Lock()

        def get(self):
            import time
            with self.lock:
                if time.monotonic() - self.at > 1:
                    self.cached = state(self)
                    self.at = time.monotonic()
                return self.cached

    s.LedStream = LedStream
