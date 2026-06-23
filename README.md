#### Video demo : https://youtu.be/P-YnNxRFSkQ

#### Description :
##### General idea

Denis is a personal recipe generator. It is powered by a daemon running on a raspberry pi that uses LLM calls to generate recipes.
By default, it generates a seasonal menu for 2-3 persons. It can also modify the last generated menu.

##### The Telegram bot

The telegram bot includes three commands :
- /recettes (stands for recipes) to generate the menu (basically the same as the web-app behavior)
- /remplace \<recipe\> (stands for replace) to replace a recipe that the user doesn't want.
- /remplacepar \<recipe\> | \<desired\> (stands for replaceby) to replace a recipe by something the user wants.
##### Design choices

###### Replacement mechanics

To replace correctly a recipe, a fuzzy matching function is defined, where a curated score of 70 must be reached. This function uses the "thefuzz" python module.

###### Lockfile

A system of non-blocking Lockfile is in place to avoid several writings to the menus.json. This system uses the fnctl library. This lockfile system has been entirely thought by and constructed by an coding agent as I had not foreseen this problem myself.

###### The LLM calls

It uses 4 system prompts in French, but can probably be optimized to use only three.
Prompt 1 asks the LLM to generate a menu. It's the base one. It explains the LLM that it's gonna be asked to generate a menu given these inputs :
- The year
- The week number
- The actual season
- The seasonal ingredients
- The expected output template
- The previous week's menu to avoid repetition

Prompt 2 explains that it will take a menu JSON as an input, and will be asked to generate a cheerful message introducing the menu and the associated shopping list. This output message is then sent to the web-app and the Telegram bot.

Prompt 3 receives an input from the user and asks to determine what the user intent is. The first version of the telegram bot did not include a command (the input was raw text). The LLM was then here to detect the intent from this message. With the implementation of commands (the /remplace and /remplacepar), the intent detection logic can be skipped).

Prompt 4 is basically the same as prompt 1, but the LLM is asked to modify a provided menu. It is given :
- The recipe to be replaced
- a reason (facultative)
- a desired recipe from the user (facultative but present with the use of /replacepar)
- the actual season with its fruits and vegetables
- the complete week menu
- the previous week's menu to avoid repetition
- The expected output template

###### JSON parsing
As the LLM is probabilistic by nature, I had to manage the case where the LLM, although asked to return a raw json only, returns a json with markdown tags or text around it. The extract_json() function manages these three cases.
The first case manages a raw json output.
The second case manages the case of a json surrounded by markdown tags
the third case manages the case of a json surrounded by text

This part will be improved as I've noticed while reading the Anthropic docs that we could get structured output out of the LLM directly.

###### State.json
This file manages the history of the bot. It offsets the entire conversation so that the bot does not see the whole history but only the sent message each time a message is sent.