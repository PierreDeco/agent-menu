# Architecture

Ce document décrit les choix de conception d'`agent-menu`. Pour l'installation et l'usage, voir [README.md](README.md).

## Vue d'ensemble

```
                   ┌──────────────────────┐
   cron weekly ──▶ │  menu_generator.py   │ ─┐
                   └──────────────────────┘  │
                                             │      ┌────────────┐
                            menu.lock        ├─▶ ─▶ │ menus.json │
                                             │      └────────────┘
                   ┌──────────────────────┐  │
   Telegram   ──▶  │  menu_modifier.py    │ ─┘
   polling         └──────────────────────┘
```

Deux processus indépendants partagent les mêmes fichiers JSON :

- Le **generator** est court — il s'exécute une fois par semaine, écrit le menu, envoie le message Telegram et termine.
- Le **modifier** est long — il long-polle Telegram en continu, applique des remplacements à la demande.

Un fichier de lock (`menu.lock`) coordonne les écritures concurrentes sur `menus.json`.

## Choix de conception

### Pourquoi deux processus séparés

L'alternative serait un seul démon qui gère à la fois la planification hebdo (avec un scheduler interne) et le polling Telegram. Le découpage choisi est plus simple :

- Le generator est trivialement testable manuellement (`python menu_generator.py`).
- Le scheduling est délégué à `cron`/`systemd`, qui est plus fiable qu'un sleep interne.
- Le modifier peut crasher et être redémarré sans impact sur la génération hebdo.

### Locking inter-processus

`helpers.LockFile` utilise `fcntl.flock` (POSIX advisory lock) sur `menu.lock`. Le generator prend un lock bloquant (il doit terminer son écriture), tandis que le modifier prend un lock non-bloquant : si le generator tient le lock, le modifier renvoie un message à l'utilisateur lui demandant de réessayer.

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

### Fuzzy matching des noms de recettes

Quand l'utilisateur écrit "remplace les pâtes aux courgettes", le LLM (prompt 3) extrait le nom de recette mentionné, mais celui-ci ne matche pas forcément exactement l'entrée stockée. `helpers.find_best_match` (via `thefuzz`) fait un matching avec un seuil de 70 pour résoudre la recette ciblée parmi le menu courant.

### Caching

Pour éviter des I/O inutiles :

- **Prompts** : `prompts.md` est lu et parsé une seule fois (au premier `load_prompt`), les 4 prompts sont mis en cache module-level.
- **Client Anthropic** : un singleton lazy est créé au premier appel à `call_llm`.

### Logging

Logs en fichier (`logs.txt`) avec `RotatingFileHandler` pour borner l'espace disque (1 MB par fichier × 3 backups) et duplication sur stdout pour le debug interactif. Les retours bruts du LLM sont loggés pour pouvoir diagnostiquer les drifts de schéma.

### Polling Telegram

Le modifier utilise long-polling (`timeout=30` côté Telegram, +5s côté `requests`) plutôt que des webhooks pour éviter d'avoir à exposer un port. L'offset des messages traités est persisté dans `state.json` pour ne pas re-traiter les messages au redémarrage. La sauvegarde se fait une fois par batch reçu, pas par message individuel, pour limiter les fsync.

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
