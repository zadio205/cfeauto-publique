"""Chargement de la configuration : environnement, comptes et description du site."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from .errors import ConfigError


class Step(BaseModel):
    # home : revient à la page d'accueil relevée juste après la connexion, et ferme les autres onglets
    # main_tab : revient dans l'onglet de connexion, tel qu'il est resté, et ferme les autres onglets
    action: Literal["home", "main_tab", "goto", "click", "fill", "select", "wait_for"]
    target: str = ""
    # Étape sautée si cet élément est déjà visible (par exemple : déjà sur la recherche de dossier)
    skip_if: str = ""
    value: str = ""
    optional: bool = False


class LoginConfig(BaseModel):
    url: str
    # Doit être visible sur toutes les pages une fois connecté : c'est ainsi qu'une session expirée est repérée
    logged_in_selector: str


class DocumentConfig(BaseModel):
    steps: list[Step]
    item_selector: str
    download_selector: str = ""
    dedupe_by_label: bool = True
    min_interval_days: int = 0
    # Ne télécharge que les documents de l'année la plus récente de la liste
    only_latest_year: bool = False


class MessagesConfig(BaseModel):
    steps: list[Step]
    row_selector: str
    link_selector: str
    subject_selector: str
    date_selector: str
    body_selector: str
    attachment_selector: str = ""
    max_per_dossier: int = 50


class PortalConfig(BaseModel):
    calibrated: bool = False
    allowed_domain: str
    login: LoginConfig
    select_dossier: list[Step] = Field(default_factory=list)
    cfe: DocumentConfig
    # Rubriques facultatives : absentes de portal.yaml, elles ne sont pas collectées
    attestation: DocumentConfig | None = None
    messages: MessagesConfig | None = None


class Dossier(BaseModel):
    siren: str
    name: str

    @field_validator("siren")
    @classmethod
    def siren_format(cls, value: str) -> str:
        if not (value.isascii() and value.isdigit() and len(value) == 9):
            raise ValueError("un SIREN comporte 9 chiffres")
        return value


class Account(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    dossiers: list[Dossier]


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    accounts_file: Path
    portal_file: Path
    headless: bool = False
    schedule_hour: int | None = None
    login_timeout: int = 600
    action_delay: float = 1.5
    web_user: str = "cabinet"
    web_password: str = ""
    # Copies des avis avec des noms lisibles ; None = pas d'export
    export_dir: Path | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cfeauto.sqlite3"

    @property
    def documents_dir(self) -> Path:
        return self.data_dir / "documents"

    @property
    def profiles_dir(self) -> Path:
        return self.data_dir / "profiles"

    @property
    def diagnostics_dir(self) -> Path:
        return self.data_dir / "diagnostics"


def _export_dir(value: str) -> Path | None:
    """Vide : export désactivé."""
    value = value.strip()
    return Path(value) if value else None


def load_settings() -> Settings:
    hour = os.environ.get("CFEAUTO_SCHEDULE_HOUR", "").strip()
    try:
        return Settings(
            data_dir=Path(os.environ.get("CFEAUTO_DATA_DIR", "./data")),
            accounts_file=Path(os.environ.get("CFEAUTO_ACCOUNTS_FILE", "./config/accounts.yaml")),
            portal_file=Path(os.environ.get("CFEAUTO_PORTAL_FILE", "./config/portal.yaml")),
            headless=os.environ.get("CFEAUTO_HEADLESS", "false").lower() == "true",
            schedule_hour=int(hour) if hour else None,
            login_timeout=int(os.environ.get("CFEAUTO_LOGIN_TIMEOUT", "600")),
            action_delay=float(os.environ.get("CFEAUTO_ACTION_DELAY", "1.5")),
            web_user=os.environ.get("CFEAUTO_WEB_USER", "cabinet"),
            web_password=os.environ.get("CFEAUTO_WEB_PASSWORD", ""),
            export_dir=_export_dir(os.environ.get("CFEAUTO_EXPORT_DIR", "./export")),
        )
    except ValueError as exc:
        raise ConfigError(f"Variable d'environnement invalide : {exc}") from exc


def _read_yaml(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ConfigError(f"Lecture impossible de {path} : {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML invalide dans {path} : {exc}") from exc


def load_accounts(path: Path) -> list[Account]:
    try:
        accounts = [Account(**raw) for raw in _read_yaml(path).get("accounts", [])]
    except (ValidationError, TypeError) as exc:
        raise ConfigError(f"Comptes invalides dans {path} : {exc}") from exc
    ids = [account.id for account in accounts]
    if len(ids) != len(set(ids)):
        raise ConfigError(f"Identifiant de compte en double dans {path}")
    return accounts


def load_portal(path: Path) -> PortalConfig:
    try:
        return PortalConfig(**_read_yaml(path))
    except (ValidationError, TypeError) as exc:
        raise ConfigError(f"Description du site invalide dans {path} : {exc}") from exc
