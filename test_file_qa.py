"""
Interactive test script for GigaChat file Q&A.

Loads credentials from .env, then loops asking for questions
that the model should answer from the uploaded file.

Usage:
    python test_file_qa.py            # debug from .env (DEBUG=1/0)
    python test_file_qa.py --debug    # force verbose debug output
    python test_file_qa.py --no-debug # force quiet output

Type 'quit' or 'exit' (or press Ctrl+C) to stop.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env from the same directory as this script
# ---------------------------------------------------------------------------
load_dotenv(Path(__file__).parent / ".env")

_AUTH_URL  = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
_CHAT_URL  = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
_FILES_URL = "https://gigachat.devices.sberbank.ru/api/v1/files"

_FILE_CAPABLE_MODELS = {"GigaChat-Pro", "GigaChat-2-Max", "GigaChat-Max", "GigaChat-2-Pro"}

log = logging.getLogger("test_file_qa")


# ---------------------------------------------------------------------------
# GigaChat helpers
# ---------------------------------------------------------------------------

class _Client:
    def __init__(self, auth_key: str, scope: str, model: str, proxy: Optional[str]) -> None:
        self._auth_key = auth_key
        self._scope = scope
        self._model = model
        self._token: Optional[str] = None
        self._token_exp: float = 0.0
        self._http = httpx.AsyncClient(verify=False, timeout=60.0, proxy=proxy or None)

    async def _refresh_token(self) -> None:
        log.debug("[AUTH] POST %s scope=%s", _AUTH_URL, self._scope)
        resp = await self._http.post(
            _AUTH_URL,
            headers={
                "Authorization": f"Basic {self._auth_key}",
                "RqUID": str(uuid.uuid4()),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={"scope": self._scope},
        )
        log.debug("[AUTH] %s", resp.status_code)
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_exp = data["expires_at"] / 1000.0
        log.debug("[AUTH] token expires_at=%s", data["expires_at"])

    async def _ensure_token(self) -> None:
        if not self._token or time.time() >= self._token_exp - 60:
            await self._refresh_token()

    async def check_file(self, file_id: str) -> Optional[dict]:
        await self._ensure_token()
        url = f"{_FILES_URL}/{file_id}"
        log.debug("[FILE] GET %s", url)
        resp = await self._http.get(
            url,
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
        )
        log.debug("[FILE] %s  body=%.300s", resp.status_code, resp.text)
        if not resp.is_success:
            return None
        return resp.json()

    async def ask(self, question: str, file_id: str, system_prompt: Optional[str]) -> str:
        await self._ensure_token()
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": question, "attachments": [file_id]})

        payload = {
            "model": self._model,
            "messages": messages,
            "function_call": "auto",
            "max_tokens": 1024,
            "temperature": 0.3,
        }
        log.debug("[CHAT] payload=\n%s", json.dumps(payload, ensure_ascii=False, indent=2))

        resp = await self._http.post(
            _CHAT_URL,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=payload,
        )
        log.debug("[CHAT] %s  body=%s", resp.status_code, resp.text)

        if resp.status_code == 401:
            self._token = None

        resp.raise_for_status()
        result = resp.json()
        usage = result.get("usage", {})
        choice = result["choices"][0]
        log.debug(
            "[CHAT] finish=%s prompt_tokens=%s completion_tokens=%s",
            choice.get("finish_reason"),
            usage.get("prompt_tokens", "?"),
            usage.get("completion_tokens", "?"),
        )
        text = choice["message"]["content"].strip()
        prompt_tok = usage.get("prompt_tokens", -1)
        if prompt_tok == 0:
            log.warning(
                "[CHAT] prompt_tokens=0 — file content NOT loaded into context. "
                "The file may still be indexing. Wait 1-3 min and retry."
            )
        return text

    async def close(self) -> None:
        await self._http.aclose()


# ---------------------------------------------------------------------------
# Main REPL
# ---------------------------------------------------------------------------

async def main(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # suppress httpx noise unless debugging
    if not debug:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    auth_key  = os.environ.get("GIGACHAT_AUTH_KEY", "").strip()
    scope     = os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS").strip()
    model     = os.environ.get("GIGACHAT_MODEL", "GigaChat").strip()
    file_id   = os.environ.get("GIGACHAT_FILE_ID", "").strip()
    proxy     = os.environ.get("GIGACHAT_PROXY", "").strip() or None
    sys_prompt = os.environ.get("SYSTEM_PROMPT", "").strip() or None

    # Validate required vars
    missing = [k for k, v in [("GIGACHAT_AUTH_KEY", auth_key), ("GIGACHAT_FILE_ID", file_id)] if not v]
    if missing:
        print(f"[ERROR] Missing env vars: {', '.join(missing)}")
        print("        Set them in .env or export before running.")
        sys.exit(1)

    if model not in _FILE_CAPABLE_MODELS:
        print(
            f"[WARN]  Model '{model}' may NOT support file attachments.\n"
            f"        For reliable file Q&A use: GigaChat-Pro or GigaChat-2-Max\n"
            f"        Set GIGACHAT_MODEL=GigaChat-Pro in .env\n"
        )

    client = _Client(auth_key, scope, model, proxy)

    print(f"\n=== GigaChat File Q&A test ===")
    print(f"  model   : {model}")
    print(f"  scope   : {scope}")
    print(f"  file_id : {file_id}")
    print(f"  debug   : {debug}")
    print()

    # Verify file accessibility
    print("Checking file accessibility...", end=" ", flush=True)
    file_ready = False
    try:
        meta = await client.check_file(file_id)
        if meta:
            modalities = meta.get("modalities") or []
            status = "indexed" if modalities else "indexing..."
            print(
                f"OK  (name={meta.get('filename', '?')}  "
                f"bytes={meta.get('bytes', '?'):,}  "
                f"modalities={modalities or '[]'}  status={status})"
            )
            if not modalities:
                print(
                    "\n[WARN] modalities=[] — GigaChat is still indexing the file.\n"
                    "       Large PDFs can take 1-3 minutes to process after upload.\n"
                    "       If prompt_tokens=0 in responses, wait and retry.\n"
                )
            file_ready = True
        else:
            print("FAILED — file not found or expired.")
            print("[WARN] File may be expired. Re-upload with: python upload_file.py <your_file>")
    except Exception as exc:
        print(f"ERROR: {exc}")

    print("\nType your question (or 'quit' / 'exit' / Ctrl+C to stop):\n")

    try:
        while True:
            try:
                question = input("You: ").strip()
            except EOFError:
                break

            if not question:
                continue
            if question.lower() in {"quit", "exit"}:
                break

            print("Bot: ", end="", flush=True)
            try:
                answer = await client.ask(question, file_id, sys_prompt)
                print(answer)
            except httpx.HTTPStatusError as exc:
                print(f"[HTTP ERROR {exc.response.status_code}] {exc.response.text[:300]}")
            except Exception as exc:
                print(f"[ERROR] {exc}")
            print()
    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GigaChat file Q&A interactive tester")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--debug",    action="store_true",  help="Force verbose debug output")
    group.add_argument("--no-debug", action="store_true",  help="Force quiet output")
    args = parser.parse_args()

    if args.debug:
        debug = True
    elif args.no_debug:
        debug = False
    else:
        debug = os.environ.get("DEBUG", "0").strip() == "1"

    asyncio.run(main(debug))
