"""Optional LangChain explanations for deterministic findings."""

from __future__ import annotations

import json
import logging
import os

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel
from dotenv import load_dotenv

from migrationguard.rules import Finding

logger = logging.getLogger(__name__)


class FindingExplanation(BaseModel):
    """Plain-English explanation and optional safer SQL rewrite."""

    explanation: str
    safer_rewrite: str


class ExplainerResult(BaseModel):
    """Explanations keyed by finding position, with visible call errors."""

    explanations: dict[int, FindingExplanation]
    errors: list[str]


def _create_model() -> BaseChatModel:
    """Create the configured OpenAI chat model from environment variables."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=os.getenv("MIGRATIONGUARD_MODEL", "gpt-4o-mini"),
        temperature=0,
    )


def explain_findings(
    findings: list[Finding], model: BaseChatModel | None = None
) -> ExplainerResult:
    """Explain findings individually without sending the full migration."""
    if not findings:
        return ExplainerResult(explanations={}, errors=[])

    active_model = model
    if active_model is None:
        load_dotenv()
        if not os.getenv("OPENAI_API_KEY"):
            message = "OPENAI_API_KEY is not configured; LLM explanations were skipped."
            logger.warning(message)
            return ExplainerResult(explanations={}, errors=[message])
        try:
            active_model = _create_model()
        except Exception as error:
            message = f"Could not initialize the LLM explainer: {error}"
            logger.warning(message)
            return ExplainerResult(explanations={}, errors=[message])

    try:
        structured_model = active_model.with_structured_output(FindingExplanation)
    except Exception as error:
        message = f"Could not configure the LLM explainer: {error}"
        logger.warning(message)
        return ExplainerResult(explanations={}, errors=[message])
    explanations: dict[int, FindingExplanation] = {}
    errors: list[str] = []
    system_message = SystemMessage(
        content=(
            "Explain this single deterministic SQL migration finding in plain English. "
            "Suggest a safer SQL rewrite that preserves intent when possible; use an empty "
            "rewrite when no safe rewrite can be determined. Do not identify or invent "
            "additional risks. Return only the requested structured fields."
        )
    )

    for position, finding in enumerate(findings):
        user_message = HumanMessage(
            content=json.dumps(
                {
                    "rule_id": finding.rule_id,
                    "sql_snippet": finding.sql_snippet,
                    "message": finding.message,
                }
            )
        )
        try:
            response = structured_model.invoke([system_message, user_message])
            explanations[position] = FindingExplanation.model_validate(response)
        except Exception as error:
            message = f"Could not explain finding {position + 1}: {error}"
            logger.warning(message)
            errors.append(message)

    return ExplainerResult(explanations=explanations, errors=errors)
