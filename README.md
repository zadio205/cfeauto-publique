# cfeauto

Récupère les avis CFE de l'espace professionnel impots.gouv.fr pour les dossiers d'un cabinet,
et les présente dans une interface locale. Les attestations fiscales et la messagerie sont prévues
dans le code mais désactivées : elles ne sont collectées que si `portal.yaml` les décrit.

L'application tourne sur un seul PC Windows : le robot, les documents et l'interface y restent.

## État du projet

- Le parcours complet est testé sur macOS contre un **faux portail local** (`tests/fake_portal.py`).
- Il n'a **encore jamais été lancé sur Windows** ni contre le vrai site.
- `config/portal.yaml` contient des hypothèses : la collecte refuse de démarrer tant que
  `calibrated` n'est pas à `true`.

## Fonctionnement

1. Vous lancez une collecte depuis l'interface.
2. Une fenêtre Chromium s'ouvre sur impots.gouv.fr. **Vous vous y connectez vous-même** : le site
   impose un CAPTCHA, et le robot ne saisit ni identifiant, ni mot de passe, ni code.
3. Le robot détecte la connexion, parcourt les dossiers et télécharge les nouveaux documents.
4. Si la session expire, il redemande la connexion, puis reprend la rubrique en cours.

Tout est conservé dans `data/` : index SQLite, documents, profils de navigateur, diagnostics.

## Installation sur le PC

1. Décompressez le paquet dans un dossier local non synchronisé, par exemple `C:\cfeauto`.
   Le robot refuse de démarrer dans OneDrive, Dropbox ou Google Drive.
2. Installez uv dans un terminal, puis fermez ce terminal :

   ```bash
   winget install --id=astral-sh.uv -e
   ```

3. Double-cliquez sur `Installer cfeauto.bat`.
4. Renseignez les dossiers dans `config\accounts.yaml`.
5. Double-cliquez sur `Lancer cfeauto.bat`. Gardez sa fenêtre ouverte.
   L'interface est sur http://127.0.0.1:8000.

### Contrôle à faire au premier démarrage sur Windows

```bash
uv run cfeauto check
```

Ce contrôle crée `data` et lui applique ses droits. Vérifiez ensuite qu'il n'autorise que votre
session et le compte Système :

```bash
icacls C:\cfeauto\data
```

Puis lancez les tests, qui vérifient le parcours complet sur ce PC :

```bash
uv run pytest
```

## Calibrage

```bash
uv run cfeauto calibrate --account cabinet
```

Connectez-vous dans la fenêtre, relevez les sélecteurs de chaque écran (choix du dossier, avis CFE,
attestation, messagerie) dans l'inspecteur Playwright et reportez-les dans `config\portal.yaml`.
`logged_in_selector` doit viser un élément visible sur toutes les pages une fois connecté
(livré : le bouton « Me déconnecter » relevé sur le site). Il est cherché dans tous les onglets et
cadres de la fenêtre. Si vous êtes connecté et que rien ne se passe, cliquez sur **« Je suis
connecté »** dans la page de la collecte : le robot démarre et note dans le journal les boutons de
déconnexion qu'il voit, à reporter dans `logged_in_selector`. Testez
avec un seul dossier, puis passez `calibrated` à `true` :

```bash
uv run cfeauto run --account cabinet
```

Un lien qui ouvre le PDF dans un nouvel onglet sans attribut `href` n'est pas géré à ce stade :
s'il se présente, `session.py` (`fetch`) devra être complété.

## Export des avis

Après chaque collecte, les avis sont copiés avec des noms lisibles, rangés par client :

```
export\
├── SOCIETE EXEMPLE - 123456789\
│   ├── 123456789_CFE_2025.pdf
│   └── 123456789_CFE_2026.pdf
└── AUTRE SOCIETE - 987654321\
    └── 987654321_CFE_2026.pdf
```

