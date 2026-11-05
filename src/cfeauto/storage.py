"""Écriture des fichiers collectés : validation, nommage et droits restreints."""

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import InvalidDocument
from .securite import make_private_dir

ATTACHMENT_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".txt", ".csv", ".doc", ".docx", ".xls", ".xlsx", ".odt", ".ods"}


@dataclass(frozen=True)
class StoredFile:
    relative_path: str
    sha256: str


def sha256_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def require_pdf(content: bytes) -> None:
    if not content.startswith(b"%PDF-"):
        raise InvalidDocument("Le fichier reçu n'est pas un PDF (session expirée ou page d'erreur ?)")


def attachment_extension(filename: str) -> str:
    extension = PurePosixPath(filename).suffix.lower()
    if extension not in ATTACHMENT_EXTENSIONS:
        raise InvalidDocument(f"Type de pièce jointe refusé : {extension or 'sans extension'}")
    return extension


def store_file(root: Path, siren: str, year: int, kind: str, extension: str, content: bytes) -> StoredFile:
    """Le nom vient de l'empreinte, jamais du site, et l'écriture est atomique."""
    digest = sha256_of(content)
    relative = Path(siren) / str(year) / kind / f"{digest[:16]}{extension}"
    target = root / relative
    make_private_dir(target.parent)
    descriptor, temporary = tempfile.mkstemp(dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return StoredFile(relative_path=relative.as_posix(), sha256=digest)
