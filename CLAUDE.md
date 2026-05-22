# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`agent-menu` is a personal meal-planning agent: Claude generates 4 weekly recipes, delivered via Telegram. A long-running daemon accepts modification requests by message.

See [README.md](README.md) for setup and usage, [ARCHITECTURE.md](ARCHITECTURE.md) for the design rationale and process diagram.

## Commands

```bash
pip install -r requirements.txt   # install deps
python menu_generator.py          # one-shot weekly generation
python menu_modifier.py           # long-running Telegram listener
```

No build, no linter, no test suite. To smoke-test code paths without sending real Telegram messages, unset `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` — messages are logged instead of sent.

## Conventions

- **Language split**: user-facing strings (Telegram messages, logs, prompts in `prompts.md`) are in French. Code, identifiers, docstrings, git commit messages are in English. JSON keys in `menus.json` and `seasons.json` use the French domain vocabulary (`"Année"`, `"Semaine"`, `"numéro"`, `"ingrédients"`, `"quantité"`) — do not translate them.
- **Docstrings**: public functions and classes carry docstrings; keep them concise (one line where possible).
- **Commits**: structured English messages with a one-line summary then a bulleted body. See recent commits for the pattern.

## Things to know when editing

- Two processes share `menus.json` via an `fcntl` advisory lock (`menu.lock`). The generator takes a blocking lock, the modifier a non-blocking one. Any new code that writes to `menus.json` must hold the lock.
- LLM output drifts from the documented schema. Always pipe `extract_json` output through `normalize_recipe` / `normalize_recipes` before writing to disk or comparing names. The canonical recipe shape is `{nom, ingrédients: [{nom, quantité}]}` — anything else gets coerced or dropped.
- Prompts live in `prompts.md` and are addressed by header (`## Prompt N`). Adding a new prompt: append a `## Prompt N` section, access it via `helpers.load_prompt(N)`. Parsing is cached at first call.
- The Anthropic client is a lazy module-level singleton in `helpers.py`. Use `call_llm(...)` rather than instantiating `anthropic.Anthropic(...)` elsewhere — that would bypass the cache and the env-var validation.
- `current_week()` returns season keys that must match `seasons.json` exactly (`"été"` with accent, not `"ete"`). Adding a new season requires updating both `SEASON_MONTHS` in `helpers.py` and `seasons.json`.
- The modifier persists the Telegram update offset in `state.json` (written once per polled batch). When debugging stuck states, deleting `state.json` resets polling to "from latest".
