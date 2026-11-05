"""Collecte d'un dossier : avis CFE, attestations et messagerie."""

import re
from datetime import datetime

from playwright.sync_api import Error as PlaywrightError

from .config import Dossier, DocumentConfig, Settings
from .db import Database
from .errors import PortalChanged, SessionExpired
from .session import PortalSession
from .storage import attachment_extension, require_pdf, sha256_of, store_file


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _year_in(text: str) -> int:
    found = re.search(r"\b(20\d{2})\b", text)
    return int(found.group(1)) if found else datetime.now().year


class DossierCollector:
    def __init__(self, session: PortalSession, dossier: Dossier, db: Database, settings: Settings):
        self.session = session
        self.dossier = dossier
        self.db = db
        self.settings = settings
        self.variables = {"siren": dossier.siren}

    def _open(self, steps) -> None:
        self.session.run_steps(self.session.portal.select_dossier, self.variables)
        self.session.run_steps(steps, self.variables)
        # Sans ce contrôle, la page de connexion passerait pour une liste vide
        if not self.session.is_logged_in():
            raise SessionExpired("Session expirée")

    def collect_documents(self, kind: str, config: DocumentConfig) -> int:
        siren = self.dossier.siren
        if config.min_interval_days and self.db.has_recent_document(siren, kind, config.min_interval_days):
            return 0
        self._open(config.steps)
        page = self.session.page
        items = page.locator(config.item_selector)
        added = 0
        try:
            labels = [_normalize(items.nth(index).inner_text()) for index in range(items.count())]
            latest = max((_year_in(label) for label in labels), default=0)
            for index, label in enumerate(labels):
                if config.only_latest_year and _year_in(label) != latest:
                    continue
                item = items.nth(index)
                source_key = sha256_of(label.encode())
                if config.dedupe_by_label and self.db.has_source(siren, kind, source_key):
                    continue
                target = item.locator(config.download_selector).first if config.download_selector else item
                content, _ = self.session.fetch(target)
                require_pdf(content)
                if self.db.has_sha(siren, kind, sha256_of(content)):
                    continue
                year = _year_in(label)
                stored = store_file(self.settings.documents_dir, siren, year, kind, ".pdf", content)
                self.db.add_document(
                    siren=siren, dossier_name=self.dossier.name, kind=kind, year=year,
                    title=label[:200] or kind, path=stored.relative_path,
                    sha256=stored.sha256, source_key=source_key,
                )
                added += 1
        except PlaywrightError as exc:
            raise PortalChanged(f"Liste « {kind} » non reconnue") from exc
        return added

    def collect_messages(self) -> int:
        """Ouvrir un message peut le marquer comme lu sur le site : seuls les nouveaux sont ouverts."""
        config = self.session.portal.messages
        siren = self.dossier.siren
        self._open(config.steps)
        page = self.session.page
        added = 0
        try:
            count = min(page.locator(config.row_selector).count(), config.max_per_dossier)
            for index in range(count):
                row = page.locator(config.row_selector).nth(index)
                row_text = _normalize(row.inner_text())
                external_id = sha256_of(row_text.encode())
                if self.db.has_message(siren, external_id):
                    continue
                row.locator(config.link_selector).first.click()
                page.wait_for_load_state()
                self._save_open_message(external_id, row_text)
                added += 1
                self._open(config.steps)
        except PlaywrightError as exc:
            raise PortalChanged("Messagerie non reconnue") from exc
        return added

    def _save_open_message(self, external_id: str, row_text: str) -> None:
        config = self.session.portal.messages
        page = self.session.page
        siren = self.dossier.siren
        subject = self._text_of(config.subject_selector) or row_text[:200]
        received_at = self._text_of(config.date_selector)
        body = page.locator(config.body_selector).first.inner_text()

        # Pièces jointes d'abord : le message n'est indexé qu'une fois complet, sinon il sera repris
        attachments = []
        if config.attachment_selector:
            links = page.locator(config.attachment_selector)
            for index in range(links.count()):
                content, filename = self.session.fetch(links.nth(index))
                extension = attachment_extension(filename)
                if extension == ".pdf":
                    require_pdf(content)
                attachments.append((filename, extension, content))

        message_id = self.db.add_message(
            siren=siren, dossier_name=self.dossier.name, external_id=external_id,
            received_at=received_at, subject=subject, body=body,
        )
        for filename, extension, content in attachments:
            if self.db.has_sha(siren, "piece_jointe", sha256_of(content)):
                continue
            year = _year_in(received_at)
            stored = store_file(self.settings.documents_dir, siren, year, "piece_jointe", extension, content)
            self.db.add_document(
                siren=siren, dossier_name=self.dossier.name, kind="piece_jointe", year=year,
                title=filename[:200], path=stored.relative_path, sha256=stored.sha256,
                source_key=external_id, message_id=message_id,
            )

    def _text_of(self, selector: str) -> str:
        locator = self.session.page.locator(selector).first
        return _normalize(locator.inner_text()) if locator.count() else ""
