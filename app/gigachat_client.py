import asyncio
import logging
import time
import uuid
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_AUTH_URL   = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
_CHAT_URL   = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
_MODELS_URL = "https://gigachat.devices.sberbank.ru/api/v1/models"


class GigaChatClient:
    """Async client for the GigaChat API."""

    def __init__(
        self,
        auth_key: str,
        scope: str = "GIGACHAT_API_PERS",
        model: str = "GigaChat",
        proxy: Optional[str] = None,
    ) -> None:
        self._credentials = auth_key
        self._scope = scope
        self._model = model
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        if proxy:
            logger.info("GigaChat will use proxy: %s", proxy)
        # GigaChat uses a Russian CA cert; disable verification for simplicity.
        # In production, supply the proper CA bundle via verify= parameter.
        self._http = httpx.AsyncClient(verify=False, timeout=30.0, proxy=proxy)

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def _refresh_token(self) -> None:
        headers = {
            "Authorization": f"Basic {self._credentials}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        data = {"scope": self._scope}
        logger.debug("[AUTH] POST %s scope=%s", _AUTH_URL, self._scope)
        response = await self._http.post(_AUTH_URL, headers=headers, data=data)
        logger.debug("[AUTH] response %s", response.status_code)
        response.raise_for_status()
        result = response.json()
        self._access_token = result["access_token"]
        # expires_at comes in milliseconds
        self._token_expires_at = result["expires_at"] / 1000.0
        logger.info("GigaChat access token refreshed (expires_at=%s)", result["expires_at"])

    async def _ensure_token(self) -> None:
        # Refresh 60 s before expiry
        if not self._access_token or time.time() >= self._token_expires_at - 60:
            await self._refresh_token()

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict],
        max_retries: int = 3,
    ) -> str:
        """Send a chat request and return the assistant reply text."""
        last_exc: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                await self._ensure_token()
                headers = {
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                payload = {
                    "model": self._model,
                    "messages": messages,
                    "max_tokens": 512,
                    "temperature": 0.8,
                }
                logger.debug(
                    "[CHAT] POST %s model=%s turns=%d",
                    _CHAT_URL, self._model, len(messages),
                )
                response = await self._http.post(
                    _CHAT_URL, headers=headers, json=payload
                )
                logger.debug("[CHAT] response %s", response.status_code)
                if response.status_code == 401:
                    # Force token refresh on next attempt
                    self._access_token = None
                response.raise_for_status()
                result = response.json()
                choice = result["choices"][0]
                text = choice["message"]["content"].strip()
                finish = choice.get("finish_reason", "")
                usage = result.get("usage", {})
                logger.debug(
                    "[CHAT] finish=%s tokens(prompt=%s completion=%s) response=%.200s",
                    finish,
                    usage.get("prompt_tokens", "?"),
                    usage.get("completion_tokens", "?"),
                    text,
                )
                return text
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "GigaChat HTTP error (attempt %d/%d): %s",
                    attempt + 1,
                    max_retries,
                    exc,
                )
                last_exc = exc
            except Exception as exc:
                logger.error(
                    "GigaChat request failed (attempt %d/%d): %s",
                    attempt + 1,
                    max_retries,
                    exc,
                )
                last_exc = exc

            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)  # exponential back-off

        raise RuntimeError(
            f"GigaChat failed after {max_retries} attempts"
        ) from last_exc

    async def chat_with_file(
        self,
        question: str,
        file_id: str,
        system_prompt: Optional[str] = None,
        max_retries: int = 3,
    ) -> str:
        """Send a question with a GigaChat file attachment and return the reply.

        Uses function_call=auto so the model can search the document content.
        """
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({
            "role": "user",
            "content": question,
            "attachments": [file_id],
        })

        last_exc: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                await self._ensure_token()
                headers = {
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                payload = {
                    "model": self._model,
                    "messages": messages,
                    "function_call": "auto",
                    "max_tokens": 1024,
                    "temperature": 0.3,
                }
                logger.debug(
                    "[CHAT_FILE] POST %s model=%s file_id=%s question=%.100s",
                    _CHAT_URL, self._model, file_id, question,
                )
                response = await self._http.post(
                    _CHAT_URL, headers=headers, json=payload
                )
                logger.debug("[CHAT_FILE] response %s", response.status_code)
                if response.status_code == 401:
                    self._access_token = None
                response.raise_for_status()
                result = response.json()
                choice = result["choices"][0]
                text = choice["message"]["content"].strip()
                finish = choice.get("finish_reason", "")
                usage = result.get("usage", {})
                logger.debug(
                    "[CHAT_FILE] finish=%s tokens(prompt=%s completion=%s) response=%.200s",
                    finish,
                    usage.get("prompt_tokens", "?"),
                    usage.get("completion_tokens", "?"),
                    text,
                )
                return text
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "GigaChat file-chat HTTP error (attempt %d/%d): %s",
                    attempt + 1, max_retries, exc,
                )
                last_exc = exc
            except Exception as exc:
                logger.error(
                    "GigaChat file-chat failed (attempt %d/%d): %s",
                    attempt + 1, max_retries, exc,
                )
                last_exc = exc

            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)

        raise RuntimeError(
            f"GigaChat file-chat failed after {max_retries} attempts"
        ) from last_exc

    # ------------------------------------------------------------------
    # Models & health check
    # ------------------------------------------------------------------

    async def list_models(self) -> list[str]:
        """Return a list of model IDs available for the current credentials."""
        await self._ensure_token()
        headers = {"Authorization": f"Bearer {self._access_token}", "Accept": "application/json"}
        response = await self._http.get(_MODELS_URL, headers=headers)
        response.raise_for_status()
        data = response.json()
        return [m["id"] for m in data.get("data", [])]

    async def check_connection(self) -> bool:
        """Verify auth and confirm the configured model is available.

        Returns True if everything is OK, False if GigaChat is unreachable.
        Never raises — the bot should start regardless so Telegram listener works.
        """
        logger.info("Checking GigaChat connection (scope=%s)...", self._scope)
        try:
            await self._refresh_token()
            logger.info("GigaChat auth OK — token received")
        except Exception as exc:
            logger.error(
                "GigaChat auth failed: %s\n"
                "  → Bot will start but CANNOT reply until GigaChat is reachable.\n"
                "  → If the server is outside Russia, set GIGACHAT_PROXY= in .env",
                exc,
            )
            return False

        try:
            models = await self.list_models()
            logger.info("Available GigaChat models: %s", ", ".join(models))
            if self._model in models:
                logger.info("Configured model '%s' — OK", self._model)
            else:
                logger.warning(
                    "Configured model '%s' NOT found in available models %s. "
                    "Check GIGACHAT_MODEL in .env.",
                    self._model,
                    models,
                )
        except Exception as exc:
            logger.error("Could not fetch model list: %s", exc)

        return True

    async def close(self) -> None:
        await self._http.aclose()
