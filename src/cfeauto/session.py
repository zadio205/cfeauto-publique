"""Session navigateur d'un compte : connexion humaine, étapes déclaratives, téléchargement.

Le site impose un CAPTCHA à la connexion : le robot ne saisit ni identifiant, ni mot de passe,
ni code. Il ouvre la page, attend qu'un humain se connecte dans la fenêtre, puis travaille.
"""

import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, sync_playwright

from .config import Account, PortalConfig, Settings, Step
from .errors import HumanActionRequired, InvalidDocument, PortalChanged
from .securite import make_private_dir

Notify = Callable[[str], None]

# Délai avant de signaler que la connexion n'est toujours pas détectée
LOGIN_HINT_DELAY = 60

# Décrit les éléments cliquables dont le texte, le titre ou le libellé évoque une déconnexion
LOGOUT_CANDIDATES_JS = r"""() => {
  const pattern = /d[ée]connex|d[ée]connect|logout|log out|sign out|quitter/i;
  const describe = (el) => {
    const label = (el.innerText || (el.tagName === "INPUT" ? el.value : "") || "").trim().replace(/\s+/g, " ").slice(0, 40);
    const attrs = ["id", "class", "title", "aria-label", "name"]
      .filter((name) => el.getAttribute(name))
      .map((name) => `${name}="${el.getAttribute(name).slice(0, 60)}"`);
    return `<${el.tagName.toLowerCase()}${attrs.length ? " " + attrs.join(" ") : ""}>${label}`;
  };
  return [...document.querySelectorAll("a, button, input[type=submit], input[type=button], [role=button], [onclick]")]
    .filter((el) => el.offsetParent !== null)
    .filter((el) => pattern.test([el.innerText, el.value, el.title, el.getAttribute("aria-label"),
                                  el.id, el.className, el.getAttribute("href")].join(" ")))
    .map(describe);
}"""


