# agent-menu

Agent de planification de repas hebdomadaire propulsé par Claude et piloté par Telegram. Génère chaque semaine 4 idées de plats de saison, équilibrés et majoritairement végétariens, livrés sur Telegram avec la liste de courses consolidée. L'utilisateur peut demander à remplacer une recette directement par message.

## Fonctionnement

Le projet repose sur un démon unique, **`menu_daemon.py`**, qui écoute Telegram en long-polling et orchestre les deux flux :

- **Génération** — sur la commande `/recettes`, le démon appelle `menu_generator.generate_menu()` pour produire le menu de la semaine courante et l'envoyer sur Telegram.
- **Modification** — sur n'importe quel autre message, le démon détecte l'intention via Claude et remplace une recette du menu courant.

Lors de la génération comme du remplacement, Claude reçoit également la liste des recettes des 8 dernières semaines (extraite de `menus.json`) pour éviter de reproposer les mêmes plats.

`menu_generator.py` n'a pas de point d'entrée CLI : `generate_menu()` est une fonction bibliothèque appelée par le démon (qui détient déjà le lock sur `menus.json`).

Pour les choix de conception, voir [ARCHITECTURE.md](ARCHITECTURE.md).

## Installation

Prérequis : Python 3.9+, une clé API Anthropic, un bot Telegram.

```bash
git clone https://github.com/PierreDeco/agent-menu.git
cd agent-menu
pip install -r requirements.txt
```

## Configuration

Copie `.env.example` vers `.env` puis renseigne tes secrets :

```bash
cp .env.example .env
```

Variables d'environnement :

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Clé API Claude (obligatoire) |
| `ANTHROPIC_MODEL` | Modèle à utiliser (défaut : `claude-haiku-4-5-20251001`) |
| `TELEGRAM_BOT_TOKEN` | Token du bot obtenu via `@BotFather` |
| `TELEGRAM_CHAT_ID` | ID du chat destinataire des menus |

Si `TELEGRAM_BOT_TOKEN` ou `TELEGRAM_CHAT_ID` est absent, les messages sont uniquement écrits dans les logs — pratique pour tester.

## Utilisation

### Lancement du démon

```bash
python menu_daemon.py
```

Le démon tourne en continu et peut être supervisé par `systemd`, `supervisord` ou `tmux`.

### Génération manuelle d'un menu

Envoie `/recettes` au bot Telegram : le démon génère le menu de la semaine ISO courante, le sauvegarde dans `menus.json` et le renvoie sur Telegram.

### Génération automatique (cron)

`menu_generator.py` n'étant pas exécutable directement, deux options :

- **Recommandée** : programmer l'envoi d'un message `/recettes` chaque semaine via un client Telegram automatisé.
- **Alternative** : invoquer la fonction depuis cron en wrappant la prise de lock. Attention : à ne lancer que si le démon est arrêté, sinon le lock bloquera.

```cron
0 8 * * 1 cd /chemin/vers/agent-menu && /usr/bin/python3 -c "from helpers import setup_logging, LockFile; from menu_generator import generate_menu; setup_logging()
with LockFile(): generate_menu()"
```

### Exemples de messages Telegram

Une fois le démon lancé, écris au bot pour demander un changement :

> "Remplace les pâtes aux courgettes, je n'ai pas envie de pâtes cette semaine"

> "Change la salade de betteraves, je n'aime pas la betterave"

Le bot identifie la recette concernée (matching flou), demande à Claude de proposer un remplacement compatible avec la saison et le reste du menu, met à jour `menus.json` et renvoie le menu mis à jour.

## Disposition du projet

```
agent-menu/
├── menu_daemon.py      # démon Telegram (orchestration génération + modification)
├── menu_generator.py   # fonction generate_menu() appelée par le démon
├── helpers.py          # I/O, LLM, Telegram, lock, parsing
├── prompts.md          # les 4 prompts envoyés à Claude
├── seasons.json        # ingrédients de saison par saison
├── menus.json          # historique des menus générés
├── state.json          # offset Telegram (persistance polling)
├── menu.lock           # lock pour les écritures sur menus.json
└── logs.txt            # logs (rotation 1 MB × 3)
```

## Logs

Les logs sont écrits dans `logs.txt` avec rotation automatique (1 MB par fichier, 3 backups conservés). Les niveaux et le format sont définis dans `helpers.py:setup_logging`.
