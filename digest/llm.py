"""Thin Claude wrapper: JSON structured outputs, retries, cost tracking."""

from __future__ import annotations

import json
import time

import anthropic

# USD per million tokens (input, output). Update if prices change:
# https://platform.claude.com/docs/en/about-claude/pricing
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
}


class LLM:
    def __init__(self, client: anthropic.Anthropic | None = None):
        self.client = client or anthropic.Anthropic()
        self.cost = 0.0
        self.calls = 0

    def ask_structured(
        self, model: str, system: str, user: str, schema: dict, max_tokens: int = 4000
    ) -> dict:
        """Ask for JSON matching `schema` using structured outputs.

        The API constrains the reply to the schema, so it is always valid JSON
        (unescaped quotes etc. can't break it). Works on Haiku/Sonnet/Opus 5.5.
        """
        last_err = None
        for attempt in range(4):
            try:
                resp = self.client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    # the system prompt (profile) is identical across calls → cache it
                    system=[
                        {
                            "type": "text",
                            "text": system,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": user}],
                    output_config={"format": {"type": "json_schema", "schema": schema}},
                )
                self._track(model, resp.usage)
                if resp.stop_reason == "max_tokens":
                    raise ValueError("reply was cut off (max_tokens too small)")
                if resp.stop_reason == "refusal":
                    raise ValueError("model declined this request")
                text = "".join(b.text for b in resp.content if b.type == "text")
                return json.loads(text)
            except (
                anthropic.RateLimitError,
                anthropic.APIConnectionError,
                anthropic.InternalServerError,
            ) as e:
                last_err = e
                wait = 2**attempt * 5
                print(f"    API busy ({type(e).__name__}), retrying in {wait}s")
                time.sleep(wait)
            except ValueError as e:
                last_err = e
                print(f"    bad reply ({e}); retrying")
        raise RuntimeError(f"Claude call failed after retries: {last_err}")

    def _track(self, model, usage):
        pin, pout = PRICES.get(model, (0, 0))
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.cost += (
            usage.input_tokens * pin
            + cache_write * pin * 1.25
            + cache_read * pin * 0.1
            + usage.output_tokens * pout
        ) / 1e6
        self.calls += 1
