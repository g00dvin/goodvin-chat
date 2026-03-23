from dataclasses import dataclass


@dataclass
class ContextMessage:
    role: str       # "user" | "assistant"
    content: str
    sender: str = ""


class PromptBuilder:
    """Assembles the message list sent to GigaChat."""

    def __init__(self, system_prompt: str, max_context: int = 10) -> None:
        self._system_prompt = system_prompt
        self._max_context = max_context

    def build(
        self,
        context: list[ContextMessage],
        current_text: str,
        sender: str = "",
    ) -> list[dict]:
        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt}
        ]

        # Take only the last N context entries
        for msg in context[-self._max_context:]:
            if msg.role == "user" and msg.sender:
                content = f"{msg.sender}: {msg.content}"
            else:
                content = msg.content
            messages.append({"role": msg.role, "content": content})

        # Current incoming message
        current_content = f"{sender}: {current_text}" if sender else current_text
        messages.append({"role": "user", "content": current_content})

        return messages
