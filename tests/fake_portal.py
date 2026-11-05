"""Faux portail local : reproduit le parcours attendu, pas le vrai site."""

import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

SIREN = "123456789"
CFE_PDF = b"%PDF-1.4 avis cfe 2026"
ATTESTATION_PDF = b"%PDF-1.4 attestation"
ATTACHMENT_PDF = b"%PDF-1.4 piece jointe"
LOGOUT = '<a id="logout" href="/home">Déconnexion</a>'


class FakePortal(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        # Simule la personne qui se connecte dans la fenêtre
        self.human_present = True
        self.expire_once = False
        self.expired_pdf = False
        self.logins = 0
        self.otp_prompts = 0
        self.message_opens = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def start(self) -> None:
        threading.Thread(target=self.serve_forever, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    server: FakePortal

    def log_message(self, *_args) -> None:
        pass

    def _cookies(self) -> dict[str, str]:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return {key: morsel.value for key, morsel in cookie.items()}

    def _send(self, body: bytes, content_type="text/html; charset=utf-8", headers=(), code=200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _html(self, content: str, headers=()) -> None:
        self._send(f"<!doctype html><html><body>{content}</body></html>".encode(), headers=headers)

    def _redirect(self, location: str, headers=()) -> None:
        self._send(b"", headers=[("Location", location), *headers], code=303)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        logged_in = self._cookies().get("session") == "ok"
        if path == "/login":
            if logged_in:
                return self._redirect("/home")
            script = "<script>setTimeout(() => document.forms[0].submit(), 200)</script>" if self.server.human_present else ""
            return self._html(f'<form method="post" action="/login"><img alt="captcha">{script}</form>')
        if path == "/otp":
            self.server.otp_prompts += 1
            return self._html('<form method="post" action="/otp"><script>setTimeout(() => document.forms[0].submit(), 200)</script></form>')
        if not logged_in:
            return self._redirect("/login")
        if path == "/home":
            return self._html(
                f'{LOGOUT}'
                '<a href="/cfe">Avis CFE</a> <a href="/attestations">Attestation fiscale</a> <a href="/messages">Messagerie</a>'
            )
        if path == "/cfe":
            if self.server.expire_once:
                self.server.expire_once = False
                return self._redirect("/login", headers=[("Set-Cookie", "session=; Path=/; Max-Age=0")])
            return self._html(
                f'{LOGOUT}<ul><li class="doc"><a href="/files/cfe-2026.pdf">Avis CFE 2026</a></li></ul>'
            )
        if path == "/files/cfe-2026.pdf":
            if self.server.expired_pdf:
                return self._html("Session expirée")
            return self._send(CFE_PDF, "application/pdf")
        if path == "/attestations":
            return self._html(
                f'{LOGOUT}'
                '<button class="doc" onclick="location.href=\'/attestation.pdf\'">Attestation 2026</button>'
            )
        if path == "/attestation.pdf":
            return self._send(ATTESTATION_PDF, "application/pdf",
                              headers=[("Content-Disposition", 'attachment; filename="attestation.pdf"')])
        if path == "/messages":
            return self._html(f'{LOGOUT}<table><tbody>'
                              '<tr class="msg"><td><a href="/messages/1">Relance CFE</a></td><td>12/10/2026</td></tr>'
                              '</tbody></table>')
        if path == "/messages/1":
            self.server.message_opens += 1
            return self._html(f'{LOGOUT}<h1 class="objet">Relance CFE</h1><p class="date">12/10/2026</p>'
                              '<div class="corps"><script>alert(1)</script>Merci de régler votre CFE.</div>'
                              '<a class="pj" href="/files/piece.pdf">piece.pdf</a>')
        if path == "/files/piece.pdf":
            return self._send(ATTACHMENT_PDF, "application/pdf")
        self._send(b"introuvable", code=404)

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        path = urlparse(self.path).path
        if path == "/login":
            self.server.logins += 1
            if self._cookies().get("trusted") == "yes":
                return self._redirect("/home", headers=[("Set-Cookie", "session=ok; Path=/")])
            return self._redirect("/otp")
        if path == "/otp":
            self.send_response(303)
            self.send_header("Location", "/home")
            self.send_header("Set-Cookie", "session=ok; Path=/")
            self.send_header("Set-Cookie", "trusted=yes; Path=/; Max-Age=15552000")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        self._send(b"introuvable", code=404)


def portal_config(base: str) -> dict:
    home = {"action": "goto", "target": f"{base}/home"}
    return {
        "calibrated": True,
        "allowed_domain": "127.0.0.1",
        "login": {
            "url": f"{base}/login",
            "logged_in_selector": "#logout",
        },
        "select_dossier": [home],
        "cfe": {
            "steps": [{"action": "click", "target": "a:has-text('Avis CFE')"}],
            "item_selector": "li.doc",
            "download_selector": "a",
        },
        "attestation": {
            "steps": [{"action": "click", "target": "a:has-text('Attestation fiscale')"}],
            "item_selector": "button.doc",
            "dedupe_by_label": False,
            "min_interval_days": 30,
        },
        "messages": {
            "steps": [{"action": "click", "target": "a:has-text('Messagerie')"}],
            "row_selector": "tr.msg",
            "link_selector": "a",
            "subject_selector": ".objet",
            "date_selector": ".date",
            "body_selector": ".corps",
            "attachment_selector": "a.pj",
        },
    }
