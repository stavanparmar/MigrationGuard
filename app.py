"""Streamlit interface for reviewing PostgreSQL migrations."""

from html import escape
import streamlit as st
from dotenv import load_dotenv

from migrationguard.explain import ExplainerResult, explain_findings
from migrationguard.rules import (
    Finding,
    MigrationParseError,
    analyze_migration,
    load_rule_config,
)
from migrationguard.verdict import Verdict, determine_verdict

load_dotenv()
st.set_page_config(page_title="MigrationGuard", page_icon="🛡️", layout="wide")


def _apply_theme(dark: bool) -> None:
    """Apply matching colors to the app shell and SQL review cards."""
    colors = (
        {
            "background": "#0b1220",
            "surface": "#111c2f",
            "surface_alt": "#17243a",
            "text": "#e6edf7",
            "muted": "#9eacc2",
            "border": "#2b3a52",
        }
        if dark
        else {
            "background": "#f4f7fb",
            "surface": "#ffffff",
            "surface_alt": "#edf2f8",
            "text": "#172338",
            "muted": "#5e6d82",
            "border": "#d7e0eb",
        }
    )
    st.markdown(
        f"""
        <style>
        [data-testid="stAppViewContainer"] {{
            background: {colors["background"]};
            color: {colors["text"]};
        }}
        [data-testid="stHeader"], [data-testid="stSidebar"] {{
            background: {colors["surface"]};
        }}
        [data-testid="stAppViewContainer"] .block-container {{
            max-width: 1120px;
            padding-top: 2rem;
            padding-bottom: 4rem;
        }}
        [data-testid="stSidebar"] * {{
            color: {colors["text"]};
        }}
        div[data-baseweb="textarea"] textarea {{
            background: {colors["surface"]};
            color: {colors["text"]};
            border-color: {colors["border"]};
            border-radius: 12px;
        }}
        [data-testid="stCodeBlock"], [data-testid="stCode"] {{
            background: {colors["surface_alt"]} !important;
            color: {colors["text"]} !important;
            border-radius: 12px;
        }}
        [data-testid="stCodeBlock"] pre, [data-testid="stCode"] pre {{
            background: {colors["surface_alt"]} !important;
            color: {colors["text"]} !important;
        }}
        .mg-hero {{
            background: linear-gradient(135deg, {colors["surface"]}, {colors["surface_alt"]});
            border: 1px solid {colors["border"]};
            border-radius: 20px;
            padding: 1.5rem 1.75rem;
            margin-bottom: 1.5rem;
        }}
        .mg-hero h1 {{ margin: 0; color: {colors["text"]}; font-size: 2rem; }}
        .mg-hero p {{ color: {colors["muted"]}; margin: .55rem 0 0; }}
        .mg-verdict {{
            display: inline-block;
            border-radius: 999px;
            padding: .45rem .9rem;
            font-weight: 800;
            letter-spacing: .06em;
        }}
        .mg-card {{
            background: {colors["surface"]};
            border: 1px solid {colors["border"]};
            border-radius: 14px;
            padding: 1rem 1.2rem;
            margin: .6rem 0;
        }}
        .mg-table-wrap {{
            overflow-x: auto;
            border: 1px solid {colors["border"]};
            border-radius: 14px;
            background: {colors["surface"]};
        }}
        .mg-table {{ width: 100%; border-collapse: collapse; color: {colors["text"]}; }}
        .mg-table th, .mg-table td {{
            padding: .8rem 1rem;
            text-align: left;
            border-bottom: 1px solid {colors["border"]};
        }}
        .mg-table th {{ color: {colors["muted"]}; font-size: .82rem; }}
        .mg-badge {{ border-radius: 999px; padding: .2rem .55rem; font-weight: 700; }}
        .mg-muted {{ color: {colors["muted"]}; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _verdict_colors(verdict: Verdict) -> tuple[str, str]:
    return {
        "BLOCK": ("#7f1d1d", "#fecaca"),
        "REVIEW": ("#78350f", "#fde68a"),
        "SAFE": ("#14532d", "#bbf7d0"),
    }[verdict]


def _show_findings(findings: list[Finding], explanations: ExplainerResult) -> None:
    verdict = determine_verdict(findings)
    background, foreground = _verdict_colors(verdict)
    st.markdown(
        f'<div class="mg-verdict" style="background:{background};color:{foreground}">'
        f"{verdict}</div>",
        unsafe_allow_html=True,
    )
    if not findings:
        st.success("No configured migration risks found.")
        return

    st.subheader("Findings")
    rows = []
    for finding in findings:
        severity_color = {
            "high": ("#7f1d1d", "#fecaca"),
            "medium": ("#78350f", "#fde68a"),
            "low": ("#164e63", "#a5f3fc"),
        }[finding.severity]
        rows.append(
            "<tr>"
            f'<td><span class="mg-badge" style="background:{severity_color[0]};'
            f'color:{severity_color[1]}">{escape(finding.severity.upper())}</span></td>'
            f"<td>{escape(finding.rule_id)}</td>"
            f"<td>{finding.line}</td><td>{finding.statement_index}</td>"
            f"<td>{escape(finding.message)}</td></tr>"
        )
    st.markdown(
        '<div class="mg-table-wrap"><table class="mg-table"><thead><tr>'
        "<th>Severity</th><th>Rule</th><th>Line</th><th>Statement</th>"
        f"<th>Finding</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>",
        unsafe_allow_html=True,
    )

    for position, finding in enumerate(findings):
        severity_color = {
            "high": "#ef4444",
            "medium": "#f59e0b",
            "low": "#06b6d4",
        }[finding.severity]
        with st.expander(
            f"{finding.severity.upper()} | {finding.rule_id} | "
            f"line {finding.line}, statement {finding.statement_index}"
        ):
            st.markdown(
                f'<div class="mg-card"><strong style="color:{severity_color}">'
                f"{finding.severity.upper()}</strong><p>{escape(finding.message)}</p></div>",
                unsafe_allow_html=True,
            )
            st.code(finding.sql_snippet, language="sql")
            explanation = explanations.explanations.get(position)
            if explanation:
                st.markdown("**Explanation**")
                st.write(explanation.explanation)
                if explanation.safer_rewrite:
                    st.markdown("**Safer rewrite**")
                    st.code(explanation.safer_rewrite, language="sql")
            else:
                st.caption("Enable optional LLM explanations to get a rewrite suggestion.")

    for error in explanations.errors:
        st.warning(error)


st.sidebar.title("MigrationGuard")
dark_theme = st.sidebar.toggle("Dark theme", value=True)
_apply_theme(dark_theme)
st.markdown(
    '<div class="mg-hero"><h1>Review migrations before they run.</h1>'
    "<p>Deterministic SQL rules find risks. Optional AI only explains those findings.</p></div>",
    unsafe_allow_html=True,
)

uploaded_file = st.file_uploader("Upload a PostgreSQL migration", type=["sql"])
migration_text = st.text_area(
    "Or paste migration SQL",
    height=260,
    placeholder="ALTER TABLE users ADD COLUMN status text NOT NULL;",
)
explain = st.checkbox("Generate optional LLM explanations", value=False)

if st.button("Review migration", type="primary", use_container_width=True):
    try:
        sql_text = uploaded_file.getvalue().decode("utf-8") if uploaded_file else migration_text
        if not sql_text.strip():
            st.warning("Paste or upload a non-empty SQL migration to review.")
            st.stop()
        findings = analyze_migration(sql_text, load_rule_config())
        explanations = explain_findings(findings) if explain else ExplainerResult(
            explanations={}, errors=[]
        )
        st.session_state["review_result"] = (findings, explanations)
    except UnicodeError:
        st.error("The uploaded migration is not valid UTF-8 text.")
    except MigrationParseError as error:
        st.error(str(error))
    except (OSError, ValueError) as error:
        st.error(f"Could not load migration rule configuration: {error}")

result = st.session_state.get("review_result")
if result:
    st.divider()
    _show_findings(*result)
