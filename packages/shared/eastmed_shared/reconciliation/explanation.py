"""Optional English explanation agent (blueprint work order 4, sections 19, 24).

A versioned callable with typed inputs and outputs and no tools beyond reading the
supplied result. It receives the deterministic result, the permitted reference IDs and
the exact numbers rendered by the calculator, and returns structured sentences that
each cite their evidence. Numbers are checked against the calculator's own fields, so a
model-invented digit is dropped; a sentence without valid references is dropped. If
nothing survives, or the model fails after a bounded retry/failover, the result is
NARRATIVE_WITHHELD and the deterministic report still completes.

The model is injected as a port so the agent is fully testable with fake fixtures that
exercise every terminal failure class; no real provider is contacted here. An
unqualified model fingerprint is never called.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

PROMPT_FINGERPRINT = "recon_explanation_prompt_v1"
SCHEMA_FINGERPRINT = "recon_explanation_schema_v1"
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_ALLOWED_KINDS = frozenset({"reported", "calculated", "assumed"})
MAX_SENTENCES = 20
MAX_SENTENCE_CHARS = 400


class ModelFailureKind(StrEnum):
    REFUSAL = "refusal"
    INVALID_JSON = "invalid_json"
    TRUNCATION = "truncation"
    TOOL_MISUSE = "tool_misuse"
    PROVIDER_OUTAGE = "provider_outage"


class ModelError(Exception):
    def __init__(self, kind: ModelFailureKind, detail: str = "") -> None:
        super().__init__(detail or kind.value)
        self.kind = kind


@dataclass(frozen=True)
class ModelRequest:
    prompt_fingerprint: str
    schema_fingerprint: str
    scope: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ModelResponse:
    text: str
    model_fingerprint: str
    cost_units: int


@runtime_checkable
class ExplanationModel(Protocol):
    @property
    def fingerprint(self) -> str: ...

    def generate(self, request: ModelRequest) -> ModelResponse: ...


@dataclass(frozen=True)
class ExplanationContext:
    """What the agent is permitted to say, supplied by the broker (never the model)."""

    allowed_references: frozenset[str]
    allowed_numbers: frozenset[str]
    scope: str = "supported_variance"


@dataclass(frozen=True)
class ExplanationSentence:
    text: str
    references: tuple[str, ...]
    kind: str


class ExplanationStatus(StrEnum):
    PRODUCED = "produced"
    NARRATIVE_WITHHELD = "narrative_withheld"


@dataclass(frozen=True)
class ExplanationOutcome:
    status: ExplanationStatus
    sentences: tuple[ExplanationSentence, ...] = ()
    withheld_reason: str | None = None
    attempts: int = 0
    cost_units: int = 0
    model_fingerprint: str | None = None
    failures: tuple[str, ...] = field(default_factory=tuple)

    @property
    def withheld(self) -> bool:
        return self.status is ExplanationStatus.NARRATIVE_WITHHELD


class _ParseError(Exception):
    pass


def _canonical_number(token: str) -> str | None:
    try:
        return str(Decimal(token))
    except InvalidOperation:
        return None


def _numbers_supported(text: str, allowed: frozenset[str]) -> bool:
    allowed_canonical = {c for c in (_canonical_number(a) for a in allowed) if c is not None}
    for token in _NUMBER.findall(text):
        canonical = _canonical_number(token)
        if canonical is None or canonical not in allowed_canonical:
            return False
    return True


def _sentence_supported(sentence: ExplanationSentence, context: ExplanationContext) -> bool:
    if sentence.kind not in _ALLOWED_KINDS:
        return False
    if not sentence.references:
        return False
    if any(ref not in context.allowed_references for ref in sentence.references):
        return False
    return _numbers_supported(sentence.text, context.allowed_numbers)


def _parse_sentences(text: str) -> list[ExplanationSentence]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _ParseError("not valid JSON") from exc
    if not isinstance(payload, list):
        raise _ParseError("expected a list of sentences")
    if len(payload) > MAX_SENTENCES:
        raise _ParseError("too many sentences")
    sentences: list[ExplanationSentence] = []
    for item in payload:
        if not isinstance(item, dict):
            raise _ParseError("sentence is not an object")
        text_value = item.get("text")
        references = item.get("references")
        kind = item.get("kind")
        if not isinstance(text_value, str) or not isinstance(kind, str):
            raise _ParseError("sentence missing text/kind")
        if not text_value or len(text_value) > MAX_SENTENCE_CHARS:
            raise _ParseError("sentence text length out of bounds")
        if not isinstance(references, list) or not all(isinstance(r, str) for r in references):
            raise _ParseError("references must be a list of strings")
        sentences.append(
            ExplanationSentence(text=text_value, references=tuple(references), kind=kind)
        )
    return sentences


def _build_request(result_json: dict[str, Any], context: ExplanationContext) -> ModelRequest:
    return ModelRequest(
        prompt_fingerprint=PROMPT_FINGERPRINT,
        schema_fingerprint=SCHEMA_FINGERPRINT,
        scope=context.scope,
        payload={
            "result": result_json,
            "allowed_references": sorted(context.allowed_references),
            "allowed_numbers": sorted(context.allowed_numbers),
            "instructions": "Describe only supplied facts; cite a reference per sentence.",
        },
    )


def explain_case(
    result_json: dict[str, Any],
    context: ExplanationContext,
    *,
    models: Sequence[ExplanationModel],
    qualified_fingerprints: frozenset[str],
    max_attempts: int = 2,
    cost_budget: int = 1000,
) -> ExplanationOutcome:
    """Try each qualified model within a bounded attempt and cost budget.

    Invalid JSON or truncation is retried on the same model; a refusal or outage fails
    over to the next model without retrying the same payload; tool misuse stops
    immediately. Any parseable output is filtered to supported sentences. Producing at
    least one supported sentence returns PRODUCED; otherwise the outcome is
    NARRATIVE_WITHHELD and the deterministic report is unaffected."""
    request = _build_request(result_json, context)
    attempts = 0
    cost = 0
    failures: list[str] = []

    for model in models:
        if model.fingerprint not in qualified_fingerprints:
            failures.append("unqualified_denied")
            continue

        while attempts < max_attempts and cost < cost_budget:
            attempts += 1
            try:
                response = model.generate(request)
            except ModelError as error:
                failures.append(error.kind.value)
                if error.kind is ModelFailureKind.TOOL_MISUSE:
                    return _withheld("tool_misuse", attempts, cost, failures)
                if error.kind in (ModelFailureKind.REFUSAL, ModelFailureKind.PROVIDER_OUTAGE):
                    break  # do not retry the same model with the same payload
                continue  # invalid_json / truncation: a retry on the same model may help

            cost += response.cost_units
            try:
                sentences = _parse_sentences(response.text)
            except _ParseError:
                failures.append(ModelFailureKind.INVALID_JSON.value)
                continue

            supported = tuple(s for s in sentences if _sentence_supported(s, context))
            if supported:
                return ExplanationOutcome(
                    status=ExplanationStatus.PRODUCED,
                    sentences=supported,
                    attempts=attempts,
                    cost_units=cost,
                    model_fingerprint=model.fingerprint,
                    failures=tuple(failures),
                )
            # Parseable but nothing survived grounding; another try with the same
            # payload will not help, so move on to the next model.
            failures.append("unsupported")
            break

    reason = "no_supported_sentences" if failures and failures[-1] == "unsupported" else "exhausted"
    return _withheld(reason, attempts, cost, failures)


def _withheld(
    reason: str, attempts: int, cost: int, failures: list[str]
) -> ExplanationOutcome:
    return ExplanationOutcome(
        status=ExplanationStatus.NARRATIVE_WITHHELD,
        withheld_reason=reason,
        attempts=attempts,
        cost_units=cost,
        failures=tuple(failures),
    )
