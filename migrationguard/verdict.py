"""Deterministic migration verdict calculation."""

from collections.abc import Iterable
from typing import Literal

from migrationguard.rules import Finding

Verdict = Literal["SAFE", "REVIEW", "BLOCK"]


def determine_verdict(findings: Iterable[Finding]) -> Verdict:
    """Return BLOCK for high, REVIEW for medium, or SAFE otherwise."""
    severities = {finding.severity for finding in findings}
    if "high" in severities:
        return "BLOCK"
    if "medium" in severities:
        return "REVIEW"
    return "SAFE"
