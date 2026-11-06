"""Command-line interface for MigrationGuard."""

from pathlib import Path

import typer

from migrationguard.explain import ExplainerResult, explain_findings
from migrationguard.rules import (
    Finding,
    MigrationParseError,
    load_rule_config,
    analyze_migration,
)
from migrationguard.verdict import Verdict, determine_verdict

app = typer.Typer(no_args_is_help=True, help="Review PostgreSQL migrations before they run.")


@app.callback()
def _root() -> None:
    """Review PostgreSQL migrations before they run."""


def _verdict_color(verdict: Verdict) -> str:
    return {"BLOCK": "red", "REVIEW": "yellow", "SAFE": "green"}[verdict]


def _print_report(
    path: Path,
    findings: list[Finding],
    verdict: Verdict,
    explanations: ExplainerResult | None,
) -> None:
    typer.secho(f"{path}: {verdict}", fg=_verdict_color(verdict), bold=True)
    if not findings:
        typer.echo("No configured migration risks found.")
    for position, finding in enumerate(findings):
        colors = {"high": "red", "medium": "yellow", "low": "cyan"}
        typer.secho(
            f"[{finding.severity.upper()}] line {finding.line}, "
            f"statement {finding.statement_index} ({finding.rule_id})",
            fg=colors[finding.severity],
            bold=True,
        )
        typer.echo(f"  {finding.message}")
        typer.echo(f"  SQL: {finding.sql_snippet}")
        explanation = explanations.explanations.get(position) if explanations else None
        if explanation:
            typer.echo(f"  Explanation: {explanation.explanation}")
            if explanation.safer_rewrite:
                typer.echo(f"  Safer rewrite:\n{explanation.safer_rewrite}")
    if explanations:
        for error in explanations.errors:
            typer.secho(f"LLM explanation unavailable: {error}", fg="yellow", err=True)


def _markdown_report(
    path: Path,
    findings: list[Finding],
    verdict: Verdict,
    explanations: ExplainerResult | None,
) -> str:
    lines = [f"## {path} - **{verdict}**", ""]
    if not findings:
        lines.extend(["No configured migration risks found.", ""])
    for position, finding in enumerate(findings):
        lines.extend(
            [
                f"- **{finding.severity.upper()}** `{finding.rule_id}` "
                f"(line {finding.line}, statement {finding.statement_index}): "
                f"{finding.message}",
                f"  ```sql\n  {finding.sql_snippet}\n  ```",
            ]
        )
        explanation = explanations.explanations.get(position) if explanations else None
        if explanation:
            lines.append(f"  **Explanation:** {explanation.explanation}")
            if explanation.safer_rewrite:
                lines.append(f"  **Safer rewrite:**\n  ```sql\n  {explanation.safer_rewrite}\n  ```")
    if explanations and explanations.errors:
        lines.extend(
            ["", "*Some LLM explanations were unavailable; deterministic findings are unchanged.*"]
        )
    return "\n".join(lines)


@app.command()
def check(
    path: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=True,
        readable=True,
        help="SQL migration file or directory of migration files.",
    ),
    config: Path | None = typer.Option(
        None, "--config", help="Path to a migrationguard.yaml configuration file."
    ),
    explain: bool = typer.Option(
        False, "--explain", help="Opt in to per-finding LLM explanations."
    ),
    report_file: Path | None = typer.Option(
        None, "--report-file", help="Write an aggregated Markdown report to this path."
    ),
) -> None:
    """Check one migration file or all SQL files in a directory."""
    try:
        rule_config = load_rule_config(config)
    except (OSError, ValueError) as error:
        typer.secho(f"Configuration error: {error}", fg="red", err=True)
        raise typer.Exit(code=2) from error

    files = (
        sorted(
            (
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file() and candidate.suffix.lower() == ".sql"
            ),
            key=lambda candidate: str(candidate).lower(),
        )
        if path.is_dir()
        else [path]
    )
    if not files:
        typer.secho(f"No SQL migration files found in {path}.", fg="red", err=True)
        raise typer.Exit(code=2)

    sections: list[str] = []
    blocked = False
    errors = False
    for migration_file in files:
        try:
            sql = migration_file.read_text(encoding="utf-8")
            findings = analyze_migration(sql, rule_config)
        except (OSError, UnicodeError, MigrationParseError) as error:
            errors = True
            message = f"Could not check {migration_file}: {error}"
            typer.secho(message, fg="red", err=True)
            sections.append(f"## {migration_file} - **ERROR**\n\n{message}")
            continue

        verdict = determine_verdict(findings)
        blocked = blocked or verdict == "BLOCK"
        explanations = explain_findings(findings) if explain else None
        _print_report(migration_file, findings, verdict, explanations)
        sections.append(_markdown_report(migration_file, findings, verdict, explanations))

    if report_file is not None:
        try:
            report_file.write_text("\n\n---\n\n".join(sections) + "\n", encoding="utf-8")
        except OSError as error:
            typer.secho(f"Could not write report to {report_file}: {error}", fg="red", err=True)
            raise typer.Exit(code=2) from error

    if errors:
        raise typer.Exit(code=2)
    if blocked:
        raise typer.Exit(code=1)


def main() -> None:
    """Run the Typer CLI."""
    app()
