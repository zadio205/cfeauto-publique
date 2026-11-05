"""Parcours CFE relevé sur le site : nouvel onglet, menu replié, avis servi par un formulaire."""

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from cfeauto.config import Account, PortalConfig, Settings, load_portal
from cfeauto.db import Database
from cfeauto.collectors import DossierCollector
from cfeauto.session import PortalSession

from fake_portal import CFE_PDF

ROOT = "https://cfspro.impots.gouv.fr"
SIREN = "123456789"
LOGOUT = '<button class="fr-btn" aria-controls="modal-logout">Me déconnecter</button>'

SEARCH = (
    f'{LOGOUT}<form action="/recherche.do"><input id="rechercheSiren" name="siren" type="text">'
    '<button class="fr-btn" type="submit" title="Rechercher">Rechercher</button></form>'
)
OTHER = "987654321"

PAGES = {
    "/mire/accueil.do": (
        # Raccourci vers le dernier dossier consulté, placé avant le menu
        f'{LOGOUT}<a href="/dossierSelect.do?idth=consulter.avis.cfe&idDossier=111111111&token=T"'
        ' target="blank_EServices">Dernier dossier</a><nav><ul style="display:none"><li>'
        '<a href="/afficherSelectionDossier.do?idth=consulter.avis.cfe&token=T" target="_self">Avis CFE</a>'
        "</li></ul></nav>"
    ),
    "/afficherSelectionDossier.do": SEARCH,
    # Les résultats s'affichent sous le formulaire de recherche, qui reste en place
    "/recherche.do": SEARCH + (
        '<a href="/dossierSelect.do?idth=consulter.avis.cfe&idDossier={siren}&token=T"'
        ' target="blank_EServices">Dossier {siren}</a>'
    ),
    # Application ouverte dans un nouvel onglet, sans bouton de déconnexion
    "/dossierSelect.do": (
        '<a id="accueilForm:boutonCFE" href="/impots/cfe/avis_cfe.xhtml">Accès aux avis de CFE</a>'
    ),
    "/impots/cfe/avis_cfe.xhtml": (
        '<table><tr><td><form id="form" method="post" action="/impots/cfe/avis.pdf">'
        '<table id="form:tableauAvisImposition"><tbody>'
        '<tr data-ri="0"><td>2026</td><td><a id="form:tableauAvisImposition:0:j_idt154" href="#"'
        ' onclick="document.getElementById(\'form\').submit();return false;">Rôle général n°092</a></td></tr>'
        # Avis d'une année passée : ignoré avec only_latest_year
        '<tr data-ri="1"><td>2025</td><td><a id="form:tableauAvisImposition:1:j_idt154" href="#"'
        ' onclick="document.getElementById(\'form\').submit();return false;">Rôle général n°092</a></td></tr>'
        "</tbody></table></form></td></tr></table>"
    ),
}


PAGES_STATE = {"dossier": ""}


@pytest.fixture
def session(tmp_path):
    settings = Settings(data_dir=tmp_path, accounts_file=tmp_path / "a.yaml", portal_file=tmp_path / "p.yaml",
                        headless=True, action_delay=0, login_timeout=10)
    portal = load_portal(Path(__file__).parent.parent / "config" / "portal.yaml")
    account = Account(id="cabinet", dossiers=[{"siren": SIREN, "name": "SOCIETE EXEMPLE"},
                                              {"siren": OTHER, "name": "AUTRE SOCIETE"}])
    with PortalSession(account, portal, settings, print) as session:
        def serve(route):
            url = urlparse(route.request.url)
            query = parse_qs(url.query)
            if url.path == "/impots/cfe/avis.pdf":
                # Un avis différent par dossier : celui choisi en dernier
                body = CFE_PDF + PAGES_STATE["dossier"].encode()
                return route.fulfill(body=body, content_type="application/pdf",
                                     headers={"Content-Disposition": 'attachment; filename="avis_cfe.pdf"'})
            if url.path == "/dossierSelect.do":
                PAGES_STATE["dossier"] = query["idDossier"][0]
            html = PAGES.get(url.path, "introuvable").replace("{siren}", query.get("siren", [""])[0])
            route.fulfill(body=f"<!doctype html><html><body>{html}</body></html>",
                          content_type="text/html; charset=utf-8")
        session.context.route(f"{ROOT}/**", serve)
        yield session, settings, account


def test_avis_cfe_de_deux_dossiers_a_la_suite(session):
    session, settings, account = session
    session.ensure_logged_in()
    db = Database(settings.db_path)
    for dossier in account.dossiers:
        collector = DossierCollector(session, dossier, db, settings)
        assert collector.collect_documents("cfe", session.portal.cfe) == 1
        documents = db.documents_for(dossier.siren)
        assert len(documents) == 1 and documents[0]["year"] == 2026
        assert (settings.documents_dir / documents[0]["path"]).read_bytes() == CFE_PDF + dossier.siren.encode()
        # Pour le dossier suivant, l'onglet de l'avis est refermé au début du parcours
    # Nouveau passage : rien de nouveau
    collector = DossierCollector(session, account.dossiers[0], db, settings)
    assert collector.collect_documents("cfe", session.portal.cfe) == 0
    assert len(session.context.pages) == 2


def test_attestation_et_messagerie_non_collectees():
    portal = load_portal(Path(__file__).parent.parent / "config" / "portal.yaml")
    assert portal.attestation is None and portal.messages is None


def test_avis_affiche_en_pdf_dans_la_page(session):
    session, settings, account = session
    session.context.unroute(f"{ROOT}/**")

    def inline(route):
        if route.request.url.endswith("/avis.pdf"):
            return route.fulfill(body=CFE_PDF, content_type="application/pdf")
        route.fulfill(body=PAGES["/impots/cfe/avis_cfe.xhtml"], content_type="text/html; charset=utf-8")
    session.context.route(f"{ROOT}/**", inline)
    session.page.goto(f"{ROOT}/impots/cfe/avis_cfe.xhtml")
    content, _ = session.fetch(session.page.locator(session.portal.cfe.download_selector).first)
    assert content == CFE_PDF
    assert session.page.url.endswith("/avis_cfe.xhtml")


@pytest.mark.parametrize("only_latest_year, expected", [(True, 1), (False, 2)])
def test_seulement_l_annee_la_plus_recente(session, only_latest_year, expected):
    session, settings, account = session
    session.ensure_logged_in()
    fetched = []
    real_fetch = session.fetch

    def counting_fetch(element):
        fetched.append(element)
        return real_fetch(element)
    session.fetch = counting_fetch
    config = session.portal.cfe.model_copy(update={"only_latest_year": only_latest_year})
    DossierCollector(session, account.dossiers[0], Database(settings.db_path), settings).collect_documents("cfe", config)
    assert len(fetched) == expected
