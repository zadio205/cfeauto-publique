"""Parcours complet avec un vrai navigateur, contre le faux portail."""

import sys

import pytest

from cfeauto.config import Account, PortalConfig, Settings
from cfeauto.db import Database
from cfeauto.runner import execute_run

from fake_portal import ATTESTATION_PDF, CFE_PDF, SIREN, FakePortal, portal_config


@pytest.fixture
def portal_server():
    server = FakePortal()
    server.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def env(tmp_path, portal_server):
    settings = Settings(data_dir=tmp_path, accounts_file=tmp_path / "a.yaml", portal_file=tmp_path / "p.yaml",
                        headless=True, action_delay=0, login_timeout=20)
    db = Database(settings.db_path)
    account = Account(id="cabinet", dossiers=[{"siren": SIREN, "name": "Société Test"}])
    portal = PortalConfig(**portal_config(portal_server.url))
    return settings, db, account, portal


def run(db, settings, account, portal):
    run_id = db.enqueue_run("test")
    db.claim_next_run()
    return run_id, execute_run(run_id, db, settings, [account], portal)


def test_collecte_complete_puis_sans_doublon(env, portal_server):
    settings, db, account, portal = env
    run_id, status = run(db, settings, account, portal)
    assert status == "success", [e["message"] for e in db.run_events(run_id)]

    documents = db.documents_for(SIREN)
    assert {d["kind"] for d in documents} == {"cfe", "attestation", "piece_jointe"}
    cfe = next(d for d in documents if d["kind"] == "cfe")
    assert cfe["year"] == 2026
    assert (settings.documents_dir / cfe["path"]).read_bytes() == CFE_PDF
    attestation = next(d for d in documents if d["kind"] == "attestation")
    assert (settings.documents_dir / attestation["path"]).read_bytes() == ATTESTATION_PDF
    if sys.platform != "win32":
        assert (settings.documents_dir / cfe["path"]).stat().st_mode & 0o077 == 0

    messages = db.messages_for(SIREN)
    assert len(messages) == 1 and messages[0]["subject"] == "Relance CFE"
    assert "Merci de régler" in messages[0]["body"]

    # Seconde collecte : appareil reconnu, aucun code demandé, rien de nouveau
    _, status = run(db, settings, account, portal)
    assert status == "success"
    assert portal_server.otp_prompts == 1
    assert portal_server.message_opens == 1
    assert len(db.documents_for(SIREN)) == len(documents)


def test_le_robot_attend_la_personne_sans_rien_saisir(env, portal_server):
    settings, db, account, portal = env
    portal_server.human_present = False
    settings = Settings(**{**settings.__dict__, "login_timeout": 1})
    run_id, status = run(db, settings, account, portal)
    assert status == "failed"
    assert portal_server.logins == 0
    assert any(e["level"] == "action" and "Connexion requise" in e["message"] for e in db.run_events(run_id))
    assert any("non effectuée à temps" in e["message"] for e in db.run_events(run_id))


def test_session_expiree_en_cours_redemande_la_connexion_et_reprend(env, portal_server):
    settings, db, account, portal = env
    portal_server.expire_once = True
    run_id, status = run(db, settings, account, portal)
    assert status == "success", [e["message"] for e in db.run_events(run_id)]
    assert portal_server.logins == 2
    assert [d for d in db.documents_for(SIREN) if d["kind"] == "cfe"]


def test_page_html_refusee_a_la_place_du_pdf(env, portal_server):
    settings, db, account, portal = env
    portal_server.expired_pdf = True
    run_id, status = run(db, settings, account, portal)
    assert status == "partial"
    assert not [d for d in db.documents_for(SIREN) if d["kind"] == "cfe"]
    assert any("pas un PDF" in e["message"] for e in db.run_events(run_id))


def test_portail_non_calibre_refuse(env):
    settings, db, account, portal = env
    run_id, status = run(db, settings, account, portal.model_copy(update={"calibrated": False}))
    assert status == "failed"
    assert any("calibré" in e["message"] for e in db.run_events(run_id))


def test_export_automatique_apres_collecte(env, tmp_path):
    from dataclasses import replace

    settings, db, account, portal = env
    settings = replace(settings, export_dir=tmp_path / "export")
    run_id, status = run(db, settings, account, portal)
    assert status == "success"
    folder = tmp_path / "export" / f"Société Test - {SIREN}"
    assert (folder / f"{SIREN}_CFE_2026.pdf").read_bytes() == CFE_PDF
    assert (folder / f"{SIREN}_ATTESTATION_2026.pdf").read_bytes() == ATTESTATION_PDF
    assert any("export : 2 fichier(s)" in e["message"] for e in db.run_events(run_id))
