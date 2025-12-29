"""
steamcmd_manager.py

Shared SteamCMD runtime manager for Crafty Controller.

Design goals:
- Install SteamCMD once under <CRAFTY_DATA_ROOT>/steamcmd/<platform>/
- Reuse that runtime for all SteamCMD-based dedicated server installs (no redundancy)
- Install each game into its own per-server directory via +force_install_dir
- Capture stdout/stderr and raise helpful exceptions
- Be resilient to SteamCMD's self-update/relaunch behavior on Windows
"""

from __future__ import annotations

import os
import subprocess
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlretrieve
from typing import Optional

from app.classes.helpers.helpers import Helpers
import logging
logger = logging.getLogger(__name__)


@dataclass
class SteamCmdResult:
    returncode: int
    stdout: str
    stderr: str
    args: list[str]


class SteamCmdError(RuntimeError):
    def __init__(self, message: str, result: SteamCmdResult):
        super().__init__(message)
        self.result = result


class SteamCmdManager:
    """
    Shared SteamCMD runtime manager.

    - Installs SteamCMD once into: <data_root>/steamcmd/<platform>/
    - Runs SteamCMD with cwd set to that folder (stable steamapps/logs state)
    - Caller passes force_install_dir to install per-server app files elsewhere
    """

    STEAMCMD_WINDOWS_ZIP = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip"

    # SteamCMD can hang on poor connections; make this configurable if needed.
    DEFAULT_TIMEOUT_SECONDS = 1800  # 30 minutes

    def __init__(self, helper: Helpers):
        self.helper = helper
        self.platform = "windows" if os.name == "nt" else "linux"
        self.root = Path(self.helper.root_dir) / "steamcmd" / self.platform
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def exe_path(self) -> Path:
        if os.name == "nt":
            return self.root / "steamcmd.exe"
        # Linux: most common is steamcmd.sh (implementation later)
        return self.root / "steamcmd.sh"

    def ensure_installed(self) -> None:
        """
        Ensure SteamCMD exists in the shared folder.

        Windows:
          - downloads and extracts steamcmd.zip if missing
          - runs a bootstrap pass to allow self-update / initialization

        Linux:
          - not implemented here yet (you'll likely use distro package + known path)
        """
        if self.exe_path.exists():
            # Even if it exists, bootstrap so it's updated/usable.
            return

        if os.name != "nt":
            raise SteamCmdError(
                f"SteamCMD not found at {self.exe_path}. Linux install handling not implemented yet.",
                SteamCmdResult(1, "", "", [str(self.exe_path)]),
            )

        zip_path = self.root / "steamcmd.zip"
        urlretrieve(self.STEAMCMD_WINDOWS_ZIP, zip_path)

        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(self.root)

        try:
            zip_path.unlink(missing_ok=True)
        except Exception:
            # Not critical
            pass

        if not self.exe_path.exists():
            raise SteamCmdError(
                f"SteamCMD install failed: {self.exe_path} not found after extracting.",
                SteamCmdResult(1, "", "", [str(self.exe_path)]),
            )

        # SteamCMD often self-updates and relaunches on first run.
        self._bootstrap()

    def _bootstrap(self) -> None:
        """
        SteamCMD may return a non-zero exit code during its self-update relaunch.
        We retry a couple times if the output looks like an update/relaunch sequence.
        """
        attempts = 3
        for i in range(attempts):
            try:
                self.run(["+quit"])
                return
            except SteamCmdError as e:
                out = (e.result.stdout + "\n" + e.result.stderr).lower()

                relaunch_signals = [
                    "update complete, launching",
                    "applying update",
                    "installing update",
                    "checking for available updates",
                    "ilocalize::addfile() failed to load file",
                ]

                if any(s in out for s in relaunch_signals) and i < attempts - 1:
                    time.sleep(2)
                    continue

                raise

    def run(self, args: list[str], timeout_seconds: int | None = None) -> SteamCmdResult:
        """
        Run SteamCMD and return captured output. Raises SteamCmdError on failure.

        Notes:
        - SteamCMD installs/updates can take a long time. If timeout_seconds is None,
          we do NOT impose a timeout (recommended for app_update).
        - Uses cwd=self.root so SteamCMD keeps stable state (steamapps/logs/appcache).
        """
        argv = [str(self.exe_path), *args]

        # If caller doesn't set a timeout, don't force one (SteamCMD downloads can be slow).
        timeout: Optional[int]
        if timeout_seconds is None:
            timeout = None
        else:
            timeout = int(timeout_seconds)

        creationflags = 0
        if os.name == "nt":
            # Prevent popping a console window when run from a service/thread.
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            logger.info(f"[steamcmd] RUN: {argv} timeout={timeout} cwd={self.root}")
            completed = subprocess.run(
                argv,
                cwd=str(self.root),
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired as e:
            # stdout/stderr can be bytes or str depending on platform
            def _to_text(v) -> str:
                if v is None:
                    return ""
                if isinstance(v, bytes):
                    return v.decode("utf-8", errors="replace")
                return str(v)

            result = SteamCmdResult(
                returncode=124,
                stdout=_to_text(e.stdout),
                stderr=_to_text(e.stderr),
                args=argv,
            )
            raise SteamCmdError(
                f"SteamCMD timed out after {timeout} seconds.\n"
                f"Hint: check logs in {self.root / 'logs'}",
                result,
            ) from e
        except OSError as e:
            result = SteamCmdResult(
                returncode=1,
                stdout="",
                stderr=str(e),
                args=argv,
            )
            raise SteamCmdError(
                f"SteamCMD failed to launch ({self.exe_path}). OS error: {e}\n"
                f"Hint: check logs in {self.root / 'logs'}",
                result,
            ) from e

        result = SteamCmdResult(
            returncode=int(completed.returncode),
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            args=argv,
        )

        if result.returncode != 0:
            combined = (result.stdout + "\n" + result.stderr).strip()
            raise SteamCmdError(
                f"SteamCMD failed (exit {result.returncode}). Output:\n{combined}\n"
                f"Hint: check logs in {self.root / 'logs'}",
                result,
            )

        return result

    def app_update(
        self,
        app_id: int,
        install_dir: str,
        login_user: str = "anonymous",
        login_pass: str | None = None,
        beta: str | None = None,
        validate: bool = True,
        timeout_seconds: int | None = None,
    ) -> SteamCmdResult:
        """
        Install/update a Steam dedicated server app into install_dir.
        Uses shared SteamCMD home for its own state.
        """
        # Normalize install dir to an absolute path (SteamCMD behaves better this way)
        install_dir_abs = str(Path(install_dir).expanduser().resolve())

        cmd: list[str] = [
            "+@ShutdownOnFailedCommand", "1",
            "+@NoPromptForPassword", "1",
            "+force_install_dir", install_dir_abs,
            "+login", login_user,
        ]

        if login_user != "anonymous":
            if not login_pass:
                raise ValueError("SteamCMD login_pass is required for non-anonymous logins.")
            cmd.append(login_pass)

        cmd += ["+app_update", str(int(app_id))]

        if beta:
            cmd += ["-beta", beta]

        if validate:
            cmd.append("validate")

        cmd += ["+quit"]

        return self.run(cmd, timeout_seconds=timeout_seconds)
