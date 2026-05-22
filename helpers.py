import os
import json
import logging
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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH),
            logging.StreamHandler(),
        ],
    )


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
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
    try:
        return load_json(STATE_PATH)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(data):
    save_json(STATE_PATH, data)


def extract_json(text):
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
    if not isinstance(recipes, list):
        raise ValueError(f"Liste de recettes attendue : {recipes!r}")
    return [normalize_recipe(r) for r in recipes]


class LockFile:
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


def _anthropic_client():
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY non définie")
    return anthropic.Anthropic(api_key=key)


def call_llm(system_prompt, user_message, max_retries=3):
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
    result = process.extractOne(query, candidates, score_cutoff=threshold)
    return result[0] if result else None


def load_prompt(num):
    path = BASE_DIR / "prompts.md"
    text = path.read_text(encoding="utf-8")
    pattern = rf"^## Prompt {num}\b.*?$"
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        msg = f"Prompt #{num} introuvable dans {path}"
        logging.error(msg)
        raise ValueError(msg)
    start = match.end()
    rest = text[start:]
    next_match = re.search(r"^## Prompt \d+\b", rest, re.MULTILINE)
    end = start + next_match.start() if next_match else len(text)
    return text[start:end].strip()
