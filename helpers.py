import os
import json
import logging
import logging.handlers
import fcntl
import re
import time
from datetime import datetime, date
from pathlib import Path

from dotenv import load_dotenv
from thefuzz import process
import anthropic
import requests

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")
LOCK_PATH = BASE_DIR / "menu.lock"
LOG_PATH = BASE_DIR / "logs.txt"
MENUS_PATH = BASE_DIR / "menus.json"
SEASONS_PATH = BASE_DIR / "seasons.json"
STATE_PATH = BASE_DIR / "state.json"

SEASON_MONTHS = {
    "printemps": [3, 4, 5],
    "été": [6, 7, 8],
    "automne": [9, 10, 11],
    "hiver": [12, 1, 2],
}

MONTHS_FR = [
    "",
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
]


def setup_logging():
    """Configure root logger: rotating file (1 MB × 3) + stdout."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.handlers.RotatingFileHandler(
                LOG_PATH,
                maxBytes=1_000_000,
                backupCount=3,
                encoding="utf-8",
            ),
            logging.StreamHandler(),
        ],
    )


def load_json(path):
    """Load JSON from a path (UTF-8)."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    """Atomically write JSON to a path via a .tmp + rename."""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def load_seasons():
    return load_json(SEASONS_PATH)


def load_menus():
    return load_json(MENUS_PATH)


def save_menus(data):
    save_json(MENUS_PATH, data)


def load_state():
    """Load state.json, returning {} if missing or corrupted."""
    try:
        return load_json(STATE_PATH)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(data):
    save_json(STATE_PATH, data)


def extract_json(text):
    """Extract a JSON object/array from LLM output.

    Tries direct parse, then markdown fences (```json ... ```), then
    scans for the first { or [ and uses raw_decode to handle JSON
    embedded in surrounding prose. Raises ValueError if none found.
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                obj, _ = decoder.raw_decode(text[i:])
                return obj
            except json.JSONDecodeError:
                continue
    raise ValueError("Aucun JSON trouvé dans la réponse LLM")


def normalize_recipe(recipe):
    """Coerce a recipe dict to the canonical {nom, ingrédients[]} shape.

    Tolerates LLM drift: accepts "ingredients" without the accent,
    string-form ingredient items ("6 œufs"), and drops any extra
    fields the LLM may have invented (description, instructions, …).
    """
    if not isinstance(recipe, dict):
        raise ValueError(f"Recette invalide : {recipe!r}")
    nom = recipe.get("nom") or recipe.get("name") or ""
    raw = recipe.get("ingrédients") or recipe.get("ingredients") or []
    ingredients = []
    for item in raw:
        if isinstance(item, dict):
            ingredients.append({
                "nom": item.get("nom") or item.get("name") or "",
                "quantité": (
                    item.get("quantité")
                    or item.get("quantite")
                    or item.get("quantity")
                    or ""
                ),
            })
        elif isinstance(item, str):
            ingredients.append({"nom": item, "quantité": ""})
    return {"nom": nom, "ingrédients": ingredients}


def normalize_recipes(recipes):
    """Apply normalize_recipe to a list of recipes."""
    if not isinstance(recipes, list):
        raise ValueError(f"Liste de recettes attendue : {recipes!r}")
    return [normalize_recipe(r) for r in recipes]


class LockFile:
    """POSIX advisory file lock (fcntl.flock) used as a context manager.

    Coordinates writes between menu_generator and menu_modifier on the
    shared menus.json. Use blocking=False to fail fast with
    BlockingIOError when another process holds the lock.
    """

    def __init__(self, path=None, blocking=True):
        self.path = Path(path or LOCK_PATH)
        self.blocking = blocking
        self.fd = None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_CREAT | os.O_WRONLY)
        flags = fcntl.LOCK_EX
        if not self.blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(self.fd, flags)
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
            raise
        logging.info("Lock acquis %s (bloquant=%s)", self.path, self.blocking)
        return self

    def __exit__(self, *args):
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None
            logging.info("Lock libéré %s", self.path)


_client = None


def _anthropic_client():
    global _client
    if _client is None:
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY non définie")
        _client = anthropic.Anthropic(api_key=key)
    return _client


def call_llm(system_prompt, user_message, max_retries=3):
    """Call Claude with the given prompts; retry up to max_retries with
    exponential backoff (1s, 2s, 4s). Returns the text of the first
    content block. Raises after the final failed attempt.
    """
    client = _anthropic_client()
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
                max_tokens=2048,
                temperature=0.7,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            text = response.content[0].text
            logging.info(f"Retour du LLM : {text}")
            logging.info("LLM ok (%d tokens)", response.usage.output_tokens)
            return text
        except Exception as e:
            logging.warning(
                "LLM échec tentative %d/%d: %s", attempt + 1, max_retries, e
            )
            if attempt == max_retries - 1:
                logging.error("LLM abandon après %d tentatives", max_retries)
                raise
            time.sleep(2**attempt)


def send_telegram(message):
    """Send a Markdown-formatted message to the configured chat.

    If TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing, the message
    is logged instead of sent — useful for local testing.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logging.info("Telegram non configuré — message loggé seulement")
        logging.info("=== MESSAGE ===\n%s\n===============", message)
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            timeout=10,
        )
        resp.raise_for_status()
        logging.info("Telegram envoyé")
    except Exception as e:
        logging.error("Échec envoi Telegram: %s", e)


def get_updates(offset=0, timeout=30):
    """Long-poll Telegram for new messages, starting at offset.

    Returns an empty list on timeout or any error so the caller can
    safely loop. Blocks up to `timeout` seconds server-side.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logging.warning("TELEGRAM_BOT_TOKEN non défini — pas de polling")
        return []
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        resp = requests.get(
            url, params={"offset": offset, "timeout": timeout}, timeout=timeout + 5
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", [])
    except requests.Timeout:
        return []
    except Exception as e:
        logging.error("Échec polling Telegram: %s", e)
        return []


def current_week():
    """Return ISO year/week info for today plus French month name and season."""
    today = date.today()
    year, week, _ = today.isocalendar()
    month = today.month
    season = next(s for s, months in SEASON_MONTHS.items() if month in months)
    return {
        "year": year,
        "week": week,
        "month": month,
        "month_name": MONTHS_FR[month],
        "season": season,
    }


def find_best_match(query, candidates, threshold=70):
    """Fuzzy-match query against candidates (thefuzz, score_cutoff=70).

    Returns the best matching candidate string or None if no candidate
    scores above the threshold.
    """
    result = process.extractOne(query, candidates, score_cutoff=threshold)
    return result[0] if result else None


_PROMPTS = None


def load_prompt(num):
    """Return the body of prompt #num from prompts.md.

    On first call, parses the file once and caches all prompts in
    memory; subsequent calls are dict lookups.
    """
    global _PROMPTS
    if _PROMPTS is None:
        path = BASE_DIR / "prompts.md"
        text = path.read_text(encoding="utf-8")
        headers = list(re.finditer(r"^## Prompt (\d+)\b.*?$", text, re.MULTILINE))
        _PROMPTS = {}
        for i, match in enumerate(headers):
            start = match.end()
            end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
            _PROMPTS[int(match.group(1))] = text[start:end].strip()
    if num not in _PROMPTS:
        msg = f"Prompt #{num} introuvable dans prompts.md"
        logging.error(msg)
        raise ValueError(msg)
    return _PROMPTS[num]


def find_week_entry(menus, year, week):
    """Return the week dict for (year, week) in the menus structure, or None."""
    for year_entry in menus.get("Année", []):
        if year_entry.get("numéro") == year:
            for week_entry in year_entry.get("Semaine", []):
                if week_entry.get("numéro") == week:
                    return week_entry
    return None
