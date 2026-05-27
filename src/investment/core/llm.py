"""Anthropic LLM client with schema-validated retry.

Usage::

    from investment.core.llm import call_llm, call_llm_with_schema
    reply = call_llm("What is the PE of 600219?")
    result = call_llm_with_schema(prompt, MyPydanticModel)
"""
from __future__ import annotations

import os
from typing import Type, TypeVar

import yaml
from anthropic import Anthropic
from pydantic import BaseModel

from .settings import CONFIG_DIR

T = TypeVar("T", bound=BaseModel)

_config_path = CONFIG_DIR / "llm.yaml"
_config: dict | None = None


def _load_config() -> dict:
    global _config
    if _config is None:
        if _config_path.exists():
            _config = yaml.safe_load(_config_path.read_text(encoding="utf-8"))
        else:
            _config = {}
    return _config


def _get_client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Export it or add to .env file."
        )
    return Anthropic(api_key=api_key)


def _resolve_model(model: str | None = None) -> tuple[str, dict]:
    cfg = _load_config()
    model_name = model or cfg.get("default_model", "claude-sonnet-4-5")
    model_cfg = cfg.get("models", {}).get(model_name, {})
    return model_cfg.get("model_id", model_name), model_cfg


def call_llm(
    prompt: str,
    model: str | None = None,
    system_prompt: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """Call Anthropic Claude with a text prompt. Returns the reply text."""
    client = _get_client()
    model_id, model_cfg = _resolve_model(model)

    kwargs: dict = {"model": model_id, "max_tokens": max_tokens or model_cfg.get("max_tokens", 4096)}
    if system_prompt:
        kwargs["system"] = system_prompt
    kwargs["temperature"] = temperature if temperature is not None else model_cfg.get("temperature", 0.3)

    messages = [{"role": "user", "content": prompt}]
    response = client.messages.create(messages=messages, **kwargs)
    return response.content[0].text


def call_llm_with_schema(
    prompt: str,
    schema: Type[T],
    model: str | None = None,
    system_prompt: str | None = None,
    max_retries: int = 3,
) -> T:
    """Call Claude with Pydantic schema validation.

    On validation failure, retries up to ``max_retries`` times with
    decreasing temperature (0.3 → 0.1 → 0.0) and validation errors
    fed back to the model.

    Returns the validated Pydantic model instance.
    """
    client = _get_client()
    model_id, model_cfg = _resolve_model(model)
    cfg = _load_config()
    retry_cfg = cfg.get("retry", {})
    temperatures = retry_cfg.get("temperatures", [0.3, 0.1, 0.0])
    max_tokens = model_cfg.get("max_tokens", 4096)

    # Inject schema requirement into system prompt
    schema_desc = _schema_description(schema)
    full_system = (
        f"{system_prompt}\n\n"
        f"OUTPUT REQUIREMENT: Return ONLY valid JSON conforming to this schema:\n"
        f"{schema_desc}\n"
        f"Do NOT include markdown fences, explanations, or any text outside the JSON object."
        if system_prompt
        else f"Return ONLY valid JSON. Do NOT include markdown fences or extra text.\n{schema_desc}"
    )

    last_error: str | None = None
    for attempt in range(max_retries):
        temp = temperatures[min(attempt, len(temperatures) - 1)]
        user_prompt = prompt
        if last_error:
            user_prompt = (
                f"{prompt}\n\n"
                f"[PREVIOUS OUTPUT WAS INVALID. Validation error: {last_error}]\n"
                f"Please fix the JSON and try again. Return ONLY valid JSON."
            )

        messages = [{"role": "user", "content": user_prompt}]
        response = client.messages.create(
            model=model_id,
            max_tokens=max_tokens,
            temperature=temp,
            system=full_system,
            messages=messages,
        )
        raw = response.content[0].text

        try:
            return _parse_json_response(raw, schema)
        except Exception as exc:
            last_error = str(exc)
            if attempt == max_retries - 1:
                raise RuntimeError(
                    f"LLM schema validation failed after {max_retries} attempts: {last_error}"
                ) from exc

    raise RuntimeError("Unreachable")  # placate type checker


def _schema_description(schema: Type[BaseModel]) -> str:
    """Generate a concise JSON schema description for the system prompt."""
    import json
    return json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)


def _parse_json_response(raw: str, schema: Type[T]) -> T:
    """Extract JSON from LLM output and validate against schema."""
    import json

    text = raw.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove opening fence (may have language tag)
        if lines[0].startswith("```"):
            lines = lines[1:]
        # Remove closing fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Try to find JSON object boundaries
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]

    data = json.loads(text)
    return schema.model_validate(data)
