"""Deterministic PostgreSQL migration risk rules."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

import sqlglot
import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.tokens import TokenType

Severity = Literal["high", "medium", "low"]

DEFAULT_SEVERITIES: dict[str, Severity] = {
    "drop_table_column": "high",
    "truncate_table": "high",
    "add_not_null_without_default": "high",
    "create_index_without_concurrently": "medium",
    "alter_column_type": "medium",
    "rename_column_or_table": "medium",
    "add_constraint_without_not_valid": "medium",
    "update_delete_without_where": "high",
}


class Finding(BaseModel):
    """A deterministic risk detected in one migration statement."""

    rule_id: str
    severity: Severity
    statement_index: int
    line: int
    sql_snippet: str
    message: str


class RuleSetting(BaseModel):
    """Enablement and severity override for one rule."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    severity: Severity


class RuleConfig(BaseModel):
    """Validated settings for the migration rule set."""

    model_config = ConfigDict(extra="forbid")

    rules: dict[str, RuleSetting] = Field(
        default_factory=lambda: {
            rule_id: RuleSetting(severity=severity)
            for rule_id, severity in DEFAULT_SEVERITIES.items()
        }
    )


class MigrationParseError(ValueError):
    """Raised when a migration cannot be parsed as PostgreSQL SQL."""


def load_rule_config(path: Path | None = None) -> RuleConfig:
    """Load YAML rule overrides, using built-in defaults for omitted rules."""
    config_path = path or Path(__file__).resolve().parent.parent / "migrationguard.yaml"
    if not config_path.exists():
        if path is not None:
            raise FileNotFoundError(f"Rule configuration not found: {config_path}")
        return RuleConfig()

    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid rule configuration in {config_path}: {error}") from error

    if not isinstance(data, dict) or set(data) - {"rules"}:
        raise ValueError("Rule configuration must contain only a 'rules' mapping.")
    overrides = data.get("rules", {})
    if not isinstance(overrides, dict):
        raise ValueError("'rules' must be a mapping of rule IDs to settings.")

    unknown_rules = set(overrides) - DEFAULT_SEVERITIES.keys()
    if unknown_rules:
        raise ValueError(f"Unknown rule ID(s): {', '.join(sorted(unknown_rules))}")

    settings = {
        rule_id: RuleSetting(severity=severity)
        for rule_id, severity in DEFAULT_SEVERITIES.items()
    }
    for rule_id, override in overrides.items():
        if not isinstance(override, dict):
            raise ValueError(f"Settings for '{rule_id}' must be a mapping.")
        unknown_settings = set(override) - {"enabled", "severity"}
        if unknown_settings:
            raise ValueError(
                f"Unknown setting(s) for '{rule_id}': {', '.join(sorted(unknown_settings))}"
            )
        settings[rule_id] = RuleSetting(
            enabled=override.get("enabled", True),
            severity=override.get("severity", DEFAULT_SEVERITIES[rule_id]),
        )
    return RuleConfig(rules=settings)


def _kind_name(node: exp.Expression) -> str:
    kind = node.args.get("kind")
    if isinstance(kind, exp.Expression):
        kind = kind.args.get("this")
    return str(kind or "").upper()


def _statement_lines(sql: str) -> list[int]:
    """Return each statement's source line using PostgreSQL-aware tokens."""
    tokens = sqlglot.Dialect.get_or_raise("postgres").tokenizer().tokenize(sql)
    lines: list[int] = []
    current_line: int | None = None
    for token in tokens:
        if token.token_type == TokenType.SEMICOLON:
            if current_line is not None:
                lines.append(current_line)
                current_line = None
        elif current_line is None:
            current_line = token.line
    if current_line is not None:
        lines.append(current_line)
    return lines


def _make_finding(
    rule_id: str,
    statement: exp.Expression,
    statement_index: int,
    line: int,
    config: RuleConfig,
    message: str,
) -> Finding:
    setting = config.rules.get(rule_id, RuleSetting(severity=DEFAULT_SEVERITIES[rule_id]))
    return Finding(
        rule_id=rule_id,
        severity=setting.severity,
        statement_index=statement_index,
        line=line,
        sql_snippet=statement.sql(dialect="postgres"),
        message=message,
    )


def _find_drop_table_column(
    statement: exp.Expression, index: int, line: int, config: RuleConfig
) -> list[Finding]:
    drops: list[exp.Expression] = []
    if isinstance(statement, exp.Drop) and _kind_name(statement) == "TABLE":
        drops.append(statement)
    if isinstance(statement, exp.Alter):
        drops.extend(
            node
            for node in statement.find_all(exp.Drop)
            if _kind_name(node) == "COLUMN"
        )
    return [
        _make_finding(
            "drop_table_column",
            statement,
            index,
            line,
            config,
            "Dropping a table or column permanently removes data.",
        )
        for _ in drops
    ]


def _find_truncate_table(
    statement: exp.Expression, index: int, line: int, config: RuleConfig
) -> list[Finding]:
    if not isinstance(statement, exp.TruncateTable):
        return []
    return [
        _make_finding(
            "truncate_table",
            statement,
            index,
            line,
            config,
            "TRUNCATE removes all rows from the target table.",
        )
    ]


