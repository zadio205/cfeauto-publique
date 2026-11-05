"""Protection du dossier de données, selon le système de l'ordinateur."""

import getpass
import os
import subprocess
import sys
from pathlib import Path

from .errors import ConfigError

SYNCED_FOLDERS = ("onedrive", "dropbox", "google drive", "googledrive", "icloud", "mobile documents")
WINDOWS_SYSTEM_ACCOUNT = "*S-1-5-18"
# Administrateurs, OWNER RIGHTS, Utilisateurs, Utilisateurs authentifiés, Tout le monde
WINDOWS_OTHER_ACCOUNTS = ("*S-1-5-32-544", "*S-1-3-4", "*S-1-5-32-545", "*S-1-5-11", "*S-1-1-0")


def check_not_synced(path: Path) -> None:
    """Des documents fiscaux et des sessions actives ne doivent pas partir dans un cloud personnel."""
    for part in path.resolve().parts:
        if any(name in part.lower() for name in SYNCED_FOLDERS):
            raise ConfigError(
                f"Le dossier de données {path} est dans un dossier synchronisé ({part}). "
                "Déplacez le projet, par exemple vers C:\\cfeauto."
            )


def restrict_to_current_user(path: Path) -> None:
    """Sur Windows, les droits passés à chmod n'ont pas d'effet : on pose de vraies autorisations NTFS."""
    if sys.platform != "win32":
        path.chmod(0o700)
        return
    domain = os.environ.get("USERDOMAIN")
    user = f"{domain}\\{getpass.getuser()}" if domain else getpass.getuser()
    grant = ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(OI)(CI)F",
             f"{WINDOWS_SYSTEM_ACCOUNT}:(OI)(CI)F"]
    # /grant:r ne retire pas les autres comptes déjà inscrits, par exemple par mkdir(mode=0o700)
    remove = ["icacls", str(path), "/remove:g", *WINDOWS_OTHER_ACCOUNTS]
    try:
        subprocess.run(grant, check=True, capture_output=True)
        subprocess.run(remove, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ConfigError(f"Impossible de protéger le dossier de données {path}") from exc


def make_private_dir(path: Path) -> None:
    """Sur Windows, mode=0o700 pose depuis Python 3.13 des droits protégés qui incluent les administrateurs
    et coupent l'héritage : sans lui, les sous-dossiers héritent des droits posés sur data."""
    if sys.platform == "win32":
        path.mkdir(parents=True, exist_ok=True)
    else:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)


def prepare_data_dir(path: Path) -> None:
    check_not_synced(path)
    make_private_dir(path)
    restrict_to_current_user(path)
