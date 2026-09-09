from __future__ import annotations

from collections.abc import Sequence

from eastmed_shared.reconciliation.explanation import (
    ExplanationContext,
    ExplanationStatus,
    ModelError,
    ModelFailureKind,
    ModelRequest,
    ModelResponse,
    explain_case,
)

QUALIFIED = frozenset({"model-a", "model-b"})
CONTEXT = ExplanationContext(
    allowed_references=frozenset({"fact-1", "receipt-1"}),
    allowed_numbers=frozenset({"1.60", "6.96"}),
)
RESULT: dict[str, object] = {"variances": [{"status": "reconciled", "fuel_grade": "VLSFO"}]}

GOOD = (
    '[{"text": "VLSFO fuel is 1.60 tonnes above plan (6.96%).",'
    ' "references": ["fact-1", "receipt-1"], "kind": "calculated"}]'
)
INVENTED_NUMBER = (
    '[{"text": "VLSFO fuel is 7.00 tonnes above plan.",'
    ' "references": ["fact-1"], "kind": "calculated"}]'
)
BAD_REFERENCE = (
    '[{"text": "VLSFO fuel is 1.60 tonnes above plan.",'
    ' "references": ["fact-999"], "kind": "calculated"}]'
)
MIXED = (
    '[{"text": "VLSFO fuel is 1.60 tonnes above plan.", "references": ["fact-1"],'
    ' "kind": "calculated"},'
    ' {"text": "This proves waste of 7.00 tonnes.", "references": ["fact-1"],'
    ' "kind": "calculated"}]'
)

ScriptItem = str | ModelError | ModelResponse


class ScriptedModel:
    def __init__(self, fingerprint: str, script: Sequence[ScriptItem], cost: int = 1) -> None:
        self._fingerprint = fingerprint
        self._script = list(script)
        self._cost = cost
        self.calls = 0

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if not self._script:
            raise ModelError(ModelFailureKind.PROVIDER_OUTAGE, "script exhausted")
        item = self._script.pop(0)
        if isinstance(item, ModelError):
            raise item
        if isinstance(item, ModelResponse):
            return item
        return ModelResponse(text=item, model_fingerprint=self._fingerprint, cost_units=self._cost)


def run(models: Sequence[ScriptedModel], **kw: object) -> object:
    return explain_case(
        RESULT, CONTEXT, models=models, qualified_fingerprints=QUALIFIED, **kw  # type: ignore[arg-type]
    )


def test_grounded_sentence_is_produced() -> None:
    outcome = run([ScriptedModel("model-a", [GOOD])])
    assert outcome.status is ExplanationStatus.PRODUCED
    assert len(outcome.sentences) == 1
    assert outcome.sentences[0].references == ("fact-1", "receipt-1")
    assert outcome.model_fingerprint == "model-a"


def test_invented_number_is_dropped_and_withheld() -> None:
    outcome = run([ScriptedModel("model-a", [INVENTED_NUMBER])])
    assert outcome.status is ExplanationStatus.NARRATIVE_WITHHELD
    assert outcome.withheld_reason == "no_supported_sentences"


def test_unowned_reference_is_dropped_and_withheld() -> None:
    outcome = run([ScriptedModel("model-a", [BAD_REFERENCE])])
    assert outcome.status is ExplanationStatus.NARRATIVE_WITHHELD


def test_mixed_output_keeps_only_supported_sentence() -> None:
    outcome = run([ScriptedModel("model-a", [MIXED])])
    assert outcome.status is ExplanationStatus.PRODUCED
    assert len(outcome.sentences) == 1
    assert "waste" not in outcome.sentences[0].text


def test_invalid_json_is_retried_then_succeeds() -> None:
    model = ScriptedModel("model-a", [ModelError(ModelFailureKind.INVALID_JSON), GOOD])
    outcome = run([model], max_attempts=2)
    assert outcome.status is ExplanationStatus.PRODUCED
    assert outcome.attempts == 2
    assert ModelFailureKind.INVALID_JSON.value in outcome.failures


def test_malformed_json_text_is_treated_as_invalid_json() -> None:
    model = ScriptedModel("model-a", ["not json at all", GOOD])
    outcome = run([model], max_attempts=2)
    assert outcome.status is ExplanationStatus.PRODUCED
    assert ModelFailureKind.INVALID_JSON.value in outcome.failures


def test_refusal_fails_over_to_alternate() -> None:
    a = ScriptedModel("model-a", [ModelError(ModelFailureKind.REFUSAL)])
    b = ScriptedModel("model-b", [GOOD])
    outcome = run([a, b], max_attempts=2)
    assert outcome.status is ExplanationStatus.PRODUCED
    assert outcome.model_fingerprint == "model-b"
    assert a.calls == 1 and b.calls == 1


def test_provider_outage_fails_over_to_alternate() -> None:
    a = ScriptedModel("model-a", [ModelError(ModelFailureKind.PROVIDER_OUTAGE)])
    b = ScriptedModel("model-b", [GOOD])
    outcome = run([a, b], max_attempts=2)
    assert outcome.status is ExplanationStatus.PRODUCED
    assert outcome.model_fingerprint == "model-b"


def test_tool_misuse_stops_immediately() -> None:
    a = ScriptedModel("model-a", [ModelError(ModelFailureKind.TOOL_MISUSE)])
    b = ScriptedModel("model-b", [GOOD])
    outcome = run([a, b], max_attempts=3)
    assert outcome.status is ExplanationStatus.NARRATIVE_WITHHELD
    assert outcome.withheld_reason == "tool_misuse"
    assert b.calls == 0


def test_unqualified_model_is_denied_not_called() -> None:
    model = ScriptedModel("model-x", [GOOD])  # not in QUALIFIED
    outcome = run([model])
    assert outcome.status is ExplanationStatus.NARRATIVE_WITHHELD
    assert "unqualified_denied" in outcome.failures
    assert model.calls == 0


def test_cost_budget_stops_further_models() -> None:
    a = ScriptedModel("model-a", [INVENTED_NUMBER], cost=5)  # parseable but unsupported, cost 5
    b = ScriptedModel("model-b", [GOOD])
    outcome = run([a, b], max_attempts=5, cost_budget=3)
    assert outcome.status is ExplanationStatus.NARRATIVE_WITHHELD
    assert a.calls == 1
    assert b.calls == 0  # budget already exhausted before reaching the alternate


def test_oversized_sentence_list_is_rejected_as_invalid_json() -> None:
    one = '{"text": "1.60", "references": ["fact-1"], "kind": "calculated"}'
    oversized = "[" + ",".join([one] * 21) + "]"
    outcome = run([ScriptedModel("model-a", [oversized])], max_attempts=1)
    assert outcome.status is ExplanationStatus.NARRATIVE_WITHHELD
    assert ModelFailureKind.INVALID_JSON.value in outcome.failures


def test_truncation_then_exhausted_is_withheld() -> None:
    model = ScriptedModel(
        "model-a",
        [ModelError(ModelFailureKind.TRUNCATION), ModelError(ModelFailureKind.TRUNCATION)],
    )
    outcome = run([model], max_attempts=2)
    assert outcome.status is ExplanationStatus.NARRATIVE_WITHHELD
    assert outcome.failures.count(ModelFailureKind.TRUNCATION.value) == 2
