from pathlib import Path

import pytest

from cfeauto import securite
from cfeauto.errors import ConfigError


@pytest.mark.parametrize("path", ["/Users/a/OneDrive - Cabinet/cfeauto/data", "/Users/a/Dropbox/data",
                                  "/Users/a/Google Drive/data", "/Users/a/Library/Mobile Documents/data"])
def test_dossier_synchronise_refuse(path):
    with pytest.raises(ConfigError):
        securite.check_not_synced(Path(path))


def test_dossier_ordinaire_accepte(tmp_path):
    securite.prepare_data_dir(tmp_path / "data")
    assert (tmp_path / "data").is_dir()


def test_windows_autorisations_reservees_a_la_session(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(securite.sys, "platform", "win32")
    monkeypatch.setenv("USERDOMAIN", "CABINET")
    monkeypatch.setattr(securite.getpass, "getuser", lambda: "marie")
    monkeypatch.setattr(securite.subprocess, "run", lambda command, **_: commands.append(command))
    securite.restrict_to_current_user(tmp_path)
    assert commands[0][:3] == ["icacls", str(tmp_path), "/inheritance:r"]
    assert "CABINET\\marie:(OI)(CI)F" in commands[0]
    # Les administrateurs posés par mkdir(mode=0o700) depuis Python 3.13 sont retirés
    assert commands[1][:3] == ["icacls", str(tmp_path), "/remove:g"]
    assert "*S-1-5-32-544" in commands[1]


def test_windows_sous_dossier_herite_des_droits_de_data(tmp_path, monkeypatch):
    modes = []
    monkeypatch.setattr(securite.sys, "platform", "win32")
    monkeypatch.setattr(Path, "mkdir", lambda self, **kwargs: modes.append(kwargs.get("mode")))
    securite.make_private_dir(tmp_path / "documents")
    assert modes == [None]


def test_windows_echec_bloque_le_demarrage(tmp_path, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError

    monkeypatch.setattr(securite.sys, "platform", "win32")
    monkeypatch.setattr(securite.subprocess, "run", fail)
    with pytest.raises(ConfigError):
        securite.restrict_to_current_user(tmp_path)
