"""Windows pythonw launch/reopen/shutdown smoke test; no PACER or browser network."""
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import urlsplit

from roc.common import read_json


@unittest.skipUnless(os.name == 'nt', 'Windows windowless launcher')
class DesktopTests(unittest.TestCase):
    def test_windowless_launcher_reopens_then_stops_without_console_or_paid_work(self):
        pythonw = Path(sys.executable).with_name('pythonw.exe')
        if not pythonw.is_file():
            self.skipTest('pythonw runtime unavailable')
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = root / 'launch.py'
            script.write_text('import sys\nsys.path.insert(0, '+repr(str(repo))+')\n'
                'from unittest.mock import patch\nfrom roc.desktop import main\n'
                'with patch("webbrowser.open", return_value=True):\n    raise SystemExit(main())\n', encoding='utf-8')
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            process = subprocess.Popen([str(pythonw), str(script)], cwd=root, creationflags=flags)
            try:
                path = root / 'runs/workspace/interface-connection.json'
                deadline = time.monotonic() + 15
                while not path.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(path.is_file(), 'Windowless app did not create its local connection file')
                info = read_json(path)
                second = subprocess.run([str(pythonw),str(script)],cwd=root,creationflags=flags,timeout=10)
                self.assertEqual(second.returncode,0)
                self.assertEqual(read_json(path),info)
                url=urlsplit(info['url'])
                connection=http.client.HTTPConnection('127.0.0.1',url.port,timeout=10)
                try:
                    connection.request('GET','/api/runs',headers={'X-ROC-Token':url.fragment})
                    result=connection.getresponse()
                    state=json.loads(result.read())
                    self.assertEqual(state['jobs'],[])
                    self.assertFalse(state['connection']['connected'])
                    connection.request('POST','/api/stop','{}',headers={'X-ROC-Token':url.fragment,'Content-Type':'application/json'})
                    self.assertEqual(connection.getresponse().status,200)
                finally:
                    connection.close()
                self.assertEqual(process.wait(15),0)
                self.assertFalse(path.exists())
                self.assertFalse((root / 'runs/workspace/.workspace.lock').exists())
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(10)
