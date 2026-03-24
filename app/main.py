import asyncio
import logging

from .config import Config
from .gigachat_client import GigaChatClient
from .message_handler import MessageHandler
from .telegram_client import create_client, register_handlers
from .utils import setup_logging, setup_signal_handlers

logger = logging.getLogger(__name__)


async def run() -> None:
    config = Config.from_env()
    setup_logging(debug=config.debug)

    if config.debug:
        logger.debug("DEBUG mode enabled")

    logger.info(
        "Configuration loaded (chat_id=%s, topic_id=%s, mode=%s, debug=%s)",
        config.chat_id, config.topic_id, config.bot_mode, config.debug,
    )

    if config.bot_mode == "file_qa":
        if not config.gigachat_file_id:
            logger.error(
                "BOT_MODE=file_qa but GIGACHAT_FILE_ID is not set. "
                "Run: python upload_file.py <your_file> to get the file_id."
            )
        else:
            logger.info(
                "file_qa mode: trigger='%s, подскажи ...' file_id=%s",
                config.bot_name, config.gigachat_file_id,
            )
    elif config.bot_mode == "dialog":
        logger.info("dialog mode: trigger_words=%s", config.trigger_words)
    else:
        logger.warning("Unknown BOT_MODE='%s', defaulting to dialog", config.bot_mode)

    gigachat = GigaChatClient(
        auth_key=config.gigachat_auth_key,
        scope=config.gigachat_scope,
        model=config.gigachat_model,
        proxy=config.gigachat_proxy,
    )

    await gigachat.check_connection()  # non-fatal: logs error but does not stop startup

    if config.bot_mode == "file_qa" and config.gigachat_file_id:
        gigachat.warn_if_file_unsupported()
        meta = await gigachat.check_file(config.gigachat_file_id)
        if meta:
            logger.info(
                "File OK — id=%s name=%s bytes=%s",
                meta.get("id"), meta.get("filename"), meta.get("bytes"),
            )
        else:
            logger.warning(
                "Could not verify file_id=%s — it may be expired or belong to a different account. "
                "Re-upload with: python upload_file.py <your_file>",
                config.gigachat_file_id,
            )

    client = create_client(config)
    handler = MessageHandler(client, gigachat, config)

    async def shutdown() -> None:
        logger.info("Shutting down...")
        await client.disconnect()
        await gigachat.close()

    setup_signal_handlers(shutdown)

    async with client:
        await client.start()
        await handler.initialize()
        await register_handlers(client, handler, config)
        logger.info("Userbot is running. Press Ctrl+C to stop.")
        await client.run_until_disconnected()

    await gigachat.close()
    logger.info("Userbot stopped.")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
