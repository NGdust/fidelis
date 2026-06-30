"""One-shot spec inference from a field signature plus a sample of values.

Everything that lives ABOVE the provider is here: prompt building (only the
field inventory + a small sample + the target model's JSON schema — never the
whole dataset), safe response parsing with retries, and anti-hallucination
post-processing.
"""

from __future__ import annotations

import json
import re
from typing import Optional, Type

from pydantic import BaseModel

from ..spec import Mapping, ParsingSpec, Spec, compute_signature, normalize_field_name
from ..transforms import available_transforms
from .base import LLMProvider

DEFAULT_CONFIDENCE_THRESHOLD = 0.8
MAX_RETRIES = 2

_SYSTEM_PROMPT = """\
You are a deterministic field-mapping spec generator. You are given an inventory
of the source fields (the headers), a small sample of values, and the JSON schema
of the target model. Your task is to map the source fields onto the target model's
fields.

Hard rules:
- Use ONLY source fields from the provided inventory. NEVER invent fields that are
  not in the inventory.
- For each mapping, give a confidence from 0.0 to 1.0 indicating how sure you are
  of the match.
- Only propose a transform from the list of available ones. If unsure, leave it null.
- Respond with STRICTLY valid JSON following the given response schema, with no
  markdown and no explanations.
"""

_RESPONSE_SCHEMA = """\
{
  "mappings": [
    {"source": "<field name from the inventory>", "target": "<target model field>",
     "transform": "<transform name or null>", "confidence": <float 0..1>}
  ],
  "parsing": {"delimiter": "<or null>", "encoding": "<or null>",
              "quote_char": "<or null>"}
}
"""


def _format_domain_hints(domain_hints: object) -> str:
    """Render free-text or structured domain hints for the prompt (empty if none)."""

    if not domain_hints:
        return ""
    if isinstance(domain_hints, str):
        body = domain_hints.strip()
    else:
        body = json.dumps(domain_hints, ensure_ascii=False, indent=2, default=str)
    return (
        "Domain context (use it to disambiguate the mapping — expected types, "
        f"allowed values, units, ranges, codes):\n{body}\n\n"
    )


def build_user_prompt(
    field_names: list[str],
    sample: list[dict],
    model: Type[BaseModel],
    domain_hints: object = None,
) -> str:
    """Build the user prompt. Only the sample goes to the LLM, not the whole dataset."""

    schema = json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2)
    sample_json = json.dumps(sample, ensure_ascii=False, indent=2, default=str)
    return (
        f"Source field inventory:\n{json.dumps(field_names, ensure_ascii=False)}\n\n"
        f"Sample values (first {len(sample)} records):\n{sample_json}\n\n"
        f"Target model JSON schema:\n{schema}\n\n"
        f"{_format_domain_hints(domain_hints)}"
        f"Available transforms: {', '.join(available_transforms())}\n\n"
        f"Return JSON strictly following the schema:\n{_RESPONSE_SCHEMA}"
    )


def _extract_json(raw: str) -> dict:
    """Pull the first JSON object out of the response, tolerating wrappers/markdown fences."""

    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def _coerce_mappings(
    payload: dict,
    field_names: list[str],
    model: Type[BaseModel],
    confidence_threshold: float,
) -> list[Mapping]:
    """Post-process the LLM response: anti-hallucination + needs_review by threshold."""

    source_norm = {normalize_field_name(f): f for f in field_names}
    valid_targets = set(model.model_fields)
    result: list[Mapping] = []

    for raw in payload.get("mappings", []):
        if not isinstance(raw, dict):
            continue
        source = raw.get("source")
        target = raw.get("target")
        if not source or not target:
            continue
        # Anti-hallucination: source must be present in the source inventory.
        norm = normalize_field_name(source)
        if norm not in source_norm:
            continue
        # target must exist in the model.
        if target not in valid_targets:
            continue
        try:
            confidence = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        transform = raw.get("transform") or None
        status = "ok" if confidence >= confidence_threshold else "needs_review"
        result.append(
            Mapping(
                target=target,
                source=source_norm[norm],
                transform=transform,
                confidence=confidence,
                status=status,
            )
        )
    return result


def _coerce_parsing(payload: dict, raw_kind: str) -> Optional[ParsingSpec]:
    """Extract the parsing section only for text sources."""

    if raw_kind != "text":
        return None
    p = payload.get("parsing")
    if not isinstance(p, dict):
        return None
    parsing = ParsingSpec(
        delimiter=p.get("delimiter") or None,
        encoding=p.get("encoding") or None,
        quote_char=p.get("quote_char") or None,
    )
    if parsing.delimiter or parsing.encoding or parsing.quote_char:
        return parsing
    return None


def generate_spec(
    provider: LLMProvider,
    *,
    field_names: list[str],
    sample: list[dict],
    model: Type[BaseModel],
    raw_kind: str = "structured",
    generated_at: Optional[str] = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    parsing_hints: Optional[ParsingSpec] = None,
    domain_hints: object = None,
) -> Spec:
    """Generate a draft spec via the LLM, retrying on invalid JSON."""

    user = build_user_prompt(field_names, sample, model, domain_hints)

    last_err: Optional[Exception] = None
    payload: Optional[dict] = None
    for _ in range(MAX_RETRIES + 1):
        raw = provider.complete(_SYSTEM_PROMPT, user)
        try:
            payload = _extract_json(raw)
            break
        except (json.JSONDecodeError, ValueError) as exc:
            last_err = exc
            user = (
                build_user_prompt(field_names, sample, model, domain_hints)
                + "\n\nTHE PREVIOUS RESPONSE WAS INVALID JSON. Return ONLY valid JSON."
            )
    if payload is None:
        raise ValueError(f"LLM returned invalid JSON after retries: {last_err}")

    mappings = _coerce_mappings(payload, field_names, model, confidence_threshold)
    parsing = parsing_hints or _coerce_parsing(payload, raw_kind)

    return Spec(
        version=1,
        generated_by=provider.model,
        generated_at=generated_at,
        signature=compute_signature(field_names),
        parsing=parsing,
        mappings=mappings,
    )
