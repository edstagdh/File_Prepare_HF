import requests
import json
import os
from loguru import logger


async def load_credentials():
    """
    Load bot_token and chat_id from creds.secret located in the project's root directory.
    Ensures chat_id is returned as an integer if possible.
    """

    try:
        # Resolve path relative to the project root (one level up from this script)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.abspath(os.path.join(script_dir, ".."))
        secrets_path = os.path.join(project_root, "creds.secret")

        with open(secrets_path, 'r') as secret_file:
            secrets = json.load(secret_file)
            bot_token = secrets.get("tg_bot_token", None)
            chat_id = secrets.get("tg_chat_id", None)

            # Attempt to convert chat_id to int
            if chat_id is not None:
                try:
                    chat_id = int(chat_id)
                except ValueError:
                    logger.error(f"Chat ID '{chat_id}' cannot be converted to an integer.")
                    chat_id = None

            return bot_token, chat_id

    except FileNotFoundError:
        logger.error("creds.secret file not found in project root.")
        return None, None
    except KeyError as e:
        logger.error(f"Key {e} is missing in creds.secret.")
        return None, None
    except json.JSONDecodeError:
        logger.error("Error parsing creds.secret. Ensure the JSON is valid.")
        return None, None


async def send_notification(message: str) -> bool:
    """
    Send a Telegram message via the Bot API with up to 3 retry attempts.

    Returns:
        True  — if the message was sent successfully
        False — if all retries fail or any error occurs
    """
    # logger.debug("Send notification running")

    bot_token, chat_id = await load_credentials()
    if not bot_token or not chat_id:
        logger.error("Telegram bot token or chat ID is missing.")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage?chat_id={chat_id}&text={message}"

    max_retries = 3

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(url, timeout=10)

            if response.status_code == 200:
                result = response.json()
                if result.get("ok", False):
                    return True
                else:
                    logger.error(f"Telegram API error: {result}")
            else:
                logger.error(f"HTTP {response.status_code}: {response.text}")

        except requests.RequestException as e:
            logger.error(f"Request error on attempt {attempt}: {e}")

        # If failed and we're not on the last attempt
        if attempt < max_retries:
            logger.info(f"Retrying ({attempt}/{max_retries})...")

    # If all retries failed
    return False
