"""Native folder picker for the code screen (Windows): a PowerShell FolderBrowserDialog on this PC.

The browser cannot give a web page a real folder path, so the local server asks Windows for one. Returns the chosen
path or "" (cancelled / unavailable). Only the path is read; nothing in the folder is touched.
"""
import os
import subprocess
import sys

SCRIPT = ("Add-Type -AssemblyName System.Windows.Forms; "
          "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
          "$d.Description = 'JetAI가 작업할 프로젝트 폴더(git 저장소)를 고르세요'; $d.ShowNewFolderButton = $false; "
          "$f = New-Object System.Windows.Forms.Form; $f.TopMost = $true; "
          "if ($d.ShowDialog($f) -eq 'OK') { [Console]::OutputEncoding = [Text.Encoding]::UTF8; Write-Output $d.SelectedPath }")


def pick(run=subprocess.run, platform=None, timeout=300):
    if (platform or sys.platform) != "win32":
        return ""
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), r"System32\WindowsPowerShell\v1.0\powershell.exe")
    try:
        done = run([exe, "-NoProfile", "-STA", "-Command", SCRIPT], capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    out = (done.stdout or b"").decode("utf-8", "replace").strip()
    return out.splitlines()[-1].strip() if out else ""
