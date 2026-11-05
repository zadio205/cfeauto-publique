"""Copie des avis collectés vers un dossier lisible, rangé par client.

Les fichiers de data/documents portent un nom technique (leur empreinte) et ne doivent pas être
touchés. L'export en fait des copies nommées pour un humain :

    export/SOCIETE EXEMPLE - 123456789/123456789_CFE_2026.pdf

L'export peut être relancé sans risque : un fichier déjà exporté à l'identique n'est pas recopié,
et un fichier modifié ou supprimé à la main dans export n'altère jamais les originaux.
"""

import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path

from .db import Database

# Seuls les documents officiels sont exportés, pas les pièces jointes de la messagerie
EXPORT_LABELS = {"cfe": "CFE", "attestation": "ATTESTATION"}

# Caractères interdits dans un nom de fichier Windows, et noms réservés
FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_name(text: str) -> str:
    cleaned = " ".join(FORBIDDEN.sub(" ", text).split()).rstrip(". ")[:80].rstrip(". ")
    if not cleaned or cleaned.upper() in RESERVED:
        return "_"
    return cleaned


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export_documents(db: Database, documents_dir: Path, export_dir: Path, siren: str | None = None) -> int:
    """Rend le nombre de fichiers copiés ; ceux déjà présents à l'identique sont ignorés."""
    copied = 0
    for siren_value in [siren] if siren else db.exported_sirens():
        documents = [d for d in db.documents_for(siren_value) if d["kind"] in EXPORT_LABELS]
        # Ordre stable : le premier avis d'une année garde le nom sans suffixe
        documents.sort(key=lambda d: d["id"])
        used: set[str] = set()
        for document in documents:
            source = documents_dir / document["path"]
            if not source.is_file():
                continue
            folder = export_dir / safe_name(f"{document['dossier_name']} - {document['siren']}")
            base = f"{document['siren']}_{EXPORT_LABELS[document['kind']]}_{document['year']}"
            name, number = f"{base}.pdf", 1
            while name in used:
                number += 1
                name = f"{base}_{number}.pdf"
            used.add(name)
            target = folder / name
            if target.is_file() and _sha256(target) == document["sha256"]:
                continue
            folder.mkdir(parents=True, exist_ok=True)
            _copy_atomic(source, target)
            copied += 1
    return copied


def _copy_atomic(source: Path, target: Path) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    os.close(descriptor)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
