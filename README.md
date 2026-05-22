# agent-menu

Agent de planification de repas hebdomadaire propulsé par Claude et piloté par Telegram. Génère chaque semaine 4 idées de plats de saison, équilibrés et majoritairement végétariens, livrés sur Telegram avec la liste de courses consolidée. L'utilisateur peut demander à remplacer une recette directement par message.

## Fonctionnement

Le projet expose deux exécutables indépendants :

- **`menu_generator.py`** — exécution ponctuelle (typiquement via cron) qui génère le menu de la semaine courante et l'envoie sur Telegram.
- **`menu_modifier.py`** — démon long-running qui écoute Telegram via long-polling et orchestre les remplacements de recettes demandés par l'utilisateur.

Les deux processus partagent l'état via les fichiers JSON et coordonnent leurs écritures via un fichier de lock (`menu.lock`).

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

### Génération manuelle d'un menu

```bash
python menu_generator.py
```

Le menu de la semaine ISO courante est généré, sauvegardé dans `menus.json` et envoyé sur Telegram.

### Génération automatique (cron)

Pour générer le menu chaque lundi matin à 8h :

```cron
0 8 * * 1 cd /chemin/vers/agent-menu && /usr/bin/python3 menu_generator.py
```

### Démon de modification

Lance le démon pour écouter les demandes de modification :

```bash
python menu_modifier.py
```

Le démon tourne en continu et peut être supervisé par `systemd`, `supervisord` ou `tmux`.

### Exemples de messages Telegram

Une fois le démon lancé, écris au bot pour demander un changement :

> "Remplace les pâtes aux courgettes, je n'ai pas envie de pâtes cette semaine"

> "Change la salade de betteraves, je n'aime pas la betterave"

Le bot identifie la recette concernée (matching flou), demande à Claude de proposer un remplacement compatible avec la saison et le reste du menu, met à jour `menus.json` et renvoie le menu mis à jour.

## Disposition du projet

```
agent-menu/
├── menu_generator.py   # entrypoint génération hebdo
├── menu_modifier.py    # démon de modification
├── helpers.py          # I/O, LLM, Telegram, lock, parsing
├── prompts.md          # les 4 prompts envoyés à Claude
├── seasons.json        # ingrédients de saison par saison
├── menus.json          # historique des menus générés
├── state.json          # offset Telegram (persistance polling)
├── menu.lock           # lock inter-processus
└── logs.txt            # logs (rotation 1 MB × 3)
```

## Logs

Les logs sont écrits dans `logs.txt` avec rotation automatique (1 MB par fichier, 3 backups conservés). Les niveaux et le format sont définis dans `helpers.py:setup_logging`.
