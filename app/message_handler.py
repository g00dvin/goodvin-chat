import asyncio
import logging
import re
import time
from collections import deque
from typing import Optional

from telethon import TelegramClient
from telethon.tl.types import Message, User

from .config import Config
from .gigachat_client import GigaChatClient
from .prompt_builder import ContextMessage, PromptBuilder

logger = logging.getLogger(__name__)

_FILE_QA_SYSTEM = (
    "Ты — ассистент, отвечающий на вопросы только по загруженному руководству.\n"
    "Не используй внешние знания, не добавляй информацию от себя.\n"
    "Если ответ не найден в файле, напиши: «Информация отсутствует в руководстве».\n"
    "Формулируй ответы кратко и по делу."
)


class MessageHandler:
    def __init__(
        self,
        client: TelegramClient,
        gigachat: GigaChatClient,
        config: Config,
    ) -> None:
        self._client = client
        self._gigachat = gigachat
        self._config = config
        self._prompt_builder = PromptBuilder(
            config.system_prompt, config.max_context_messages
        )

        # Thread contexts: thread_key → conversation history
        # A thread starts on the first trigger and grows as users reply to us.
        self._threads: dict[str, deque[ContextMessage]] = {}

        # Our sent messages: sent_msg_id → thread_key
        # Used to detect when someone replies to our message (thread continuation).
        self._our_messages: dict[int, str] = {}

        # Rate-limit slots per thread_key
        self._last_reply: dict[str, float] = {}

        self._me: Optional[User] = None

        # file_qa mode: compiled trigger pattern "Кирилл, подскажи <вопрос>"
        name = re.escape(config.bot_name)
        self._file_qa_pattern = re.compile(
            rf"^{name}[,\s]+подскажи\s+(.+)",
            re.IGNORECASE | re.DOTALL,
        )

    async def initialize(self) -> None:
        self._me = await self._client.get_me()
        logger.info("Logged in as @%s (id=%s)", self._me.username, self._me.id)

    # ------------------------------------------------------------------
    # Topic helpers
    # ------------------------------------------------------------------

    def _get_message_topic_id(self, message: Message) -> Optional[int]:
        rt = getattr(message, "reply_to", None)
        if rt is None:
            return None
        top_id = getattr(rt, "reply_to_top_id", None)
        if top_id:
            return top_id
        if getattr(rt, "forum_topic", False):
            return getattr(rt, "reply_to_msg_id", None)
        return None

    def _is_in_target_topic(self, message: Message) -> bool:
        if self._config.topic_id is None:
            return True
        topic_id = self._get_message_topic_id(message)
        if self._config.topic_id == 1 and topic_id is None:
            return True
        return topic_id == self._config.topic_id

    def _thread_key(self, chat_id: int, topic_id: Optional[int], root_msg_id: int) -> str:
        return f"{chat_id}:{topic_id or 0}:{root_msg_id}"

    # ------------------------------------------------------------------
    # Trigger detection
    # ------------------------------------------------------------------

    def _has_trigger_word(self, text: str) -> bool:
        low = text.lower()
        return any(w.lower() in low for w in self._config.trigger_words)

    def _mentions_me(self, text: str) -> bool:
        if not self._me or not self._me.username:
            return False
        return f"@{self._me.username}".lower() in text.lower()

    async def _is_reply_to_me_via_api(self, message: Message) -> bool:
        """Fallback check for replies to our messages sent before the bot started."""
        reply_to_id = getattr(message, "reply_to_msg_id", None)
        if not reply_to_id:
            return False
        try:
            replied = await self._client.get_messages(message.chat_id, ids=reply_to_id)
            return replied is not None and replied.sender_id == self._me.id
        except Exception as exc:
            logger.error("Could not fetch replied-to message: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _reserve_slot(self, key: str) -> float:
        """Claim the reply slot immediately; return seconds to wait.

        Reserving upfront prevents concurrent coroutines from both
        passing the rate check before either finishes.
        """
        now = time.time()
        last = self._last_reply.get(key, 0.0)
        wait = max(0.0, self._config.rate_limit_seconds - (now - last))
        self._last_reply[key] = now + wait
        return wait

    # ------------------------------------------------------------------
    # Thread resolution
    # ------------------------------------------------------------------

    async def _resolve_thread(
        self, message: Message, topic_id: Optional[int]
    ) -> tuple[Optional[str], str]:
        """Return (thread_key, trigger_reason) or (None, "") if not triggered."""
        reply_to_id = getattr(message, "reply_to_msg_id", None)
        text = getattr(message, "text", "") or getattr(message, "message", "") or ""

        # Priority 1: reply to one of our tracked messages → continue that thread
        if reply_to_id and reply_to_id in self._our_messages:
            thread_key = self._our_messages[reply_to_id]
            logger.info("Thread continuation (reply→msg %d): %.60s", reply_to_id, text)
            return thread_key, "reply"

        # Priority 2: @mention → new thread
        if self._mentions_me(text):
            key = self._thread_key(message.chat_id, topic_id, message.id)
            logger.info("New thread (@mention): %.60s", text)
            return key, "mention"

        # Priority 3: trigger word → new thread
        if self._has_trigger_word(text):
            key = self._thread_key(message.chat_id, topic_id, message.id)
            logger.info("New thread (trigger word): %.60s", text)
            return key, "trigger_word"

        # Priority 4: reply to our old message not in memory (e.g. after restart) → new thread
        if reply_to_id and await self._is_reply_to_me_via_api(message):
            key = self._thread_key(message.chat_id, topic_id, message.id)
            logger.info("New thread (reply to pre-start msg %d): %.60s", reply_to_id, text)
            return key, "reply_old"

        return None, ""

    # ------------------------------------------------------------------
    # File Q&A mode
    # ------------------------------------------------------------------

    async def _handle_file_qa(self, message: Message, question: str) -> None:
        """Answer a single question using the configured GigaChat file."""
        file_id = self._config.gigachat_file_id
        if not file_id:
            logger.error("file_qa mode: GIGACHAT_FILE_ID is not set")
            return

        rate_key = f"file_qa:{message.chat_id}"
        wait = self._reserve_slot(rate_key)
        if wait > 0:
            logger.info("Rate-limit: waiting %.1fs (file_qa)", wait)
            await asyncio.sleep(wait)

        logger.info("[FILE_QA] file_id=%s question=%.100s", file_id, question)

        try:
            reply_text = await self._gigachat.chat_with_file(
                question=question,
                file_id=file_id,
                system_prompt=_FILE_QA_SYSTEM,
            )
        except Exception as exc:
            logger.error("GigaChat file-chat failed: %s", exc)
            return

        if not reply_text:
            return

        logger.debug("[FILE_QA RESPONSE] %.300s", reply_text)

        typing_seconds = len(reply_text) / 4
        try:
            async with self._client.action(message.chat_id, "typing"):
                await asyncio.sleep(typing_seconds)
        except Exception as exc:
            logger.debug("Typing action failed (non-fatal): %s", exc)

        try:
            await self._client.send_message(
                message.chat_id,
                reply_text,
                reply_to=message.id,
            )
            logger.info(
                "[FILE_QA] Sent reply (%.1fs typing): %.80s",
                typing_seconds, reply_text,
            )
        except Exception as exc:
            logger.error("Failed to send file_qa reply: %s", exc)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def handle(self, message: Message) -> None:
        if self._me is None:
            return

        if message.sender_id == self._me.id:
            return

        text: str = getattr(message, "text", "") or getattr(message, "message", "") or ""
        if not text.strip():
            return

        topic_id = self._get_message_topic_id(message)

        logger.debug(
            "[MSG] chat=%s topic=%s msg_id=%s sender_id=%s | %.120s",
            message.chat_id, topic_id, message.id, message.sender_id, text,
        )

        if not self._is_in_target_topic(message):
            logger.debug("[SKIP] topic %s != target %s", topic_id, self._config.topic_id)
            return

        # Route to the correct mode
        if self._config.bot_mode == "file_qa":
            match = self._file_qa_pattern.match(text.strip())
            if match:
                question = match.group(1).strip()
                await self._handle_file_qa(message, question)
            else:
                logger.debug("[FILE_QA SKIP] pattern not matched: %.80s", text)
            return

        # Resolve thread
        thread_key, trigger_reason = await self._resolve_thread(message, topic_id)
        if thread_key is None:
            logger.debug("[SKIP] no trigger: %.80s", text)
            return

        # Ensure thread context exists
        if thread_key not in self._threads:
            self._threads[thread_key] = deque(maxlen=self._config.max_context_messages * 2)

        # Resolve sender display name
        sender = ""
        raw_sender = getattr(message, "sender", None)
        if raw_sender:
            sender = (
                getattr(raw_sender, "first_name", "")
                or getattr(raw_sender, "username", "")
                or ""
            )

        # Add incoming message to thread
        self._threads[thread_key].append(
            ContextMessage(role="user", content=text, sender=sender)
        )

        # Rate limit per thread
        wait = self._reserve_slot(thread_key)
        if wait > 0:
            logger.info("Rate-limit: waiting %.1fs (thread=%s)", wait, thread_key)
            await asyncio.sleep(wait)

        # Build prompt from this thread's history (exclude the just-added message)
        context_snapshot = list(self._threads[thread_key])[:-1]
        prompt = self._prompt_builder.build(context_snapshot, text, sender)

        logger.debug(
            "[GIGACHAT REQUEST] trigger=%s thread=%s history=%d prompt_turns=%d\n%s",
            trigger_reason,
            thread_key,
            len(context_snapshot),
            len(prompt),
            "\n".join(
                f"  [{m['role'].upper()}] {m['content'][:200]}"
                for m in prompt
            ),
        )

        # Generate reply
        try:
            reply_text = await self._gigachat.chat(prompt)
        except Exception as exc:
            logger.error("GigaChat generation failed: %s", exc)
            return

        if not reply_text:
            return

        logger.debug("[GIGACHAT RESPONSE] %.300s", reply_text)

        # Simulate typing: duration = chars / 4 seconds
        typing_seconds = len(reply_text) / 4
        logger.debug("[TYPING] %.1fs (%d chars)", typing_seconds, len(reply_text))
        try:
            async with self._client.action(message.chat_id, "typing"):
                await asyncio.sleep(typing_seconds)
        except Exception as exc:
            logger.debug("Typing action failed (non-fatal): %s", exc)

        # Send message
        try:
            sent = await self._client.send_message(
                message.chat_id,
                reply_text,
                reply_to=message.id,
            )
            logger.info(
                "Sent reply (%.1fs typing, thread=%s): %.80s",
                typing_seconds, thread_key, reply_text,
            )
        except Exception as exc:
            logger.error("Failed to send message: %s", exc)
            return

        # Register our message so future replies to it continue this thread
        self._our_messages[sent.id] = thread_key

        # Add our reply to thread context
        self._threads[thread_key].append(
            ContextMessage(role="assistant", content=reply_text)
        )
