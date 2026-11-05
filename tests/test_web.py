from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cfeauto.config import Settings, load_portal
from cfeauto.db import Database
from cfeauto.storage import store_file
from cfeauto.web.app import create_app

AUTH = ("cabinet", "secret-de-test")
BASE_URL = "http://127.0.0.1"
SAME_ORIGIN = {"origin": BASE_URL}


@pytest.fixture
def setup(tmp_path):
    settings = Settings(data_dir=tmp_path, accounts_file=tmp_path / "a", portal_file=tmp_path / "p",
                        web_user=AUTH[0], web_password=AUTH[1])
    db = Database(settings.db_path)
    client = TestClient(create_app(settings, db), base_url=BASE_URL, follow_redirects=False)
    client.auth = AUTH
    return settings, db, client


def add_pdf(settings, db, path=None) -> int:
    stored = store_file(settings.documents_dir, "123456789", 2026, "cfe", ".pdf", b"%PDF-1.4 test")
    return db.add_document(siren="123456789", dossier_name="Société <Test>", kind="cfe", year=2026, title="Avis 2026",
                           path=path or stored.relative_path, sha256=stored.sha256, source_key="k")


def test_sans_mot_de_passe_acces_local_seulement(tmp_path):
    settings = Settings(data_dir=tmp_path, accounts_file=tmp_path / "a", portal_file=tmp_path / "p")
    app = create_app(settings)
    assert TestClient(app, base_url=BASE_URL).get("/").status_code == 200
    assert TestClient(app, base_url="http://localhost:8000").get("/").status_code == 200
    assert TestClient(app, base_url="http://site-malveillant.example").get("/").status_code == 400


@pytest.mark.parametrize("url", ["/", "/runs/1", "/dossiers/123456789", "/messages/1", "/documents/1/download"])
def test_authentification_obligatoire(setup, url):
    settings, db, _ = setup
    client = TestClient(create_app(settings, db), base_url=BASE_URL)
    assert client.get(url).status_code == 401
    assert client.get(url, auth=("cabinet", "faux")).status_code == 401


def test_tableau_de_bord_et_telechargement(setup):
    settings, db, client = setup
    document_id = add_pdf(settings, db)
    page = client.get("/")
    assert page.status_code == 200
    assert "Société &lt;Test&gt;" in page.text
    assert "1 nouv." in page.text

    download = client.get(f"/documents/{document_id}/download")
    assert download.content == b"%PDF-1.4 test"
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["x-content-type-options"] == "nosniff"
    assert "1 nouv." not in client.get("/").text


def test_chemin_hors_du_dossier_refuse(setup, tmp_path):
    settings, db, client = setup
    (tmp_path / "secret.pdf").write_bytes(b"secret")
    document_id = add_pdf(settings, db, path="../secret.pdf")
    assert client.get(f"/documents/{document_id}/download").status_code == 404


def test_post_d_un_autre_site_refuse(setup):
    _, db, client = setup
    assert client.post("/runs", headers={"origin": "https://autre.example"}).status_code == 403
    assert client.post("/runs").status_code == 403
    assert db.recent_runs() == []


def test_politique_referrer_compatible_avec_les_formulaires(setup):
    _, _, client = setup
    assert client.get("/").headers["referrer-policy"] == "same-origin"


def test_lancement_sans_doublon(setup):
    _, db, client = setup
    assert client.post("/runs", headers=SAME_ORIGIN).status_code == 303
    assert client.post("/runs", headers=SAME_ORIGIN).status_code == 303
    assert len(db.recent_runs()) == 1


def test_message_echappe(setup):
    _, db, client = setup
    message_id = db.add_message(siren="123456789", dossier_name="S", external_id="e", received_at="",
                                subject="<b>Objet</b>", body="<script>alert(1)</script>")
    page = client.get(f"/messages/{message_id}")
    assert "<script>alert" not in page.text
    assert "&lt;script&gt;" in page.text


def test_configuration_livree_non_calibree():
    path = Path(__file__).parent.parent / "config" / "portal.yaml"
    assert load_portal(path).calibrated is False


def test_bouton_je_suis_connecte(tmp_path):
    from fastapi.testclient import TestClient

    from cfeauto.config import Settings
    from cfeauto.db import Database
    from cfeauto.web.app import create_app

    settings = Settings(data_dir=tmp_path, accounts_file=tmp_path / "a.yaml", portal_file=tmp_path / "p.yaml")
    db = Database(settings.db_path)
    run_id = db.enqueue_run("manual")
    db.claim_next_run()
    client = TestClient(create_app(settings, db), base_url="http://127.0.0.1")
    assert "Je suis connecté" in client.get(f"/runs/{run_id}").text
    response = client.post(f"/runs/{run_id}/connecte", headers={"Origin": "http://127.0.0.1"}, follow_redirects=False)
    assert response.status_code == 303
    assert db.take_login_confirmation(run_id)
    assert not db.take_login_confirmation(run_id)
    # Sans en-tête d'origine, un autre site ne peut pas confirmer à la place de la personne
    assert client.post(f"/runs/{run_id}/connecte", follow_redirects=False).status_code == 403


def test_bouton_export(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", accounts_file=tmp_path / "a", portal_file=tmp_path / "p",
                        export_dir=tmp_path / "export")
    db = Database(settings.db_path)
    stored = store_file(settings.documents_dir, "123456789", 2026, "cfe", ".pdf", b"%PDF-1 a")
    db.add_document(siren="123456789", dossier_name="SOCIETE EXEMPLE", kind="cfe", year=2026, title="Avis",
                    path=stored.relative_path, sha256=stored.sha256, source_key="k")
    client = TestClient(create_app(settings, db), base_url=BASE_URL)
    assert "Exporter tous les avis" in client.get("/").text
    response = client.post("/export", headers=SAME_ORIGIN)
    assert "1 fichier(s) copié(s)" in response.text
    assert (tmp_path / "export" / "SOCIETE EXEMPLE - 123456789" / "123456789_CFE_2026.pdf").is_file()
    assert "tout était déjà à jour" in client.post("/export", headers=SAME_ORIGIN).text
    assert client.post("/export", follow_redirects=False).status_code == 403
