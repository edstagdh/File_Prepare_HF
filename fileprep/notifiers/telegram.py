import httpx
import socket
import asyncio
from loguru import logger

from fileprep.config.store import get_credentials


async def load_credentials():
    """Telegram bot token and chat id from Configs/credentials.secret (chat id converted to int when possible)."""
    notifier_credentials = get_credentials().notifiers
    bot_token = notifier_credentials.telegram_bot_token or None
    chat_id = notifier_credentials.telegram_chat_id or None
    if chat_id is not None:
        try:
            chat_id = int(chat_id)
        except ValueError:
            logger.error(f"Chat ID '{chat_id}' cannot be converted to an integer.")
            chat_id = None
    return bot_token, chat_id


async def send_notification(message: str) -> bool:
    """
    Send a Telegram message via the Bot API with up to 3 retry attempts.

    Returns:
        True  — if the message was sent successfully
        False — if all retries fail or any error occurs
    """
    max_retries = 3
    retry_delay = 2  # seconds

    bot_token, chat_id = await load_credentials()
    if not bot_token or not chat_id:
        logger.error("Telegram bot token or chat ID is missing.")
        return False

    hostname = socket.gethostname()
    full_message = f"[{hostname}] {message}"
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": full_message}

    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, data=payload, timeout=10)

            if response.status_code == 200:
                result = response.json()
                if result.get("ok", False):
                    return True
                else:
                    logger.error(f"Telegram API error: {result}")
            else:
                logger.error(f"HTTP {response.status_code}: {response.text}")

        except httpx.RequestError as e:
            logger.error(f"Request error on attempt {attempt}: {e}")

        if attempt < max_retries:
            logger.info(f"Retrying ({attempt}/{max_retries})...")
            await asyncio.sleep(retry_delay)

    return False