Deux avis de la même année (rôle général et supplémentaire) donnent `…_2026.pdf` et `…_2026_2.pdf`.
Le bouton **« Exporter tous les avis maintenant »** de l'interface, ou `uv run cfeauto export`,
copie aussi les avis collectés auparavant. Un fichier déjà exporté n'est pas recopié ; un fichier
supprimé dans `export` est recopié au prochain export.

Le dossier se règle avec `CFEAUTO_EXPORT_DIR` dans `.env` (par défaut `export`, à côté de
l'application ; vide = pas d'export). Contrairement à `data`, ce dossier n'est **pas** protégé par le
robot : choisissez un emplacement dont les droits d'accès conviennent à des documents fiscaux.

## Sécurité

### Ce que le robot applique

| Risque | Protection |
| --- | --- |
| Vol de mots de passe | Aucun identifiant, mot de passe ou code n'est stocké ni saisi par le robot |
| Contournement du CAPTCHA | Aucun : la connexion est faite par un humain, sans masquage du navigateur |
| Accès depuis le réseau | L'interface n'écoute que sur `127.0.0.1` et refuse les noms d'hôte inconnus |
| Autre session Windows | `data` réservé à votre session et au compte Système par des droits NTFS ; refus de démarrer en cas d'échec |
| Fuite vers un cloud personnel | Refus de démarrer dans un dossier OneDrive, Dropbox, Google Drive ou iCloud |
| Site malveillant visité en parallèle | Formulaires d'autres origines refusés, pages interdites d'intégration |
| Fichier piégé ou inattendu | Avis et attestations : PDF seulement. Pièces jointes : extensions autorisées, noms générés par le robot, téléchargement sans ouverture dans l'interface |
| Liens externes | Téléchargement direct limité au domaine `impots.gouv.fr` |
| Message contenant du code | Stocké et affiché en texte, jamais exécuté |
| Secrets dans les journaux | Les erreurs du navigateur, qui peuvent citer des données de page, ne sont pas journalisées |

Le robot ne fait appel à aucune IA et n'envoie les documents à aucun serveur tiers.

### Ce qui reste à votre charge

- **BitLocker** activé sur le disque : le robot ne le vérifie pas.
- **Session Windows personnelle**, avec mot de passe et verrouillage automatique. Un administrateur
  du PC peut toujours accéder à `data`.
- **`CFEAUTO_WEB_PASSWORD`** dans `.env` si d'autres personnes utilisent votre session. Sans mot de
  passe, tout programme lancé sur le PC peut joindre l'interface.
- **Sauvegardes chiffrées** de `data` : sans elles, la perte du PC emporte les documents.
- **Profil de navigateur :** `data\profiles` contient des cookies du site. Une copie peut permettre
  d'ouvrir un compte tant que la session reste valide. Ne le copiez jamais en clair.
- **Captures de diagnostic :** `data\diagnostics` peut contenir des données fiscales. Videz-le une fois
  le problème résolu.
- **RGPD et mandats :** mention au registre du cabinet, durée de conservation, mandats clients.
- **Validation de l'automatisation :** le CAPTCHA indique que la DGFiP ne veut pas de connexions
  automatisées. Faites vérifier l'automatisation après connexion humaine avant un usage régulier.

## Limites connues

- La collecte exige une personne présente pour se connecter : `CFEAUTO_SCHEDULE_HOUR` ne fait que
  démarrer une collecte qui attend cette connexion, jusqu'à `CFEAUTO_LOGIN_TIMEOUT` secondes.
- Ouvrir un message peut le marquer comme lu sur le site. Seuls les nouveaux messages sont ouverts.
- Un message est identifié par le texte de sa ligne dans la liste : si ce texte change, il est repris.
- L'attestation est générée au plus une fois tous les `min_interval_days` jours par dossier.
- Un sélecteur de liste mal calibré ressemble à une liste vide : vérifiez les résultats lors du test.
