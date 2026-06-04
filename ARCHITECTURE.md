# Architecture

This document describes the design decisions of `agent-menu`. For installation and usage, see [README.md](README.md).

## Overview

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

A single daemon (`menu_daemon.py`) listens to Telegram and delegates to two functions based on the message:

- `menu_generator.generate_menu()` — generates the current week's menu.
- `_handle_modification()` (internal to the daemon) — applies a recipe replacement.

The daemon always acquires a non-blocking lock on `menus.json` before invoking either. The lock ensures a cron job running `generate_menu` in parallel does not corrupt the file.

## Design decisions

### Why a daemon rather than a cron script

The alternative would be two separate executables: `menu_generator.py` run by cron each week, and a separate `menu_daemon.py` for modifications. The current design favours a single process:

- A single entry point (`/recettes` on Telegram) to trigger generation on demand, without connecting to the server.
- No second process to supervise.
- The split remains possible by recovering the old `__main__` from `menu_generator` if a strict cron is needed.

### Inter-process locking

`helpers.LockFile` uses `fcntl.flock` (POSIX advisory lock) on `menu.lock`. The daemon acquires a non-blocking lock before each generation or modification: if another process (e.g. a generation cron job) already holds the lock, the user receives a message asking them to retry. A direct call to `generate_menu()` from cron will use a blocking lock instead (see `README.md`).

### JSON format and recipe schema

Recipes follow this minimal schema:

```json
{
  "nom": "Pâtes aux courgettes",
  "ingrédients": [
    { "nom": "courgettes", "quantité": "400g" }
  ]
}
```

JSON keys use the French domain vocabulary (`"Année"`, `"Semaine"`, `"numéro"`, `"ingrédients"`, `"quantité"`). Since the LLM tends to drift from the schema (adding `description`, `instructions`, or writing `"ingredients"` without the accent), every response passes through `helpers.normalize_recipe`, which enforces the canonical shape before writing to disk.

### LLM output parsing

`helpers.extract_json` tolerates three response formats:

1. Plain JSON;
2. JSON wrapped in markdown fences (` ```json … ``` `);
3. JSON embedded in explanatory prose.

Case (3) uses `json.JSONDecoder.raw_decode`, which consumes a valid JSON object from any position and ignores the suffix — robust to nested objects (which a simple non-greedy regex would truncate at the first `}`).

### History injected into prompts

To prevent Claude from reproducing the same dishes week after week, prompts 1 (generation) and 4 (replacement) receive the list of recipes from the last 8 weeks, extracted from `menus.json` by `helpers.get_recent_recipe_names`. Only names are transmitted (minimal token cost). The line is omitted when history is empty (first run). Sorting is done on `(year, week)` descending, across all years.

### Fuzzy matching of recipe names

When the user writes "remplace les pâtes aux courgettes", the LLM (prompt 3) extracts the recipe name mentioned, but it may not exactly match the stored entry. `helpers.find_best_match` (via `thefuzz`) fuzzy-matches with a threshold of 70 to resolve the target recipe among the current menu.

### Caching

To avoid unnecessary I/O:

- **Prompts**: `prompts.md` is read and parsed once (on the first `load_prompt` call); all 4 prompts are cached at module level.
- **Anthropic client**: a lazy singleton is created on the first `call_llm` call.

### Logging

File logging (`logs.txt`) with `RotatingFileHandler` to bound disk usage (1 MB per file × 3 backups), duplicated to stdout for interactive debugging. Raw LLM responses are logged to diagnose schema drift.

### Telegram polling

The daemon uses long-polling (`timeout=30` on the Telegram side, +5s on the `requests` side) rather than webhooks to avoid exposing a port. The offset of processed messages is persisted in `state.json` to avoid re-processing messages on restart. The save happens once per received batch, not per individual message, to limit fsync calls.

### LLM retry

`call_llm` retries up to 3 times with exponential backoff (1s, 2s, 4s) on any exception. This is intentionally broad: on authentication or quota errors, the function re-logs and fails cleanly after the 3 attempts.

## Prompts

The 4 prompts are in `prompts.md`, in this order:

| # | Role |
|---|------|
| 1 | Weekly menu generation (4 recipes, seasonality + balance constraints) |
| 2 | Formatting as a friendly Telegram message with consolidated shopping list |
| 3 | Intent detection (modify vs. other) on user messages |
| 4 | Replacement recipe generation compatible with the current menu |

`load_prompt(N)` extracts the block corresponding to `## Prompt N`. This separation allows prompts to be modified without touching the code.
