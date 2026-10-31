# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""Where the local server finds the user's Documents folder and the Craft app CLIs.

The local server runs on Windows, macOS and Linux, so these follow each OS's own
conventions instead of assuming a Mac home folder.
"""

import os
import shlex
import sys
from pathlib import Path


def documents_dir() -> Path:
    """The user's Documents folder, wherever the OS keeps it."""
    if sys.platform == "win32":
        # OneDrive and folder redirection can move Documents out of the profile folder.
        if (known := _windows_documents()) is not None:
            return known
    elif sys.platform != "darwin":
        if (configured := _xdg_documents()) is not None:
            return configured
    return Path.home() / "Documents"


def data_dir() -> Path:
    """The per-user folder for installed programs' data."""
    if sys.platform == "win32":
        if local := os.environ.get("LOCALAPPDATA"):
            return Path(local)
        return Path.home() / "AppData" / "Local"
    if sys.platform != "darwin" and (xdg := os.environ.get("XDG_DATA_HOME")):
        if Path(xdg).is_absolute():
            return Path(xdg)
    return Path.home() / ".local" / "share"


def craft_cli_path(app: str, version: str) -> Path:
    """Default install path of a Craft app's CLI, as the README's steps lay it out."""
    executable = f"{app}-cli.exe" if sys.platform == "win32" else f"{app}-cli"
    return data_dir() / "artcraft-cli" / app / version / executable


def _windows_documents() -> Path | None:
    try:
        import ctypes
        from ctypes import wintypes
        from uuid import UUID

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", wintypes.BYTE * 8),
            ]

        # FOLDERID_Documents
        folder = UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7")
        guid = GUID.from_buffer_copy(folder.bytes_le)
        path = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path)) != 0:
            return None
        try:
            return Path(path.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(path)
    except (AttributeError, OSError, ValueError):
        return None


def _xdg_documents() -> Path | None:
    """XDG_DOCUMENTS_DIR from ~/.config/user-dirs.dirs, which desktop Linux uses for localised names."""
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    try:
        lines = (config / "user-dirs.dirs").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    for line in lines:
        name, _, value = line.strip().partition("=")
        if name != "XDG_DOCUMENTS_DIR":
            continue
        try:
            parts = shlex.split(value)
        except ValueError:
            return None
        if len(parts) != 1:
            return None
        raw = parts[0]
        if raw.startswith("$HOME/"):
            path = Path.home() / raw.removeprefix("$HOME/")
        elif raw.startswith("/"):
            path = Path(raw)
        else:
            return None
        # A value of plain $HOME means the desktop has no separate Documents folder.
        return None if path == Path.home() else path
    return None
