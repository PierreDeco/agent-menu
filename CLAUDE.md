# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`agent-menu` is a personal meal-planning agent: Claude generates 4 weekly recipes, delivered via Telegram. A long-running daemon accepts modification requests by message.

See [README.md](README.md) for setup and usage, [ARCHITECTURE.md](ARCHITECTURE.md) for the design rationale and process diagram.

## Commands

```bash
pip install -r requirements.txt   # install deps
python menu_daemon.py             # long-running Telegram listener (handles /recettes and modifications)
```

`menu_generator.py` exposes `generate_menu()` as a library function — it has no `__main__` block. The daemon invokes it when the user sends `/recettes` on Telegram. To trigger generation from the shell (e.g. cron), wrap the call:

```bash
python -c "from helpers import setup_logging, LockFile; from menu_generator import generate_menu; setup_logging()
with LockFile(): generate_menu()"
```

No build, no linter, no test suite. To smoke-test code paths without sending real Telegram messages, unset `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` — messages are logged instead of sent.

## Conventions

- **Language split**: user-facing strings (Telegram messages, logs, prompts in `prompts.md`) are in French. Code, identifiers, docstrings, git commit messages are in English. JSON keys in `menus.json` and `seasons.json` use the French domain vocabulary (`"Année"`, `"Semaine"`, `"numéro"`, `"ingrédients"`, `"quantité"`) — do not translate them.
- **Docstrings**: public functions and classes carry docstrings; keep them concise (one line where possible).
- **Commits**: structured English messages with a one-line summary then a bulleted body. See recent commits for the pattern.

## Things to know when editing

- `menu_daemon.py` orchestrates both flows (generation on `/recettes`, recipe replacement on any other message). It always holds the lock on `menus.json` before calling into `menu_generator` or `_handle_modification`. Any new code that writes to `menus.json` must hold the lock via `LockFile`.
- LLM output drifts from the documented schema. Always pipe `extract_json` output through `normalize_recipe` / `normalize_recipes` before writing to disk or comparing names. The canonical recipe shape is `{nom, ingrédients: [{nom, quantité}]}` — anything else gets coerced or dropped.
- Prompts live in `prompts.md` and are addressed by header (`## Prompt N`). Adding a new prompt: append a `## Prompt N` section, access it via `helpers.load_prompt(N)`. Parsing is cached at first call.
- Prompts 1 and 4 receive an optional `"Recettes des semaines récentes à éviter : …"` line, built by `helpers.get_recent_recipe_names(menus, n=8)` from `menus.json`. The history is the 8 most recent weeks across all years (sorted by `(year, week)` descending), names only. The line is omitted entirely when no history exists.
- The Anthropic client is a lazy module-level singleton in `helpers.py`. Use `call_llm(...)` rather than instantiating `anthropic.Anthropic(...)` elsewhere — that would bypass the cache and the env-var validation.
- `current_week()` returns season keys that must match `seasons.json` exactly (`"été"` with accent, not `"ete"`). Adding a new season requires updating both `SEASON_MONTHS` in `helpers.py` and `seasons.json`.
- The daemon persists the Telegram update offset in `state.json` (written once per polled batch). When debugging stuck states, deleting `state.json` resets polling to "from latest".
