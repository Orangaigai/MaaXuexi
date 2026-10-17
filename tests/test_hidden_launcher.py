"""Exercise the GUI launcher against isolated PowerShell scripts, never the app."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1]/'scripts/Launch-Hidden.py'
spec = importlib.util.spec_from_file_location('maa_hidden_launcher', SOURCE)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


@unittest.skipUnless(os.name == 'nt', 'Windows GUI launcher')
class HiddenLauncherTests(unittest.TestCase):
    def test_pythonw_entry_and_powershell_child_allocate_no_console(self):
        with tempfile.TemporaryDirectory(prefix='maa hidden 中文 ') as temporary:
            root = Path(temporary)
            (root/'scripts').mkdir()
            shutil.copyfile(SOURCE, root/'scripts/Launch-Hidden.py')
            (root/'scripts/Start-MaaXuexi.ps1').write_text('''
Add-Type 'using System; using System.Runtime.InteropServices; public class MaaConsoleProbe { [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); }'
@{console_window=[MaaConsoleProbe]::GetConsoleWindow().ToInt64();cwd=(Get-Location).Path} | ConvertTo-Json -Compress
exit 0
''', encoding='utf-8-sig')
            pythonw = Path(sys.executable).with_name('pythonw.exe')
            result = subprocess.run([str(pythonw), str(root/'scripts/Launch-Hidden.py')],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0)
            log = next((root/'logs').glob('launcher-*.log')).read_text('utf-8-sig')
            data = json.loads(next(line for line in log.splitlines() if line.startswith('{')))
            self.assertEqual(data['console_window'], 0)
            self.assertEqual(Path(data['cwd']), root)
            self.assertIn('Launcher exit code: 0', log)

    def test_daily_failure_exit_code_is_preserved_and_logged(self):
        with tempfile.TemporaryDirectory(prefix='maa-hidden-failure-') as temporary:
            root = Path(temporary)
            (root/'scripts').mkdir()
            (root/'scripts/Run-Daily.ps1').write_text("Write-Output 'Simulated startup failure'\nexit 7\n", encoding='utf-8-sig')
            result = launcher.run_launcher(root, daily=True, notify=False)
            self.assertEqual(result, 7)
            log = next((root/'logs').glob('launcher-*.log')).read_text('utf-8-sig')
            self.assertIn('Simulated startup failure', log)
            self.assertIn('Launcher exit code: 7', log)

    def test_check_mode_does_not_enter_daily_execution(self):
        with tempfile.TemporaryDirectory(prefix='maa-hidden-check-') as temporary:
            root = Path(temporary)
            (root/'scripts').mkdir()
            (root/'scripts/Run-Daily.ps1').write_text(
                "param([switch]$CheckOnly)\nif(!$CheckOnly){exit 9}\nexit 0\n", encoding='utf-8-sig')
            self.assertEqual(launcher.run_launcher(root, check_only=True, notify=False), 0)


if __name__ == '__main__':
    unittest.main()
