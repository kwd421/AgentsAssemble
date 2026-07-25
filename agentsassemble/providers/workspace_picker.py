"""Local desktop folder selection for server-owned Agent Sessions."""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path


class WorkspacePickerUnavailable(RuntimeError):
    """The host cannot present a native folder picker."""


def choose_workspace_folder() -> str:
    system = platform.system()
    if system == "Darwin":
        command = _macos_command()
    elif system == "Windows":
        command = _windows_command()
    elif system == "Linux":
        command = _linux_command()
    else:
        raise WorkspacePickerUnavailable("workspace_picker_unsupported_platform")

    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300.0,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise WorkspacePickerUnavailable("workspace_picker_timeout") from error
    except OSError as error:
        raise WorkspacePickerUnavailable("workspace_picker_unavailable") from error

    if completed.returncode != 0:
        if _was_cancelled(system, completed.returncode, completed.stderr):
            return ""
        raise WorkspacePickerUnavailable("workspace_picker_failed")

    selected = completed.stdout.strip()
    if not selected:
        return ""
    path = Path(selected).expanduser().resolve()
    if not path.is_dir():
        raise WorkspacePickerUnavailable("workspace_picker_invalid_selection")
    return str(path)


def _macos_command() -> list[str]:
    executable = shutil.which("osascript")
    if not executable:
        raise WorkspacePickerUnavailable("workspace_picker_unavailable")
    return [
        executable,
        "-e",
        'POSIX path of (choose folder with prompt "AgentsAssemble 작업 폴더 선택")',
    ]


def _windows_command() -> list[str]:
    executable = shutil.which("powershell.exe") or shutil.which("pwsh")
    if not executable:
        raise WorkspacePickerUnavailable("workspace_picker_unavailable")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$dialog.Description = 'AgentsAssemble 작업 폴더 선택'; "
        "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
        "{ [Console]::Out.Write($dialog.SelectedPath); exit 0 }; exit 2"
    )
    return [executable, "-NoProfile", "-NonInteractive", "-Command", script]


def _linux_command() -> list[str]:
    executable = shutil.which("zenity")
    if not executable:
        raise WorkspacePickerUnavailable("workspace_picker_unavailable")
    return [
        executable,
        "--file-selection",
        "--directory",
        "--title=AgentsAssemble 작업 폴더 선택",
    ]


def _was_cancelled(system: str, returncode: int, stderr: str) -> bool:
    if system == "Windows" and returncode == 2:
        return True
    if system == "Linux" and returncode == 1:
        return True
    return system == "Darwin" and (
        "user canceled" in stderr.casefold() or "(-128)" in stderr
    )


__all__ = [
    "WorkspacePickerUnavailable",
    "choose_workspace_folder",
]
