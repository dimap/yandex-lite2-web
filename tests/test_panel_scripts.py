"""Inline-скрипты панели делят один глобальный scope: дубли const/let ломают всю вкладку."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PanelInlineScripts(unittest.TestCase):
    @unittest.skipIf(not shutil.which("node"), "нужен node")
    def test_classic_scripts_parse_together(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        scripts = [body for attrs, body in re.findall(r"<script([^>]*)>(.*?)</script>", html, re.S | re.I)
                   if "src=" not in attrs and body.strip() and "importmap" not in attrs and "module" not in attrs]
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write("\n;\n".join(scripts))
            path = f.name
        try:
            r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
        finally:
            os.unlink(path)
        self.assertEqual(r.returncode, 0, (r.stderr or r.stdout)[-1500:])


if __name__ == "__main__":
    unittest.main()
