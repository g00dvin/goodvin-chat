import os
from dataclasses import dataclass, field
from typing import Optional, Union


@dataclass
class Config:
    # Telegram
    tg_api_id: int
    tg_api_hash: str
    tg_session: str

    # Target chat
    chat_id: Union[int, str]
    topic_id: Optional[int]

    # Behavior
    trigger_words: list[str]
    system_prompt: str
    max_context_messages: int

    # Rate limiting
    rate_limit_seconds: float

    # GigaChat
    gigachat_auth_key: str  # base64 auth key from developers.sber.ru/studio → "Авторизационные данные"
    gigachat_scope: str
    gigachat_model: str
    gigachat_proxy: Optional[str]  # e.g. "http://user:pass@host:port" or "socks5://..."

    # Debug
    debug: bool

    @classmethod
    def from_env(cls) -> "Config":
        trigger_words_raw = os.environ.get("TRIGGER_WORDS", "")
        trigger_words = [w.strip() for w in trigger_words_raw.split(",") if w.strip()]

        topic_id_raw = os.environ.get("TOPIC_ID")
        topic_id = int(topic_id_raw) if topic_id_raw else None

        chat_id_raw = os.environ["CHAT_ID"]
        try:
            chat_id: Union[int, str] = int(chat_id_raw)
        except ValueError:
            chat_id = chat_id_raw  # Allow username like "@mychat"

        return cls(
            tg_api_id=int(os.environ["TG_API_ID"]),
            tg_api_hash=os.environ["TG_API_HASH"],
            tg_session=os.environ.get("TG_SESSION", "userbot"),
            chat_id=chat_id,
            topic_id=topic_id,
            trigger_words=trigger_words,
            system_prompt=os.environ.get("SYSTEM_PROMPT", "Ты helpful assistant."),
            max_context_messages=int(os.environ.get("MAX_CONTEXT_MESSAGES", "10")),
            rate_limit_seconds=float(os.environ.get("RATE_LIMIT_SECONDS", "5")),
            gigachat_auth_key=os.environ["GIGACHAT_AUTH_KEY"],
            gigachat_scope=os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
            gigachat_model=os.environ.get("GIGACHAT_MODEL", "GigaChat"),
            gigachat_proxy=os.environ.get("GIGACHAT_PROXY") or None,
            debug=os.environ.get("DEBUG", "0").strip() == "1",
        )
