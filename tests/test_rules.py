"""Tests for deterministic PostgreSQL migration rules."""

from pathlib import Path

import pytest

from migrationguard.rules import (
    MigrationParseError,
    analyze_migration,
    load_rule_config,
)


@pytest.mark.parametrize(
    ("sql", "finding_count"),
    [
        ("DROP TABLE users;", 1),
        ("ALTER TABLE users DROP COLUMN email;", 1),
        ("CREATE TABLE users (id integer);", 0),
        ("DROP INDEX users_email_idx;", 0),
    ],
)
def test_drop_table_or_column(sql: str, finding_count: int) -> None:
    findings = analyze_migration(sql)
    assert sum(f.rule_id == "drop_table_column" for f in findings) == finding_count


@pytest.mark.parametrize(
    ("sql", "expected"),
    [("TRUNCATE TABLE users;", 1), ("DELETE FROM users WHERE id = 1;", 0)],
)
def test_truncate_table(sql: str, expected: int) -> None:
    findings = analyze_migration(sql)
    assert sum(f.rule_id == "truncate_table" for f in findings) == expected


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("ALTER TABLE users ADD COLUMN active boolean NOT NULL;", 1),
        (
            "ALTER TABLE users ADD COLUMN active boolean NOT NULL DEFAULT true;",
            0,
        ),
        ("ALTER TABLE users ADD COLUMN active boolean;", 0),
    ],
)
def test_add_not_null_without_default(sql: str, expected: int) -> None:
    findings = analyze_migration(sql)
    assert sum(f.rule_id == "add_not_null_without_default" for f in findings) == expected


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("CREATE INDEX users_email_idx ON users (email);", 1),
        ("CREATE INDEX CONCURRENTLY users_email_idx ON users (email);", 0),
    ],
)
def test_create_index_without_concurrently(sql: str, expected: int) -> None:
    findings = analyze_migration(sql)
    assert (
        sum(f.rule_id == "create_index_without_concurrently" for f in findings)
        == expected
    )


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("ALTER TABLE users ALTER COLUMN age TYPE bigint;", 1),
        ("ALTER TABLE users ALTER COLUMN age SET DEFAULT 0;", 0),
    ],
)
def test_alter_column_type(sql: str, expected: int) -> None:
    findings = analyze_migration(sql)
    assert sum(f.rule_id == "alter_column_type" for f in findings) == expected


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("ALTER TABLE users RENAME COLUMN name TO display_name;", 1),
        ("ALTER TABLE users RENAME TO people;", 1),
        ("ALTER TABLE users ADD COLUMN display_name text;", 0),
    ],
)
def test_rename_column_or_table(sql: str, expected: int) -> None:
    findings = analyze_migration(sql)
    assert sum(f.rule_id == "rename_column_or_table" for f in findings) == expected


@pytest.mark.parametrize(
    ("constraint", "expected"),
    [
        ("FOREIGN KEY (team_id) REFERENCES teams(id)", 1),
        ("UNIQUE (email)", 1),
        ("CHECK (age >= 0)", 1),
        ("FOREIGN KEY (team_id) REFERENCES teams(id) NOT VALID", 0),
    ],
)
def test_add_constraint_without_not_valid(constraint: str, expected: int) -> None:
    sql = f"ALTER TABLE users ADD CONSTRAINT users_team_check {constraint};"
    findings = analyze_migration(sql)
    assert (
        sum(f.rule_id == "add_constraint_without_not_valid" for f in findings)
        == expected
    )


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("UPDATE users SET active = false;", 1),
        ("UPDATE users SET active = false WHERE id = 1;", 0),
        ("DELETE FROM users;", 1),
        ("DELETE FROM users WHERE id = 1;", 0),
    ],
)
def test_update_delete_without_where(sql: str, expected: int) -> None:
    findings = analyze_migration(sql)
    assert sum(f.rule_id == "update_delete_without_where" for f in findings) == expected


def test_rule_config_disables_and_overrides_severity(tmp_path: Path) -> None:
    config_path = tmp_path / "migrationguard.yaml"
    config_path.write_text(
        "rules:\n"
        "  drop_table_column:\n"
        "    enabled: false\n"
        "  truncate_table:\n"
        "    severity: low\n",
        encoding="utf-8",
    )

    config = load_rule_config(config_path)
    findings = analyze_migration("DROP TABLE users; TRUNCATE TABLE users;", config)

    assert len(findings) == 1
    assert findings[0].rule_id == "truncate_table"
    assert findings[0].severity == "low"


def test_multiple_statements_preserve_indices_and_lines() -> None:
    sql = "DROP TABLE users;\n\nUPDATE users SET active = false;"

    findings = analyze_migration(sql)

    assert [(f.rule_id, f.statement_index, f.line) for f in findings] == [
        ("drop_table_column", 1, 1),
        ("update_delete_without_where", 2, 3),
    ]


def test_invalid_sql_raises_clear_error() -> None:
    with pytest.raises(MigrationParseError, match="Invalid PostgreSQL SQL"):
        analyze_migration("ALTER TABLE users ADD COLUMN (")
