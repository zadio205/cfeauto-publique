"""Déroulé d'une collecte, et boucle du robot."""

import time
from collections.abc import Callable
from datetime import datetime

from playwright.sync_api import Error as PlaywrightError

from .collectors import DossierCollector
from .config import Account, PortalConfig, Settings
from .db import Database
from .errors import AccountStop, CfeautoError, ConfigError, PortalChanged
from .export import export_documents
from .session import PortalSession


def execute_run(run_id: int, db: Database, settings: Settings, accounts: list[Account], portal: PortalConfig,
                account_id: str | None = None) -> str:
    if not portal.calibrated:
        db.log(run_id, "error", "portal.yaml n'est pas calibré : collecte refusée")
        db.finish_run(run_id, "failed")
        return "failed"

    selected = [account for account in accounts if account_id in (None, account.id)]
    if not selected:
        db.log(run_id, "error", "Aucun compte à traiter")
        db.finish_run(run_id, "failed")
        return "failed"

    failures = successes = 0
    for account in selected:
        try:
            ok, ko = _run_account(run_id, account, db, settings, portal)
        except (CfeautoError, PlaywrightError) as exc:
            # Une erreur Playwright brute peut citer des valeurs de page : seul son type est journalisé
            message = str(exc) if isinstance(exc, CfeautoError) else f"Erreur navigateur ({type(exc).__name__})"
            db.log(run_id, "error", message, account_id=account.id)
            ok, ko = 0, 1
        successes += ok
        failures += ko

    status = "success" if not failures else ("partial" if successes else "failed")
    db.finish_run(run_id, status)
    return status


def _attempt(session: PortalSession, task: Callable[[], int]) -> int:
    """Une session expirée en cours de route : on redemande la connexion et on reprend une fois."""
    try:
        return task()
    except AccountStop:
        raise
    except CfeautoError:
        if session.is_logged_in():
            raise
        session.ensure_logged_in()
        return task()


def _run_account(run_id: int, account: Account, db: Database, settings: Settings,
                 portal: PortalConfig) -> tuple[int, int]:
    successes = failures = 0

    def notify(message: str) -> None:
        db.log(run_id, "action", message, account_id=account.id)

    with PortalSession(account, portal, settings, notify,
                       login_confirmed=lambda: db.take_login_confirmation(run_id)) as session:
        session.ensure_logged_in()
        db.log(run_id, "info", "Connexion établie", account_id=account.id)
        for dossier in account.dossiers:
            collector = DossierCollector(session, dossier, db, settings)
            tasks = [("avis CFE", lambda: collector.collect_documents("cfe", portal.cfe))]
            if portal.attestation is not None:
                tasks.append(("attestations", lambda: collector.collect_documents("attestation", portal.attestation)))
            if portal.messages is not None:
                tasks.append(("messages", collector.collect_messages))
            # Un échec sur une rubrique n'empêche pas de collecter les autres
            for label, task in tasks:
                try:
                    added = _attempt(session, task)
                except AccountStop:
                    raise
                except CfeautoError as exc:
                    failures += 1
                    if isinstance(exc, PortalChanged):
                        session.save_diagnostic(dossier.siren)
                    db.log(run_id, "error", f"{label} : {exc}", account_id=account.id, siren=dossier.siren)
                else:
                    successes += 1
                    db.log(run_id, "info", f"{label} : {added} nouveau(x)", account_id=account.id, siren=dossier.siren)
            _export(run_id, db, settings, account.id, dossier.siren)
    return successes, failures


def _export(run_id: int, db: Database, settings: Settings, account_id: str, siren: str) -> None:
    """Un export impossible (dossier absent, fichier ouvert…) ne fait pas échouer la collecte."""
    if settings.export_dir is None:
        return
    try:
        copied = export_documents(db, settings.documents_dir, settings.export_dir, siren)
    except OSError as exc:
        db.log(run_id, "error", f"export impossible vers {settings.export_dir} ({type(exc).__name__})",
               account_id=account_id, siren=siren)
        return
    if copied:
        db.log(run_id, "info", f"export : {copied} fichier(s) copié(s) dans {settings.export_dir}",
               account_id=account_id, siren=siren)


def worker_loop(db: Database, settings: Settings, accounts: list[Account], portal: PortalConfig,
                poll_seconds: float = 5.0) -> None:
    db.fail_interrupted_runs()
    while True:
        if (settings.schedule_hour is not None
                and datetime.now().hour >= settings.schedule_hour
                and not db.has_scheduled_run_today()):
            db.enqueue_run("schedule")
        run = db.claim_next_run()
        if run is not None:
            try:
                execute_run(run["id"], db, settings, accounts, portal, run["account_id"])
            except ConfigError as exc:
                db.log(run["id"], "error", str(exc))
                db.finish_run(run["id"], "failed")
            except Exception as exc:
                # La boucle tourne dans l'application : elle doit survivre à une erreur imprévue
                db.log(run["id"], "error", f"Erreur inattendue ({type(exc).__name__})")
                db.finish_run(run["id"], "failed")
        time.sleep(poll_seconds)
