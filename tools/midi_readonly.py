#!/usr/bin/env python3
"""Bounded Cucumber audit, using only reads and an explicitly selected ADB target.

No ADB server restart, root request, file transfer, firmware tool or service
command is used. The target address and transport error belong in a PRIVATE
report; successful hardware metadata excludes account/network identifiers.
"""
import argparse
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from midi import READ_SCRIPT, parse_probe


def audit(serial, adb='adb', runner=subprocess.run, connector=socket.create_connection):
    result = {'target': serial, 'mode': 'read-only', 'hardware': None}
    # The connect target must be a literal IP or ADB serial selected by the user;
    # it is never discovered by falling back to an arbitrary attached device.
    try:
        remote = runner([adb, '-s', serial, 'shell', READ_SCRIPT],
                        text=True, capture_output=True, timeout=35)
        if remote.returncode:
            result['adb'] = 'unavailable'
            result['error'] = (remote.stderr or remote.stdout).strip()[-500:]
        else:
            result['hardware'] = parse_probe(remote.stdout)
            result['adb'] = 'connected'
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        result.update(adb='unavailable', error=str(exc)[:500])
    host, sep, port = serial.rpartition(':')
    if sep and port.isdecimal():
        result['ports'] = {}
        for service in (int(port), 1961, 22, 2222):
            try:
                with connector((host, service), timeout=2):
                    result['ports'][str(service)] = 'open'
            except OSError as exc:
                result['ports'][str(service)] = type(exc).__name__
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True, help='explicit ADB serial/address; never auto-selects')
    parser.add_argument('--adb', default=shutil.which('adb') or 'adb')
    args = parser.parse_args()
    result = audit(args.serial, args.adb)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['hardware'] is not None else 2


if __name__ == '__main__':
    raise SystemExit(main())
