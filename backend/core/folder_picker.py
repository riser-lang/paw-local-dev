# -*- coding: utf-8 -*-
"""PAW Native Folder Picker.

Shows the Windows folder-selection dialog via a dedicated PowerShell child
process. The main PAW process runs windowless, where in-process dialog APIs
fail to display; a separate interactive process owns its own window station
and shows the dialog reliably. The dialog is forced to the foreground so it
never appears behind the browser window.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from typing import Optional

logger = logging.getLogger("paw.folder_picker")


def _ps_escape(text: str) -> str:
    return text.replace("'", "''")


def pick_folder(title: str = "选择文件夹") -> Optional[str]:
    """Open the native Windows folder-selection dialog.

    Returns the chosen folder path, or None when the user cancels.
    Blocks until the user closes the dialog (max 10 minutes).
    """
    if sys.platform != "win32":
        return None
    safe_title = _ps_escape(title or "选择文件夹")
    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms | Out-Null;"
        "$dlg = New-Object System.Windows.Forms.FolderBrowserDialog;"
        f"$dlg.Description = '{safe_title}';"
        "$dlg.ShowNewFolderButton = $true;"
        "$owner = New-Object System.Windows.Forms.Form;"
        "$owner.TopMost = $true;"
        "$owner.ShowInTaskbar = $false;"
        "$owner.WindowState = 'Minimized';"
        "$owner.Opacity = 0;"
        "$owner.Show();"
        "$owner.Activate();"
        "if ($dlg.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { "
        "Write-Output $dlg.SelectedPath "
        "};"
        "$owner.Close();"
    )
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                ps_script,
            ],
            capture_output=True,
            text=True,
            timeout=600,
            creationflags=0x08000000,  # CREATE_NO_WINDOW: never show a console
        )
        path = (result.stdout or "").strip().strip('"')
        if path:
            return path
        return None
    except subprocess.TimeoutExpired:
        logger.warning("Folder picker timed out")
        return None
    except Exception as e:
        logger.warning(f"Folder picker failed: {e}")
        return None
