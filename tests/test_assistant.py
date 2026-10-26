import gzip
import io
import json
import re
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

import assistant
import server


class AssistantBridge(unittest.TestCase):
    def test_secret_routes_reject_plain_lan_and_simple_cross_site_posts(self):
        for address, content_type, origin, rejected in (
            ('127.0.0.1', 'application/json', 'http://localhost:8765', False),
            ('192.168.1.20', 'application/json', 'http://localhost:8765', True),
            ('127.0.0.1', 'text/plain', 'http://localhost:8765', True),
            ('127.0.0.1', 'application/json', 'null', True),
        ):
            handler = server.Handler.__new__(server.Handler)
            handler.command = 'POST'
            handler.connection = Mock()
            handler.client_address = (address, 1234)
            handler.headers = {'Content-Type': content_type, 'Origin': origin, 'Host': 'localhost:8765'}
            handler.access = Mock()
            handler.access.session_ok.return_value = True
            handler._send = Mock()
            with patch.dict('os.environ', {}, clear=True):
                self.assertEqual(handler._guard('/api/assistant/config'), rejected)

    def test_secret_is_transferred_as_private_file_not_shell_argument(self):
        adb = Mock()
        secret = "test-api-secret"
        pushed = []
        def push(local, remote):
            self.assertEqual(Path(local).stat().st_mode & 0o777, 0o600)
            pushed.append(json.loads(Path(local).read_text()))
        def shell(command, **kwargs):
            self.assertNotIn(secret, command)
            if command.startswith("rm -f"):
                return "", "", 0
            marker = re.search(r"__assistant_exit_[a-f0-9]+__", command).group()
            return '{"installed":true,"has_key":true}\n' + marker + '0\n', "", 0
        adb.push.side_effect = push
        adb.shell.side_effect = shell
        result = assistant.Assistant(adb).command("config", {"api_key": secret})
        self.assertTrue(result["has_key"])
        self.assertEqual(pushed, [{"api_key": secret}])
        self.assertTrue(adb.shell.call_args.args[0].startswith("rm -f"))

    def test_old_adbd_failure_is_not_reported_as_success(self):
        adb = Mock()
        def shell(command, **kwargs):
            marker = re.search(r"__assistant_exit_[a-f0-9]+__", command).group()
            return 'download failed\n' + marker + '1\n', '', 0
        adb.shell.side_effect = shell
        with self.assertRaisesRegex(ValueError, 'download failed'):
            assistant.Assistant(adb).shell('false')

    def test_credentials_are_excluded_from_backup(self):
        for name in ('assistant/config.json', 'assistant/vpn.ovpn', 'assistant/vpn.auth', 'assistant/.request-123'):
            self.assertIsNone(server.Panel.BACKUP_FILES.fullmatch(name))

    def test_dependency_order_and_checksum_required(self):
        index = '''Package: openvpn-openssl
Depends: libc, liblzo
Filename: vpn.ipk
SHA256sum: {sha}

Package: liblzo
Depends: libc
Filename: lzo.ipk
SHA256sum: {sha}

Package: ip-full
Depends: libc
Filename: ip.ipk
SHA256sum: {sha}
'''.format(sha='a'*64)
        with patch('assistant.urllib.request.urlopen', return_value=io.BytesIO(gzip.compress(index.encode()))):
            packages = assistant.vpn_packages({'libc'})
        self.assertEqual([p['Package'] for p in packages], ['liblzo', 'openvpn-openssl', 'ip-full'])
        index = index.replace('Filename: lzo.ipk', 'Filename: ../bad.ipk')
        with patch('assistant.urllib.request.urlopen', return_value=io.BytesIO(gzip.compress(index.encode()))):
            with self.assertRaises(ValueError):
                assistant.vpn_packages({'libc'})

    def test_reject_command_names_before_adb(self):
        adb = Mock()
        with self.assertRaises(ValueError):
            assistant.Assistant(adb).command('status; reboot')
        adb.push.assert_not_called()
        adb.shell.assert_not_called()


if __name__ == '__main__':
    unittest.main()
