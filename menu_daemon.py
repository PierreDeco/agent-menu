#!/usr/bin/env python3
import json
import time
import sys

from helpers import (
    setup_logging,
    logging,
    LockFile,
    load_menus,
    save_menus,
    load_seasons,
    call_llm,
    send_telegram,
    get_updates,
    current_week,
    load_prompt,
    extract_json,
    load_state,
    save_state,
    find_best_match,
    find_week_entry,
    normalize_recipe,
)
from menu_generator import generate_menu


def get_current_week_menu(menus, year, week):
    """Return the list of recipes for (year, week), or None if absent."""
    entry = find_week_entry(menus, year, week)
    return entry.get("menu", []) if entry else None


def replace_recipe_in_menu(menus, year, week, original_name, new_recipe):
    """Replace original_name with new_recipe in (year, week) and persist.

    Returns True on success, False if the week or recipe was not found.
    Mutates `menus` in place.
    """
    entry = find_week_entry(menus, year, week)
    if entry is None:
        return False
    menu = entry.get("menu", [])
    for i, recipe in enumerate(menu):
        if recipe.get("nom") == original_name:
            menu[i] = new_recipe
            save_menus(menus)
            return True
    return False


def main():
    """Long-poll Telegram.
    Also generates menus on the "/recettes" command call or modifies
    menu on user's demand.

    Persists the Telegram update offset in state.json so messages
    aren't re-processed after a restart. Takes a non-blocking lock on
    menus.json — if the generator is running, the user is asked to
    retry later.
    """
    setup_logging()
    logger = logging.getLogger("Daemon")
    logger.info("Agent démarré")

    offset = load_state().get("offset", 0)

    while True:
        try:
            updates = get_updates(offset=offset)
        except Exception as e:
            logger.error("Erreur polling: %s", e)
            time.sleep(10)
            continue

        for update in updates:
            update_id = update.get("update_id", 0)
            offset = update_id + 1

            message = update.get("message") or update.get("edited_message")
            if not message:
                continue

            text = message.get("text", "").strip()
            if not text:
                continue

            logger.info("Message reçu: %s", text[:100])

            if text.startswith("/recettes"):
                logger.info("Commande /recettes reçue")
                try:
                    with LockFile(blocking=False):
                        generate_menu()
                except BlockingIOError:
                    send_telegram(
                        "Génération déjà en cours, réessaie dans quelques minutes."
                    )
                except Exception as e:
                    logger.error("Erreur génération menu: %s", e)
                    send_telegram(f"Erreur lors de la génération : {e}")
            else:
                try:
                    with LockFile(blocking=False):
                        _handle_modification(text)
                except BlockingIOError:
                    send_telegram(
                        "Le menu est en cours de génération. "
                        "Réessaie dans quelques minutes."
                    )
                except Exception as e:
                    logger.error("Erreur traitement message: %s", e)
                    send_telegram(f"Erreur lors du traitement : {e}")

        if updates:
            save_state({"offset": offset})


def _handle_modification(user_text):
    """Run the full modification flow for one user message.

    Pipeline: intent detection (prompt 3) → fuzzy-match the target
    recipe → ask Claude for a replacement (prompt 4) → write to disk →
    send an updated recap (prompt 2).
    """
    logger = logging.getLogger("menu_modifier")

    week_info = current_week()
    year = week_info["year"]
    week = week_info["week"]
    season = week_info["season"]

    prompt3 = load_prompt(3)
    msg_intent = (
        f'Message utilisateur : "{user_text}"\n'
        f"Semaine courante : {week} ({week_info['month_name']} {year})"
    )
    intent_raw = call_llm(prompt3, msg_intent)
    intent = extract_json(intent_raw)

    if intent.get("intention") != "modifier":
        logger.info("Message ignoré (intention: %s)", intent.get("intention"))
        return

    original_name = intent.get("recette_originale", "")
    raison = intent.get("raison", "")
    logger.info("Demande de modification: %s → %s", original_name, raison)

    menus = load_menus()
    current_menu = get_current_week_menu(menus, year, week)
    if current_menu is None:
        send_telegram("Aucun menu trouvé pour cette semaine.")
        return

    candidates = [r["nom"] for r in current_menu]
    matched = find_best_match(original_name, candidates)
    if not matched:
        liste = ", ".join(candidates)
        send_telegram(f"Je n'ai pas trouvé \"{original_name}\" dans le menu. Recettes disponibles : {liste}")
        return
    original_name = matched

    seasons = load_seasons()
    ingredients = ", ".join(seasons.get(season, []))

    prompt4 = load_prompt(4)
    msg_replace = (
        f'Recette à remplacer : "{original_name}".\n'
        f"Raison : {raison}.\n"
        f"Saison : {season}. Ingrédients de saison : {ingredients}.\n"
        f"Menu complet actuel :\n"
        + json.dumps(current_menu, ensure_ascii=False, indent=2)
    )
    new_recipe_raw = call_llm(prompt4, msg_replace)
    new_recipe = normalize_recipe(extract_json(new_recipe_raw))

    if not replace_recipe_in_menu(menus, year, week, original_name, new_recipe):
        send_telegram(
            f"Impossible de remplacer la recette \"{original_name}\"."
        )
        return
    logger.info("Recette remplacée: %s", original_name)

    updated_menu = get_current_week_menu(menus, year, week)
    prompt2 = load_prompt(2)
    recap = call_llm(
        prompt2, json.dumps(updated_menu, ensure_ascii=False, indent=2)
    )
    send_telegram(recap)


if __name__ == "__main__":
    main()
