"""Export des avis avec des noms lisibles."""

from cfeauto.config import Settings
from cfeauto.db import Database
from cfeauto.export import export_documents, safe_name
from cfeauto.storage import store_file


def _add(db, settings, siren, name, year, content, kind="cfe"):
    stored = store_file(settings.documents_dir, siren, year, kind, ".pdf", content)
    db.add_document(siren=siren, dossier_name=name, kind=kind, year=year, title=f"Avis {year}",
                    path=stored.relative_path, sha256=stored.sha256, source_key=content.hex())


def _env(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", accounts_file=tmp_path / "a", portal_file=tmp_path / "p",
                        export_dir=tmp_path / "export")
    return settings, Database(settings.db_path)


def test_noms_lisibles_ranges_par_client(tmp_path):
    settings, db = _env(tmp_path)
    _add(db, settings, "123456789", "SOCIETE EXEMPLE", 2026, b"%PDF-1 a")
    _add(db, settings, "123456789", "SOCIETE EXEMPLE", 2025, b"%PDF-1 b")
    # Deux avis la même année : rôle général et rôle supplémentaire
    _add(db, settings, "123456789", "SOCIETE EXEMPLE", 2026, b"%PDF-1 c")
    _add(db, settings, "987654321", "AUTRE SOCIETE", 2026, b"%PDF-1 d")
    _add(db, settings, "987654321", "AUTRE SOCIETE", 2026, b"%PDF-1 pj", kind="piece_jointe")

    assert export_documents(db, settings.documents_dir, settings.export_dir) == 4
    blq = settings.export_dir / "SOCIETE EXEMPLE - 123456789"
    assert sorted(p.name for p in blq.iterdir()) == [
        "123456789_CFE_2025.pdf", "123456789_CFE_2026.pdf", "123456789_CFE_2026_2.pdf"]
    assert (blq / "123456789_CFE_2026.pdf").read_bytes() == b"%PDF-1 a"
    assert [p.name for p in (settings.export_dir / "AUTRE SOCIETE - 987654321").iterdir()] == [
        "987654321_CFE_2026.pdf"]


def test_export_relance_sans_doublon_et_restaure_un_fichier_supprime(tmp_path):
    settings, db = _env(tmp_path)
    _add(db, settings, "123456789", "SOCIETE EXEMPLE", 2026, b"%PDF-1 a")
    assert export_documents(db, settings.documents_dir, settings.export_dir) == 1
    assert export_documents(db, settings.documents_dir, settings.export_dir) == 0
    exported = settings.export_dir / "SOCIETE EXEMPLE - 123456789" / "123456789_CFE_2026.pdf"
    exported.unlink()
    assert export_documents(db, settings.documents_dir, settings.export_dir, "123456789") == 1
    assert exported.read_bytes() == b"%PDF-1 a"


def test_nom_de_dossier_compatible_windows():
    assert safe_name('A/B: "C"* <D>?') == "A B C D"
    assert safe_name("SOCIETE.  ") == "SOCIETE"
    assert safe_name("CON") == "_"
    assert len(safe_name("X" * 200)) == 80
