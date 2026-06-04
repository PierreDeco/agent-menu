# agent-menu

Weekly meal-planning agent powered by Claude and driven by Telegram. Generates 4 seasonal, balanced, mostly vegetarian meal ideas each week, delivered on Telegram with a consolidated shopping list. The user can request a recipe replacement via the `/remplace` command.

## How it works

The project relies on a single daemon, **`menu_daemon.py`**, which listens to Telegram via long-polling and routes messages to one of three handlers:

- **`/recettes`** — calls `menu_generator.generate_menu()` to produce the current week's menu and send it on Telegram.
- **`/remplace <recette>`** — fuzzy-matches the named recipe in the current menu, asks Claude for a season-compatible replacement, updates `menus.json`, and sends back the updated menu.
- **Anything else** — sends a usage hint listing the available commands.

During both generation and replacement, Claude also receives the list of recipes from the last 8 weeks (extracted from `menus.json`) to avoid re-proposing the same dishes.

`menu_generator.py` has no CLI entry point: `generate_menu()` is a library function called by the daemon (which already holds the lock on `menus.json`).

For design decisions, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Installation

Prerequisites: Python 3.9+, an Anthropic API key, a Telegram bot.

```bash
git clone https://github.com/PierreDeco/agent-menu.git
cd agent-menu
pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` then fill in your secrets:

```bash
cp .env.example .env
```

Environment variables:

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Claude API key (required) |
| `ANTHROPIC_MODEL` | Model to use (default: `claude-haiku-4-5-20251001`) |
| `TELEGRAM_BOT_TOKEN` | Bot token obtained via `@BotFather` |
| `TELEGRAM_CHAT_ID` | ID of the chat that receives the menus |

If `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` is missing, messages are written to logs only — handy for testing.

## Usage

### Starting the daemon

```bash
python menu_daemon.py
```

The daemon runs continuously and can be supervised by `systemd`, `supervisord`, or `tmux`.

### Manually generating a menu

Send `/recettes` to the Telegram bot: the daemon generates the current ISO week's menu, saves it to `menus.json`, and sends it on Telegram.

### Automatic generation (cron)

Since `menu_generator.py` is not directly executable, two options:

- **Recommended**: schedule the `/recettes` message each week via an automated Telegram client.
- **Alternative**: invoke the function from cron, wrapping the lock acquisition. Note: only run this when the daemon is stopped, otherwise the lock will block.

```cron
0 8 * * 1 cd /path/to/agent-menu && /usr/bin/python3 -c "from helpers import setup_logging, LockFile; from menu_generator import generate_menu; setup_logging()
with LockFile(): generate_menu()"
```

### Replacing a recipe

Send `/remplace <recipe name>` to the bot:

```
/remplace Pâtes aux courgettes
/remplace salade de betteraves
```

The bot fuzzy-matches the name against the current week's recipes, asks Claude for a season-compatible replacement, updates `menus.json`, and sends back the updated menu.

If the name isn't recognized, the bot lists the current week's recipes and asks you to retry with the exact name.

## Project layout

```
agent-menu/
├── menu_daemon.py      # Telegram daemon (orchestrates generation + modification)
├── menu_generator.py   # generate_menu() function called by the daemon
├── helpers.py          # I/O, LLM, Telegram, lock, parsing
├── prompts.md          # the 4 prompts sent to Claude
├── seasons.json        # seasonal ingredients by season
├── menus.json          # history of generated menus
├── state.json          # Telegram offset (polling persistence)
├── menu.lock           # lock for writes to menus.json
└── logs.txt            # logs (1 MB × 3 rotation)
```

## Logs

Logs are written to `logs.txt` with automatic rotation (1 MB per file, 3 backups kept). Levels and format are defined in `helpers.py:setup_logging`.