class PortalSession:
    def __init__(self, account: Account, portal: PortalConfig, settings: Settings, notify: Notify,
                 login_confirmed: Callable[[], bool] = lambda: False):
        self.account = account
        self.portal = portal
        self.settings = settings
        self.notify = notify
        # Bouton « Je suis connecté » de l'interface, quand logged_in_selector ne reconnaît pas le site
        self.login_confirmed = login_confirmed
        self.manual_login = False
        # Page d'arrivée après la connexion : point de départ de chaque rubrique
        self.home_url = ""
        self.home_page: Page | None = None
        self.page: Page

    def __enter__(self) -> "PortalSession":
        profile = self.settings.profiles_dir / self.account.id
        make_private_dir(profile)
        self._playwright = sync_playwright().start()
        self.context = self._playwright.chromium.launch_persistent_context(
            str(profile),
            headless=self.settings.headless,
            accept_downloads=True,
            locale="fr-FR",
            timezone_id="Europe/Paris",
        )
        self.context.set_default_timeout(30_000)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        return self

    def __exit__(self, *_exc) -> None:
        self.context.close()
        self._playwright.stop()

    # Connexion

    def ensure_logged_in(self) -> None:
        login = self.portal.login
        # Une confirmation manuelle ne vaut que pour la connexion en cours : toute reconnexion en redemande une
        self.manual_login = False
        try:
            self.page.goto(login.url)
        except PlaywrightError as exc:
            raise PortalChanged(f"Page de connexion inaccessible : {login.url}") from exc
        if not self._wait_logged_in(timeout=3):
            self.notify(f"Connexion requise : connectez-vous dans la fenêtre du compte {self.account.id}")
            self.page.bring_to_front()
            if not self._wait_logged_in(timeout=self.settings.login_timeout):
                raise HumanActionRequired(f"Connexion non effectuée à temps pour le compte {self.account.id}")
        self.home_url = self.page.url
        self.home_page = self.page

    def is_logged_in(self) -> bool:
        """Cherche la marque de connexion dans tous les onglets et tous leurs cadres.

        L'espace connecté peut s'ouvrir dans un nouvel onglet ou un cadre, et une rubrique peut
        s'ouvrir dans un onglet sans bouton de déconnexion : tous les onglets comptent.
        Après une confirmation manuelle, la session est tenue pour ouverte tant que la page
        de connexion ne réapparaît pas.
        """
        if self._logged_in_page() is not None:
            return True
        return self.manual_login and not self._on_login_page()

    def _logged_in_page(self) -> Page | None:
        selector = self.portal.login.logged_in_selector
        for page in self._open_pages():
            try:
                page.wait_for_load_state()
                if any(frame.locator(selector).first.is_visible() for frame in page.frames):
                    return page
            except PlaywrightError:
                continue
        return None

    def _open_pages(self) -> list[Page]:
        pages = [self.page] + [page for page in self.context.pages if page is not self.page]
        return [page for page in pages if not page.is_closed()]

    def _on_login_page(self) -> bool:
        """Un champ de mot de passe visible : la session a expiré et le site redemande la connexion."""
        try:
            return any(frame.locator("input[type=password]").first.is_visible() for frame in self.page.frames)
        except PlaywrightError:
            return False

    def _wait_logged_in(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        hint_at = time.monotonic() + LOGIN_HINT_DELAY
        while True:
            pages = self._open_pages()
            if not pages:
                raise HumanActionRequired(f"Fenêtre du compte {self.account.id} fermée avant la connexion")
            self.page = pages[0]
            logged_in_page = self._logged_in_page()
            if logged_in_page is not None:
                self.page = logged_in_page
                return True
            if self.login_confirmed():
                self._accept_manual_login()
                return True
            now = time.monotonic()
            if now >= deadline:
                return False
            if timeout > LOGIN_HINT_DELAY and now >= hint_at:
                hint_at = deadline
                self.notify(
                    "Connexion toujours pas détectée. Si vous êtes connecté, cliquez sur "
                    "« Je suis connecté » dans la page de cette collecte"
                )
            time.sleep(0.5)

    def _accept_manual_login(self) -> None:
        """Travaille dans l'onglet ouvert le plus récemment et relève les boutons de déconnexion."""
        self.manual_login = True
        self.page = self._open_pages()[-1]
        self.notify("Connexion confirmée manuellement : collecte lancée")
        candidates = self.logout_candidates()
        if candidates:
            self.notify("Boutons de déconnexion repérés (à reporter dans logged_in_selector) : " + " | ".join(candidates))
        else:
            self.notify("Aucun bouton de déconnexion repéré sur la page : logged_in_selector reste à définir")

    def logout_candidates(self) -> list[str]:
        """Décrit, sans lien ni valeur de formulaire, les éléments qui ressemblent à une déconnexion."""
        found: list[str] = []
        for frame in self.page.frames:
            try:
                found += frame.evaluate(LOGOUT_CANDIDATES_JS)
            except PlaywrightError:
                continue
        return list(dict.fromkeys(found))[:5]

    # Navigation

    def run_steps(self, steps: list[Step], variables: dict[str, str]) -> None:
        for step in steps:
            try:
                self._run_step(step, variables)
            except PlaywrightError:
                if step.optional:
                    continue
                # Seul le chemin de la page est cité : ses paramètres peuvent contenir un jeton de session
                path = urlparse(self.page.url).path if not self.page.is_closed() else "?"
                raise PortalChanged(f"Étape échouée : {step.action} sur « {step.target} » (page {path})") from None
            time.sleep(self.settings.action_delay)

    def _run_step(self, step: Step, variables: dict[str, str]) -> None:
        target = _substitute(step.target, variables)
        value = _substitute(step.value, variables)
        if step.skip_if and self._visible(_substitute(step.skip_if, variables)):
            return
        if step.action == "home":
            self.go_home()
            return
        if step.action == "main_tab":
            self.main_tab()
            return
        if step.action == "goto":
            self.page.goto(target)
            return
        locator = self.page.locator(target).first
        if step.action == "click":
            self._click(locator)
        elif step.action == "fill":
            locator.fill(value)
        elif step.action == "select":
            locator.select_option(value)
            self.page.wait_for_load_state()
        elif step.action == "wait_for":
            locator.wait_for()

    def go_home(self) -> None:
        """Revient dans l'onglet de connexion, sur la page d'arrivée, et ferme les autres onglets."""
        self.main_tab()
        self.page.goto(self.home_url or self.portal.login.url)
        self.page.wait_for_load_state()

    def main_tab(self) -> None:
        """Ferme les onglets ouverts par les rubriques et revient dans l'onglet de connexion, sans le recharger."""
        if self.home_page is not None and not self.home_page.is_closed():
            self.page = self.home_page
        for page in self._open_pages():
            if page is not self.page:
                page.close()
        self.page.bring_to_front()

    def _visible(self, selector: str) -> bool:
        try:
            return self.page.locator(selector).first.is_visible()
        except PlaywrightError:
            return False

    def _click(self, locator: Locator) -> None:
        """Clique, y compris sur un lien de menu replié, et suit le lien s'il ouvre un nouvel onglet."""
        locator.wait_for(state="attached")
        target = locator.get_attribute("target") or ""

        def click() -> None:
            if locator.is_visible():
                locator.click()
            else:
                locator.evaluate("element => element.click()")

        if target and target not in ("_self", "_parent", "_top"):
            with self.context.expect_page() as new_page:
                click()
            self.page = new_page.value
        else:
            click()
        self.page.wait_for_load_state()

    # Téléchargement

    def fetch(self, element: Locator) -> tuple[bytes, str]:
        """Rend le contenu et le nom du fichier, par lien direct ou par téléchargement."""
        href = element.get_attribute("href")
        if href and not href.startswith(("#", "javascript:")):
            url = urljoin(self.page.url, href)
            self._check_domain(url)
            response = self.context.request.get(url)
            if not response.ok:
                raise InvalidDocument(f"Téléchargement refusé (HTTP {response.status})")
            return response.body(), Path(urlparse(url).path).name
        return self._click_for_file(element)

    def _click_for_file(self, element: Locator) -> tuple[bytes, str]:
        """Bouton ou formulaire (href="#") : le fichier arrive en téléchargement ou s'affiche en PDF."""
        downloads: list = []
        pdfs: list = []
        start_url = self.page.url
        pages_before = set(self._open_pages())

        def on_response(response) -> None:
            if "application/pdf" in (response.headers.get("content-type") or ""):
                pdfs.append(response)

        def on_download(download) -> None:
            downloads.append(download)

        def watch(page: Page) -> None:
            page.on("download", on_download)

        for page in pages_before:
            watch(page)
        self.context.on("page", watch)
        self.context.on("response", on_response)
        try:
            element.click()
            deadline = time.monotonic() + 30
            while not (downloads or pdfs) and time.monotonic() < deadline:
                self.page.wait_for_timeout(250)
            content = self._pdf_body(pdfs, downloads, deadline)
            if downloads:
                content, name = Path(downloads[0].path()).read_bytes(), downloads[0].suggested_filename
            elif content is not None:
                name = Path(urlparse(pdfs[0].url).path).name
            else:
                raise InvalidDocument("Aucun fichier reçu après le clic")
        finally:
            self.context.remove_listener("page", watch)
            self.context.remove_listener("response", on_response)
            # Un PDF ouvert dans un onglet ou à la place de la liste : on revient à la liste
            for page in self._open_pages():
                if page not in pages_before:
                    page.close()
            if pdfs and self.page.url != start_url:
                try:
                    self.page.go_back()
                    self.page.wait_for_load_state()
                except PlaywrightError:
                    pass
        return content, name

    def _pdf_body(self, pdfs: list, downloads: list, deadline: float) -> bytes | None:
        """Le PDF affiché dans la page ; s'il part en téléchargement, son contenu n'est lisible qu'ainsi."""
        if not pdfs or downloads:
            return None
        self._check_domain(pdfs[0].url)
        try:
            return pdfs[0].body()
        except PlaywrightError:
            while not downloads and time.monotonic() < deadline:
                self.page.wait_for_timeout(250)
            return None

    def _check_domain(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        domain = self.portal.allowed_domain
        if host != domain and not host.endswith(f".{domain}"):
            raise InvalidDocument(f"Lien hors du domaine autorisé : {host}")

    def save_diagnostic(self, label: str) -> Path | None:
        """Capture d'écran privée : elle peut contenir des données fiscales."""
        directory = self.settings.diagnostics_dir
        make_private_dir(directory)
        path = directory / f"{datetime.now():%Y%m%d-%H%M%S}-{self.account.id}-{label}.png"
        try:
            self.page.screenshot(path=str(path), full_page=True)
            path.chmod(0o600)
        except PlaywrightError:
            return None
        return path


def _substitute(text: str, variables: dict[str, str]) -> str:
    for name, value in variables.items():
        text = text.replace(f"{{{name}}}", value)
    return text
