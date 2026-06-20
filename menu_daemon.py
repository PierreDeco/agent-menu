import time

from helpers import (
    setup_logging,
    logging,
    LockFile,
    send_telegram,
    get_updates,
    load_state,
    save_state,
)
from menu_generator import generate_menu, _handle_remplace


def main():
    """Long-poll Telegram.

    Routes /recettes to menu generation, /remplace <recette> to recipe
    replacement, /remplacepar <recette> | <souhait> to replacement by a
    user-chosen recipe, and anything else to a usage hint.

    Persists the Telegram update offset in state.json so messages
    aren't re-processed after a restart. Takes a non-blocking lock on
    menus.json — if the generator is running, the user is asked to
    retry later.
    """
    setup_logging()
    logger = logging.getLogger("Daemon")
    logger.info("Agent started")

    offset = load_state().get("offset", 0)

    while True:
        try:
            updates = get_updates(offset=offset)
        except Exception as e:
            logger.error("Polling error: %s", e)
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

            logger.info("Message received: %s", text[:100])

            if text.startswith("/recettes"):
                logger.info("Command /recettes received")
                try:
                    with LockFile(blocking=False):
                        generate_menu()
                except BlockingIOError:
                    send_telegram(
                        "Génération déjà en cours, réessaie dans quelques minutes."
                    )
                except Exception as e:
                    logger.error("Menu generation error: %s", e)
                    send_telegram(f"Erreur lors de la génération : {e}")
            elif text.startswith("/remplacepar"):
                args = text[len("/remplacepar") :].strip()
                logger.info("Command /remplacepar received: %s", args[:100])
                existing, sep, desired = args.partition("|")
                existing = existing.strip()
                desired = desired.strip()
                if not sep or not existing or not desired:
                    send_telegram(
                        "Utilisation : /remplacepar <recette du menu> | "
                        "<recette souhaitée>\n"
                        "Exemple : /remplacepar poulet frites | pâtes au pesto"
                    )
                else:
                    try:
                        with LockFile(blocking=False):
                            _handle_remplace(existing, desired=desired)
                    except BlockingIOError:
                        send_telegram(
                            "Le menu est en cours de génération. "
                            "Réessaie dans quelques minutes."
                        )
                    except Exception as e:
                        logger.error("Replacement error: %s", e)
                        send_telegram(f"Erreur lors du remplacement : {e}")
            elif text.startswith("/remplace"):
                recipe_name = text[len("/remplace") :].strip()
                logger.info("Command /remplace received: %s", recipe_name[:100])
                if not recipe_name:
                    send_telegram(
                        "Utilisation : /remplace <nom de la recette>\n"
                        "Exemple : /remplace Pâtes aux courgettes"
                    )
                else:
                    try:
                        with LockFile(blocking=False):
                            _handle_remplace(recipe_name)
                    except BlockingIOError:
                        send_telegram(
                            "Le menu est en cours de génération. "
                            "Réessaie dans quelques minutes."
                        )
                    except Exception as e:
                        logger.error("Replacement error: %s", e)
                        send_telegram(f"Erreur lors du remplacement : {e}")
            else:
                send_telegram(
                    "Commandes disponibles :\n"
                    "• /recettes — générer le menu de la semaine\n"
                    "• /remplace <recette> — remplacer une recette du menu\n"
                    "• /remplacepar <recette> | <souhait> — remplacer une "
                    "recette par celle de ton choix"
                )

        if updates:
            save_state({"offset": offset})


if __name__ == "__main__":
    main()
