import json

from helpers import (
    logging,
    load_menus,
    save_menus,
    load_seasons,
    call_llm,
    send_telegram,
    current_week,
    load_prompt,
    extract_json,
    normalize_recipes,
    get_recent_recipe_names,
    find_week_entry,
    find_best_match,
    get_current_week_menu,
    normalize_recipe,
)


def _handle_remplace(recipe_name, desired=None):
    """Replace a recipe in the current week's menu on explicit user request.

    Pipeline: fuzzy-match recipe_name against current menu → generate a
    replacement (prompt 4) → write to disk → send an updated recap (prompt 2).
    When `desired` is given, the replacement is based on that user wish
    instead of being freely chosen by the LLM.
    """
    logger = logging.getLogger("menu_daemon")

    week_info = current_week()
    year = week_info["year"]
    week = week_info["week"]
    season = week_info["season"]

    menus = load_menus()
    current_menu = get_current_week_menu(menus, year, week)
    if current_menu is None:
        send_telegram("Aucun menu trouvé pour cette semaine.")
        return

    candidates = [r["nom"] for r in current_menu]
    matched = find_best_match(recipe_name, candidates)
    if not matched:
        liste = "\n".join(f"• {n}" for n in candidates)
        send_telegram(
            f"Je n'ai pas trouvé « {recipe_name} » dans le menu. "
            f"Recettes de cette semaine :\n{liste}\n\n"
            f"Réessaie avec : /remplace <nom exact>"
        )
        return

    logger.info("Replacing recipe: %s", matched)

    seasons = load_seasons()
    ingredients = ", ".join(seasons.get(season, []))

    prompt4 = load_prompt(4)
    msg_replace = (
        f'Recette à remplacer : "{matched}".\n'
        f"Raison : à la demande de l'utilisateur.\n"
    )
    if desired:
        msg_replace += f'Recette souhaitée par l\'utilisateur : "{desired}".\n'
    msg_replace += f"Saison : {season}. Ingrédients de saison : {ingredients}.\n"
    recent = get_recent_recipe_names(menus)
    if recent:
        msg_replace += (
            f"Recettes des semaines récentes à éviter : {', '.join(recent)}.\n"
        )
    msg_replace += "Menu complet actuel :\n" + json.dumps(
        current_menu, ensure_ascii=False, indent=2
    )
    new_recipe_raw = call_llm(prompt4, msg_replace)
    new_recipe = normalize_recipe(extract_json(new_recipe_raw))

    if not replace_recipe_in_menu(menus, year, week, matched, new_recipe):
        send_telegram(f"Impossible de remplacer la recette « {matched} ».")
        return
    logger.info("Recipe replaced: %s → %s", matched, new_recipe.get("nom"))

    updated_menu = get_current_week_menu(menus, year, week)
    prompt2 = load_prompt(2)
    recap = call_llm(prompt2, json.dumps(updated_menu, ensure_ascii=False, indent=2))
    send_telegram(recap)


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


def save_week(menus, year, week, recipes):
    """Upsert the given week's recipes into menus, then persist to disk.

    Creates the year entry if missing and replaces any existing week
    with the same number.
    """
    year_entry = None
    for entry in menus.get("Année", []):
        if entry.get("numéro") == year:
            year_entry = entry
            break
    if year_entry is None:
        year_entry = {"numéro": year, "Semaine": []}
        menus.setdefault("Année", []).append(year_entry)
    year_entry["Semaine"] = [
        w for w in year_entry.get("Semaine", []) if w.get("numéro") != week
    ]
    year_entry["Semaine"].append(
        {
            "numéro": week,
            "menu": recipes,
        }
    )
    save_menus(menus)


def generate_menu():
    """Generate this week's menu and send it on Telegram.

    Assumes the menus.json lock is already held by the caller.
    Raises on failure (does not sys.exit).
    """
    logger = logging.getLogger("menu_generator")

    week_info = current_week()
    year = week_info["year"]
    week = week_info["week"]
    season = week_info["season"]
    month_name = week_info["month_name"]

    menus = load_menus()
    seasons = load_seasons()
    ingredients = ", ".join(seasons.get(season, []))

    prompt1 = load_prompt(1)
    msg1 = (
        f"Génère un menu de 4 recettes pour la semaine {week} ({month_name} {year}).\n"
        f"Saison : {season}. Ingrédients de saison : {ingredients}."
    )
    recent = get_recent_recipe_names(menus)
    if recent:
        msg1 += f"\nRecettes des semaines récentes à éviter : {', '.join(recent)}."
    recipes_json = call_llm(prompt1, msg1)
    parsed = extract_json(recipes_json)
    if isinstance(parsed, dict) and "menu" in parsed:
        parsed = parsed["menu"]
    recipes = normalize_recipes(parsed)

    prompt2 = load_prompt(2)
    formatted = call_llm(prompt2, json.dumps(recipes, ensure_ascii=False, indent=2))

    send_telegram(formatted)
    save_week(menus, year, week, recipes)

    logger.info("Week %d/%d menu generated successfully", week, year)
