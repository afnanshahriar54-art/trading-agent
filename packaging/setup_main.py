"""TradingAgentSetup.exe: asks, then installs Trading Agent for the current user.

Bundled by packaging/build.ps1 with the app (TradingAgent.zip) and install.ps1 inside.
"""
import ctypes
import subprocess
import sys
from pathlib import Path

MB_OKCANCEL, MB_ICONQUESTION, MB_ICONERROR = 0x1, 0x20, 0x10
IDOK = 1


def message(text: str, flags: int) -> int:
    return ctypes.windll.user32.MessageBoxW(None, text, "Trading Agent Setup", flags)


def main() -> None:
    payload = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    if message("Install Trading Agent on this computer?\n\n"
               "It uses practice money only. It installs for your Windows user and "
               "adds icons to your Desktop and Start menu.", MB_OKCANCEL | MB_ICONQUESTION) != IDOK:
        return
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
         "-File", str(payload / "install.ps1")],
        creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True, text=True)
    if result.returncode != 0:  # install.ps1 shows its own error box; this covers PowerShell itself failing
        message("Installation did not finish.\n\n" + (result.stderr or result.stdout)[-800:], MB_ICONERROR)


if __name__ == "__main__":
    main()
