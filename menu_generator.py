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
)


def collect_used_names(menus):
    names = []
    for year_entry in menus.get("Année", []):
        for week_entry in year_entry.get("Semaine", []):
            for recipe in week_entry.get("menu", []):
                names.append(recipe["nom"])
    return names


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

            # used = collect_used_names(menus)
            # used_str = ", ".join(f'"{n}"' for n in used) if used else "aucun"
            ingredients = ", ".join(seasons.get(season, []))

            prompt1 = load_prompt(1)
            msg1 = (
                f"Génère un menu de 4 recettes pour la semaine {week} ({month_name} {year}).\n"
                f"Saison : {season}. Ingrédients de saison : {ingredients}.\n"
                # f"Plats déjà utilisés (à ne pas répéter) : {used_str}."
            )
            recipes_json = call_llm(prompt1, msg1)
            recipes = extract_json(recipes_json)

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
