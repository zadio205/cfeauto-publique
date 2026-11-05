"""Erreurs métier. Leurs messages sont journalisés : jamais de secret dedans."""


class CfeautoError(Exception):
    pass


class ConfigError(CfeautoError):
    pass


class AccountStop(CfeautoError):
    """Arrête tout le compte : continuer sans connexion ne servirait à rien."""


class HumanActionRequired(AccountStop):
    """Connexion humaine non effectuée à temps, ou fenêtre fermée."""


class SessionExpired(CfeautoError):
    pass


class PortalChanged(CfeautoError):
    """Le site ne correspond plus à portal.yaml."""


class InvalidDocument(CfeautoError):
    """Le fichier reçu n'est pas celui attendu (session expirée, page d'erreur...)."""