def _find_add_not_null_without_default(
    statement: exp.Expression, index: int, line: int, config: RuleConfig
) -> list[Finding]:
    if not isinstance(statement, exp.Alter):
        return []
    findings: list[Finding] = []
    for column in statement.find_all(exp.ColumnDef):
        constraints = list(column.find_all(exp.ColumnConstraint))
        has_not_null = any(
            isinstance(constraint.args.get("kind"), exp.NotNullColumnConstraint)
            for constraint in constraints
        )
        has_default = any(
            isinstance(constraint.args.get("kind"), exp.DefaultColumnConstraint)
            for constraint in constraints
        ) or column.args.get("default") is not None
        if has_not_null and not has_default:
            findings.append(
                _make_finding(
                    "add_not_null_without_default",
                    statement,
                    index,
                    line,
                    config,
                    "Adding a NOT NULL column without a default can fail when the table has existing rows.",
                )
            )
    return findings


def _find_create_index_without_concurrently(
    statement: exp.Expression, index: int, line: int, config: RuleConfig
) -> list[Finding]:
    if not isinstance(statement, exp.Create) or _kind_name(statement) != "INDEX":
        return []
    if statement.args.get("concurrently"):
        return []
    return [
        _make_finding(
            "create_index_without_concurrently",
            statement,
            index,
            line,
            config,
            "Creating an index without CONCURRENTLY can block writes to the table.",
        )
    ]


def _find_alter_column_type(
    statement: exp.Expression, index: int, line: int, config: RuleConfig
) -> list[Finding]:
    if not isinstance(statement, exp.Alter):
        return []
    actions = [
        action
        for action in statement.walk()
        if isinstance(action, exp.AlterColumn) and action.args.get("dtype") is not None
    ]
    return [
        _make_finding(
            "alter_column_type",
            statement,
            index,
            line,
            config,
            "Changing a column type may rewrite the whole table and hold locks.",
        )
        for _ in actions
    ]


def _find_rename_column_or_table(
    statement: exp.Expression, index: int, line: int, config: RuleConfig
) -> list[Finding]:
    if not isinstance(statement, exp.Alter):
        return []
    actions = [
        action
        for action in statement.walk()
        if "rename" in type(action).__name__.lower()
        and (
            "RENAME COLUMN" in action.sql(dialect="postgres").upper()
            or "RENAME TO" in action.sql(dialect="postgres").upper()
        )
    ]
    return [
        _make_finding(
            "rename_column_or_table",
            statement,
            index,
            line,
            config,
            "Renaming a column or table can break application code that still uses the old name.",
        )
        for _ in actions
    ]


def _find_add_constraint_without_not_valid(
    statement: exp.Expression, index: int, line: int, config: RuleConfig
) -> list[Finding]:
    if not isinstance(statement, exp.Alter):
        return []
    findings: list[Finding] = []
    for action in statement.walk():
        if type(action).__name__ != "AddConstraint":
            continue
        rendered = action.sql(dialect="postgres").upper()
        if not any(kind in rendered for kind in ("FOREIGN KEY", "UNIQUE", "CHECK")):
            continue
        if not statement.args.get("not_valid"):
            findings.append(
                _make_finding(
                    "add_constraint_without_not_valid",
                    statement,
                    index,
                    line,
                    config,
                    "Adding this constraint without NOT VALID checks existing rows under a table lock.",
                )
            )
    return findings


def _find_update_delete_without_where(
    statement: exp.Expression, index: int, line: int, config: RuleConfig
) -> list[Finding]:
    if not isinstance(statement, (exp.Update, exp.Delete)):
        return []
    if statement.args.get("where") is not None:
        return []
    return [
        _make_finding(
            "update_delete_without_where",
            statement,
            index,
            line,
            config,
            f"{type(statement).__name__.upper()} without WHERE affects every row in the table.",
        )
    ]


_RULES: tuple[
    tuple[str, Callable[[exp.Expression, int, int, RuleConfig], list[Finding]]], ...
] = (
    ("drop_table_column", _find_drop_table_column),
    ("truncate_table", _find_truncate_table),
    ("add_not_null_without_default", _find_add_not_null_without_default),
    ("create_index_without_concurrently", _find_create_index_without_concurrently),
    ("alter_column_type", _find_alter_column_type),
    ("rename_column_or_table", _find_rename_column_or_table),
    ("add_constraint_without_not_valid", _find_add_constraint_without_not_valid),
    ("update_delete_without_where", _find_update_delete_without_where),
)


def analyze_migration(
    sql: str, config: RuleConfig | None = None
) -> list[Finding]:
    """Parse PostgreSQL SQL and return enabled deterministic rule findings."""
    active_config = config or load_rule_config()
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except ParseError as error:
        raise MigrationParseError(f"Invalid PostgreSQL SQL: {error}") from error

    meaningful_statements = [
        statement
        for statement in statements
        if statement is not None and not isinstance(statement, exp.Semicolon)
    ]
    for statement_index, statement in enumerate(meaningful_statements, start=1):
        if isinstance(statement, exp.Command):
            raise MigrationParseError(
                f"Invalid or unsupported PostgreSQL SQL at statement {statement_index}: "
                "the parser could not build a structured syntax tree."
            )

    lines = _statement_lines(sql)
    if len(lines) != len(meaningful_statements):
        raise MigrationParseError(
            "Could not map parsed PostgreSQL statements to their source lines."
        )

    findings: list[Finding] = []
    for statement_index, (statement, line) in enumerate(
        zip(meaningful_statements, lines), start=1
    ):
        for rule_id, rule in _RULES:
            setting = active_config.rules.get(
                rule_id, RuleSetting(severity=DEFAULT_SEVERITIES[rule_id])
            )
            if setting.enabled:
                findings.extend(rule(statement, statement_index, line, active_config))
    return findings
