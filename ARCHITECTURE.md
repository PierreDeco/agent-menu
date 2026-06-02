# Architecture

Ce document décrit les choix de conception d'`agent-menu`. Pour l'installation et l'usage, voir [README.md](README.md).

## Vue d'ensemble

```
                                  ┌────────────────────┐
   Telegram /recettes  ─────────▶ │                    │ ──▶ generate_menu()  ─┐
                                  │   menu_daemon.py   │                       │
   Telegram message libre ─────▶  │ (long-polling)     │ ──▶ _handle_modif()  ─┤
                                  └────────────────────┘                       │
                                                                  menu.lock    │
                                                                               ▼
                                                                       ┌────────────┐
                                                                       │ menus.json │
                                                                       └────────────┘
```

Un seul démon (`menu_daemon.py`) écoute Telegram et délègue à deux fonctions selon le message :

- `menu_generator.generate_menu()` — génère le menu de la semaine courante.
- `_handle_modification()` (interne au démon) — applique un remplacement de recette.

Le démon prend systématiquement un lock non-bloquant sur `menus.json` avant d'invoquer l'un ou l'autre. Le lock reste utile pour qu'un job cron lançant `generate_menu` en parallèle ne corrompe pas le fichier.

## Choix de conception

### Pourquoi un démon plutôt qu'un script lancé par cron

L'alternative serait deux exécutables séparés : un `menu_generator.py` lancé par cron chaque semaine, et un `menu_daemon.py` séparé pour les modifications. Le découpage actuel privilégie un seul process :

- Un seul point d'entrée (`/recettes` sur Telegram) pour déclencher une génération à la demande, sans avoir à se connecter au serveur.
- Pas de second process à superviser.
- La séparation reste possible en récupérant l'ancien `__main__` de `menu_generator` si on veut un cron strict.

### Locking inter-processus

`helpers.LockFile` utilise `fcntl.flock` (POSIX advisory lock) sur `menu.lock`. Le démon prend un lock non-bloquant avant chaque génération ou modification : si un autre process (un cron de génération par exemple) tient déjà le lock, l'utilisateur reçoit un message lui demandant de réessayer. Un appel direct à `generate_menu()` depuis cron utilisera plutôt un lock bloquant (cf. `README.md`).

### Format JSON et schéma des recettes

Les recettes suivent ce schéma minimal :

```json
{
  "nom": "Pâtes aux courgettes",
  "ingrédients": [
    { "nom": "courgettes", "quantité": "400g" }
  ]
}
```

Les clés JSON utilisent le vocabulaire métier en français (`"Année"`, `"Semaine"`, `"numéro"`, `"ingrédients"`, `"quantité"`). Le LLM ayant tendance à dériver du schéma (ajouter `description`, `instructions`, ou écrire `"ingredients"` sans accent), toute réponse passe par `helpers.normalize_recipe` qui force le shape canonique avant écriture sur disque.

### Parsing de la sortie LLM

`helpers.extract_json` tolère trois formats de réponse :

1. JSON pur ;
2. JSON encadré par des fences markdown (` ```json … ``` `) ;
3. JSON noyé dans du texte explicatif.

Le cas (3) utilise `json.JSONDecoder.raw_decode` qui consomme un objet JSON valide depuis n'importe quelle position et ignore le suffixe — robuste aux objets imbriqués (qu'un simple regex non-greedy tronquerait au premier `}`).

### Historique injecté dans les prompts

Pour éviter que Claude reproduise les mêmes plats d'une semaine à l'autre, les prompts 1 (génération) et 4 (remplacement) reçoivent en entrée la liste des recettes des 8 dernières semaines, extraite de `menus.json` par `helpers.get_recent_recipe_names`. Seuls les noms sont transmis (coût token minimal). La ligne est omise quand l'historique est vide (premier lancement). Le tri se fait sur `(année, semaine)` décroissant, à travers toutes les années.

### Fuzzy matching des noms de recettes

Quand l'utilisateur écrit "remplace les pâtes aux courgettes", le LLM (prompt 3) extrait le nom de recette mentionné, mais celui-ci ne matche pas forcément exactement l'entrée stockée. `helpers.find_best_match` (via `thefuzz`) fait un matching avec un seuil de 70 pour résoudre la recette ciblée parmi le menu courant.

### Caching

Pour éviter des I/O inutiles :

- **Prompts** : `prompts.md` est lu et parsé une seule fois (au premier `load_prompt`), les 4 prompts sont mis en cache module-level.
- **Client Anthropic** : un singleton lazy est créé au premier appel à `call_llm`.

### Logging

Logs en fichier (`logs.txt`) avec `RotatingFileHandler` pour borner l'espace disque (1 MB par fichier × 3 backups) et duplication sur stdout pour le debug interactif. Les retours bruts du LLM sont loggés pour pouvoir diagnostiquer les drifts de schéma.

### Polling Telegram

Le démon utilise long-polling (`timeout=30` côté Telegram, +5s côté `requests`) plutôt que des webhooks pour éviter d'avoir à exposer un port. L'offset des messages traités est persisté dans `state.json` pour ne pas re-traiter les messages au redémarrage. La sauvegarde se fait une fois par batch reçu, pas par message individuel, pour limiter les fsync.

### Retry LLM

`call_llm` retry jusqu'à 3 fois avec backoff exponentiel (1s, 2s, 4s) sur n'importe quelle exception. C'est volontairement large : en cas d'erreur d'authentification ou de quota, on relog et on échoue proprement après les 3 tentatives.

## Prompts

Les 4 prompts sont dans `prompts.md`, dans cet ordre :

| # | Rôle |
|---|------|
| 1 | Génération du menu hebdomadaire (4 recettes, contraintes saisonnalité + équilibre) |
| 2 | Formatage en message Telegram convivial avec liste de courses consolidée |
| 3 | Détection d'intention (modifier vs autre) sur les messages utilisateurs |
| 4 | Génération d'une recette de remplacement compatible avec le menu courant |

`load_prompt(N)` extrait le bloc correspondant à `## Prompt N`. Cette séparation permet de modifier les prompts sans toucher au code.
