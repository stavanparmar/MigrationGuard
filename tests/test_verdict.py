"""Tests for deterministic verdict calculation."""

import pytest

from migrationguard.rules import Finding
from migrationguard.verdict import determine_verdict


@pytest.mark.parametrize(
    ("severities", "expected"),
    [
        ([], "SAFE"),
        (["low"], "SAFE"),
        (["medium"], "REVIEW"),
        (["high"], "BLOCK"),
        (["medium", "high"], "BLOCK"),
    ],
)
def test_verdict_depends_only_on_highest_severity(
    severities: list[str], expected: str
) -> None:
    findings = [
        Finding(
            rule_id="test_rule",
            severity=severity,
            statement_index=index,
            line=index,
            sql_snippet="SELECT 1;",
            message="Test finding.",
        )
        for index, severity in enumerate(severities, start=1)
    ]

    assert determine_verdict(findings) == expected
