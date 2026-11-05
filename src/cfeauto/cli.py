"""Ligne de commande : start, check, run, calibrate."""

import argparse
import asyncio
import socket
import sys
import threading
import webbrowser

from dotenv import load_dotenv

from .config import load_accounts, load_portal, load_settings
from .db import Database
from .errors import CfeautoError
from .export import export_documents
from .runner import execute_run, worker_loop
from .securite import make_private_dir, prepare_data_dir

HOST = "127.0.0.1"


def _print_check(settings) -> int:
    accounts = load_accounts(settings.accounts_file)
    portal = load_portal(settings.portal_file)
    dossiers = sum(len(account.dossiers) for account in accounts)
    print(f"{len(accounts)} compte(s), {dossiers} dossier(s)")
    prepare_data_dir(settings.data_dir)
    print(f"Dossier de données protégé : {settings.data_dir.resolve()}")
    print("Interface protégée par mot de passe" if settings.web_password else "Interface sans mot de passe")
    if settings.export_dir is None:
        print("Export des avis désactivé (CFEAUTO_EXPORT_DIR vide)")
    else:
        print(f"Export des avis vers : {settings.export_dir.resolve()}")
    if not portal.calibrated:
        print("portal.yaml n'est pas calibré : la collecte sera refusée")
        return 1
    print("portal.yaml calibré")
    return 0


def _calibrate(settings, account_id: str) -> int:
    """Ouvre un navigateur visible et l'inspecteur Playwright."""
    from playwright.sync_api import sync_playwright

    portal = load_portal(settings.portal_file)
    prepare_data_dir(settings.data_dir)
    profile = settings.profiles_dir / account_id
    make_private_dir(profile)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(str(profile), headless=False, locale="fr-FR")
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(portal.login.url)
        print("Connectez-vous dans la fenêtre, puis relevez les sélecteurs avec l'inspecteur.")
        page.pause()
        context.close()
    return 0


def _port_in_use(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex((HOST, port)) == 0


def _start(settings, port: int, open_browser: bool) -> int:
    """Lance dans un seul processus l'interface et le robot, uniquement sur cet ordinateur."""
    import uvicorn

    from .web.app import create_app

    url = f"http://{HOST}:{port}"
    if _port_in_use(port):
        # Déjà ouverte : ne pas relancer le robot, qui marquerait la collecte en cours comme échouée
        print(f"L'application est déjà ouverte : {url}")
        if open_browser:
            webbrowser.open(url)
        return 0

    if sys.platform == "win32":
        # Playwright lance Chromium en sous-processus, ce que seule cette boucle permet sur Windows
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    prepare_data_dir(settings.data_dir)
    db = Database(settings.db_path)
    accounts = load_accounts(settings.accounts_file)
    portal = load_portal(settings.portal_file)
    threading.Thread(target=worker_loop, args=(db, settings, accounts, portal), daemon=True).start()
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    print(f"Interface : {url} (gardez cette fenêtre ouverte, Ctrl+C pour arrêter)")
    uvicorn.run(create_app(settings, db), host=HOST, port=port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cfeauto")
    commands = parser.add_subparsers(dest="command", required=True)
    start_parser = commands.add_parser("start", help="ouvre l'application : interface et robot")
    start_parser.add_argument("--port", type=int, default=8000)
    start_parser.add_argument("--no-browser", action="store_true")
    commands.add_parser("check", help="vérifie la configuration")
    run_parser = commands.add_parser("run", help="lance une collecte dans ce terminal")
    run_parser.add_argument("--account")
    commands.add_parser("export", help="copie les avis déjà collectés avec des noms lisibles")
    calibrate_parser = commands.add_parser("calibrate", help="ouvre le site pour relever les sélecteurs")
    calibrate_parser.add_argument("--account", required=True)
    args = parser.parse_args(argv)

    load_dotenv(".env")
    try:
        settings = load_settings()
        if args.command == "start":
            return _start(settings, args.port, not args.no_browser)
        if args.command == "check":
            return _print_check(settings)
        if args.command == "calibrate":
            return _calibrate(settings, args.account)
        if args.command == "export":
            if settings.export_dir is None:
                print("Export désactivé : renseignez CFEAUTO_EXPORT_DIR dans .env", file=sys.stderr)
                return 1
            prepare_data_dir(settings.data_dir)
            copied = export_documents(Database(settings.db_path), settings.documents_dir, settings.export_dir)
            print(f"{copied} fichier(s) copié(s) dans {settings.export_dir.resolve()}")
            return 0

        prepare_data_dir(settings.data_dir)
        db = Database(settings.db_path)
        accounts = load_accounts(settings.accounts_file)
        portal = load_portal(settings.portal_file)
        run_id = db.enqueue_run("cli", args.account)
        run = db.claim_next_run()
        if run is None or run["id"] != run_id:
            print("Une collecte est déjà en attente ou en cours", file=sys.stderr)
            return 1
        status = execute_run(run_id, db, settings, accounts, portal, args.account)
        for event in db.run_events(run_id):
            print(f"[{event['level']}] {event['account_id'] or '-'} {event['siren'] or '-'} {event['message']}")
        print(f"Collecte {run_id} : {status}")
        return 0 if status == "success" else 1
    except CfeautoError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
