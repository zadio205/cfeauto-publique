"""Détection de la connexion : nouvel onglet, cadre, sélecteur livré."""

from pathlib import Path

import pytest

from cfeauto.config import Account, PortalConfig, Settings, load_portal
from cfeauto.session import PortalSession

from fake_portal import portal_config

SELECTOR = load_portal(Path(__file__).parent.parent / "config" / "portal.yaml").login.logged_in_selector


@pytest.fixture
def session(tmp_path):
    settings = Settings(data_dir=tmp_path, accounts_file=tmp_path / "a.yaml", portal_file=tmp_path / "p.yaml",
                        headless=True, action_delay=0)
    config = portal_config("http://127.0.0.1:1")
    config["login"]["logged_in_selector"] = SELECTOR
    with PortalSession(Account(id="cabinet", dossiers=[]), PortalConfig(**config), settings, print) as session:
        yield session


@pytest.mark.parametrize("html", [
    '<button class="fr-btn" aria-controls="modal-logout"><svg aria-hidden="true"></svg> Me déconnecter</button>',
    '<button class="fr-btn" aria-controls="modal-logout">X</button>',
    '<a href="#">Déconnexion</a>',
    "<button>Se déconnecter</button>",
    "<a>SE DECONNECTER</a>",
])
def test_selecteur_livre_reconnait_la_deconnexion(session, html):
    session.page.set_content(html)
    assert session.is_logged_in()


def test_selecteur_livre_ignore_la_page_de_connexion(session):
    session.page.set_content('<h1>Connexion</h1><a title="Se connecter">Se connecter</a><button>Connexion</button>')
    assert not session.is_logged_in()


def test_connexion_dans_un_nouvel_onglet(session):
    first = session.page
    first.set_content("<h1>Connexion</h1>")
    other = session.context.new_page()
    other.set_content("<a>Déconnexion</a>")
    assert session._wait_logged_in(timeout=3)
    assert session.page is other


def test_connexion_dans_un_cadre(session):
    session.page.set_content('<iframe srcdoc="<a>Déconnexion</a>"></iframe>')
    session.page.wait_for_function("document.querySelector('iframe').contentDocument?.querySelector('a')")
    assert session.is_logged_in()


def test_confirmation_manuelle_et_releve_des_boutons(tmp_path):
    settings = Settings(data_dir=tmp_path, accounts_file=tmp_path / "a.yaml", portal_file=tmp_path / "p.yaml",
                        headless=True, action_delay=0, login_timeout=10)
    config = portal_config("http://127.0.0.1:1")
    config["login"]["logged_in_selector"] = "#introuvable"
    notes: list[str] = []
    clicks = iter([False, False, True])
    with PortalSession(Account(id="cabinet", dossiers=[]), PortalConfig(**config), settings, notes.append,
                       login_confirmed=lambda: next(clicks, False)) as session:
        session.page.set_content('<a class="btn-logout" href="/x?jeton=secret">Quitter l\'espace</a>')
        assert session._wait_logged_in(timeout=10)
        assert session.manual_login and session.is_logged_in()
        assert any('class="btn-logout"' in note for note in notes)
        assert not any("secret" in note for note in notes)
        # Le site redemande le mot de passe : la session n'est plus tenue pour ouverte
        session.page.set_content('<input type="password">')
        assert not session.is_logged_in()
