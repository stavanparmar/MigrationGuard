"""Tests for the public command-line interface."""

from pathlib import Path

from typer.testing import CliRunner

from migrationguard.cli import app

runner = CliRunner()


def test_block_exit_code_and_markdown_report(tmp_path: Path) -> None:
    migration = tmp_path / "risky.sql"
    report = tmp_path / "report.md"
    migration.write_text("DROP TABLE users;", encoding="utf-8")

    result = runner.invoke(
        app,
        ["check", str(migration), "--report-file", str(report)],
    )

    assert result.exit_code == 1
    assert "BLOCK" in result.output
    assert "drop_table_column" in report.read_text(encoding="utf-8")


def test_safe_migration_returns_zero(tmp_path: Path) -> None:
    migration = tmp_path / "safe.sql"
    migration.write_text(
        "CREATE INDEX CONCURRENTLY users_email_idx ON users (email);",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["check", str(migration)])

    assert result.exit_code == 0
    assert "SAFE" in result.output
