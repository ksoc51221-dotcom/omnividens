"""Thin API-client interface for the router, so tests can use a fake (no network, no key)."""

from typing import Protocol

HAIKU_MODEL = "claude-haiku-5-5"

ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "needs_clarification": {"type": "boolean"},
        "question": {"type": ["string", "null"]},
        "task": {"type": ["string", "null"]},
        "skill": {"type": ["string", "null"]},
        "model": {"type": ["string", "null"]},
        "effort": {"type": ["string", "null"]},
        "project": {"type": ["string", "null"]},
    },
    "required": ["needs_clarification", "question", "task", "skill", "model", "effort", "project"],
    "additionalProperties": False,
}


class RouterClient(Protocol):
    def complete(self, system: str, user: str) -> dict:
        """Send one prompt, return the parsed JSON object matching ROUTER_SCHEMA."""


class AnthropicRouterClient:
    """Real client: Claude Haiku with structured output. The key comes from the environment."""

    def __init__(self, model: str = HAIKU_MODEL) -> None:
        import anthropic  # imported here so tests and rule-only use never need the SDK

        self._client = anthropic.Anthropic()
        self._model = model

    def complete(self, system: str, user: str) -> dict:
        import json

        response = self._client.messages.create(
            model=self._model,
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": ROUTER_SCHEMA}},
        )
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)
