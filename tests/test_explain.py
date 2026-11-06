"""Tests for optional structured LLM explanations."""

import json
from unittest.mock import MagicMock

from langchain_core.messages import HumanMessage
from pytest import LogCaptureFixture

from migrationguard.explain import FindingExplanation, explain_findings
from migrationguard.rules import Finding


def _finding() -> Finding:
    return Finding(
        rule_id="drop_table_column",
        severity="high",
        statement_index=1,
        line=1,
        sql_snippet="DROP TABLE users;",
        message="Dropping a table permanently removes data.",
    )


def test_explainer_sends_only_one_finding_and_returns_structured_output() -> None:
    finding = _finding()
    model = MagicMock()
    structured_model = MagicMock()
    model.with_structured_output.return_value = structured_model
    structured_model.invoke.return_value = FindingExplanation(
        explanation="This removes the table.",
        safer_rewrite="ALTER TABLE users RENAME TO users_old;",
    )

    result = explain_findings([finding], model=model)

    assert result.explanations[0].explanation == "This removes the table."
    assert result.explanations[0].safer_rewrite.startswith("ALTER TABLE")
    assert result.errors == []
    messages = structured_model.invoke.call_args.args[0]
    assert isinstance(messages[1], HumanMessage)
    assert json.loads(messages[1].content) == {
        "rule_id": finding.rule_id,
        "sql_snippet": finding.sql_snippet,
        "message": finding.message,
    }


def test_explainer_failure_keeps_findings_without_explanations(
    caplog: LogCaptureFixture,
) -> None:
    finding = _finding()
    model = MagicMock()
    structured_model = MagicMock()
    model.with_structured_output.return_value = structured_model
    structured_model.invoke.side_effect = RuntimeError("model unavailable")

    result = explain_findings([finding], model=model)

    assert result.explanations == {}
    assert len(result.errors) == 1
    assert "model unavailable" in result.errors[0]
    assert "Could not explain finding" in caplog.text
    assert finding.rule_id == "drop_table_column"
