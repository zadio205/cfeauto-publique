"""Interface web : consultation des documents et lancement des collectes."""

import secrets
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..config import Settings
from ..db import Database
from ..export import export_documents

KIND_LABELS = {"cfe": "Avis CFE", "attestation": "Attestation", "piece_jointe": "Pièce jointe"}


def create_app(settings: Settings, db: Database | None = None) -> FastAPI:
    database = db or Database(settings.db_path)
    security = HTTPBasic(realm="cfeauto")
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    templates.env.globals["kind_labels"] = KIND_LABELS

    def authenticate(credentials: HTTPBasicCredentials = Depends(security)) -> None:
        user_ok = secrets.compare_digest(credentials.username.encode(), settings.web_user.encode())
        password_ok = secrets.compare_digest(credentials.password.encode(), settings.web_password.encode())
        if not (user_ok and password_ok):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Identifiants invalides",
                headers={"WWW-Authenticate": 'Basic realm="cfeauto"'},
            )

    def same_origin(request: Request) -> None:
        """Le navigateur renvoie seul l'authentification Basic : on bloque les POST d'un autre site."""
        origin = request.headers.get("origin") or request.headers.get("referer")
        if not origin or urlparse(origin).netloc != request.headers.get("host"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Origine refusée")

    app = FastAPI(title="cfeauto", docs_url=None, redoc_url=None, openapi_url=None,
                  dependencies=[Depends(authenticate)] if settings.web_password else [])
    # Empêche un site malveillant de joindre l'interface locale en détournant son nom de domaine
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        # Pas no-referrer : le navigateur enverrait « Origin: null » et same_origin bloquerait les formulaires
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"
        return response

    @app.get("/")
    def index(request: Request, exported: int | None = None, export_error: bool = False):
        return templates.TemplateResponse(request, "index.html", {
            "dossiers": database.dossiers_overview(),
            "runs": database.recent_runs(),
            "export_dir": settings.export_dir.resolve() if settings.export_dir else None,
            "exported": exported,
            "export_error": export_error,
        })

    @app.post("/export", dependencies=[Depends(same_origin)])
    def export_all():
        if settings.export_dir is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        try:
            copied = export_documents(database, settings.documents_dir, settings.export_dir)
        except OSError:
            return RedirectResponse("/?export_error=1", status.HTTP_303_SEE_OTHER)
        return RedirectResponse(f"/?exported={copied}", status.HTTP_303_SEE_OTHER)

    @app.post("/runs", dependencies=[Depends(same_origin)])
    def start_run():
        run_id = database.enqueue_run("manual")
        return RedirectResponse(f"/runs/{run_id}", status.HTTP_303_SEE_OTHER)

    @app.post("/runs/{run_id}/connecte", dependencies=[Depends(same_origin)])
    def confirm_login(run_id: int):
        database.confirm_login(run_id)
        return RedirectResponse(f"/runs/{run_id}", status.HTTP_303_SEE_OTHER)

    @app.get("/runs/{run_id}")
    def run_detail(request: Request, run_id: int):
        run = database.get_run(run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        return templates.TemplateResponse(request, "run.html", {"run": run, "events": database.run_events(run_id)})

    @app.get("/dossiers/{siren}")
    def dossier_detail(request: Request, siren: str):
        documents = database.documents_for(siren)
        messages = database.messages_for(siren)
        if not documents and not messages:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        return templates.TemplateResponse(request, "dossier.html", {
            "siren": siren, "documents": documents, "messages": messages,
        })

    @app.get("/messages/{message_id}")
    def message_detail(request: Request, message_id: int):
        message = database.get_message(message_id)
        if message is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        database.mark_message_seen(message_id)
        return templates.TemplateResponse(request, "message.html", {
            "message": message, "attachments": database.attachments_for(message_id),
        })

    @app.get("/documents/{document_id}/download")
    def download(document_id: int):
        document = database.get_document(document_id)
        if document is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        root = settings.documents_dir.resolve()
        path = (root / document["path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        database.mark_document_seen(document_id)
        filename = f"{document['siren']}_{document['kind']}_{document['year']}_{document['id']}{path.suffix}"
        return FileResponse(path, filename=filename, media_type="application/octet-stream")

    return app
