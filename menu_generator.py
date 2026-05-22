#!/usr/bin/env python3
import json
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
    current_week,
    load_prompt,
    extract_json,
    normalize_recipes,
)


def save_week(menus, year, week, recipes):
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


def main():
    setup_logging()
    logger = logging.getLogger("menu_generator")

    week_info = current_week()
    year = week_info["year"]
    week = week_info["week"]
    season = week_info["season"]
    month_name = week_info["month_name"]

    with LockFile(blocking=True):
        try:
            menus = load_menus()
            seasons = load_seasons()
            ingredients = ", ".join(seasons.get(season, []))

            prompt1 = load_prompt(1)
            msg1 = (
                f"Génère un menu de 4 recettes pour la semaine {week} ({month_name} {year}).\n"
                f"Saison : {season}. Ingrédients de saison : {ingredients}."
            )
            recipes_json = call_llm(prompt1, msg1)
            parsed = extract_json(recipes_json)
            if isinstance(parsed, dict) and "menu" in parsed:
                parsed = parsed["menu"]
            recipes = normalize_recipes(parsed)

            prompt2 = load_prompt(2)
            formatted = call_llm(
                prompt2, json.dumps(recipes, ensure_ascii=False, indent=2)
            )

            send_telegram(formatted)
            save_week(menus, year, week, recipes)

            logger.info("Menu semaine %d/%d généré avec succès", week, year)

        except Exception as e:
            logger.error("Erreur génération menu: %s", e)
            send_telegram(f"Erreur génération menu semaine {week} : {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
