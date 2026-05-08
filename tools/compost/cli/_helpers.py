from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from compost.ingest.classifier import ClassifyDecision

console = Console()


def _load_gitea_client(repo: Path):
    """Build GiteaClient from .compost.yml gitea config + GITEA_TOKEN env var."""
    from compost.gitea.client import GiteaClient
    from compost.repo import load_repo_config
    config = load_repo_config(repo)
    gitea_cfg = config.get("gitea")
    if not gitea_cfg:
        console.print("[red].compost.yml missing 'gitea:' config. Run: compost gitea setup[/red]")
        raise SystemExit(1)
    token = os.environ.get("GITEA_TOKEN", "")
    if not token:
        console.print("[red]GITEA_TOKEN env var is not set.[/red]")
        raise SystemExit(1)
    return GiteaClient(
        url=gitea_cfg["url"],
        owner=gitea_cfg["owner"],
        repo=gitea_cfg["repo"],
        token=token,
    )


def _run_doctor_checks(repo: Path) -> list[tuple[str, bool, str]]:
    from compost.repo import load_repo_config
    from compost.model.frontmatter import validate_frontmatter, parse_frontmatter

    config = load_repo_config(repo)
    index_name = config["qmd_index"]
    checks: list[tuple[str, bool, str]] = []

    checks.append((".compost.yml present", True, ""))

    result = subprocess.run(["qmd", "--version"], capture_output=True)
    checks.append(("qmd binary", result.returncode == 0,
                   "" if result.returncode == 0 else "qmd not found on PATH"))

    expected = {"wiki", "raw", "decisions", "incidents"}
    registered = _qmd_collections(index_name)
    missing = expected - registered
    checks.append(("qmd collections", not missing,
                   f"missing: {', '.join(sorted(missing))}" if missing else ""))

    codeowners = repo / ".github" / "CODEOWNERS"
    checks.append(("CODEOWNERS", codeowners.exists() and codeowners.stat().st_size > 0,
                   "missing or empty .github/CODEOWNERS"))

    gitignore = repo / ".gitignore"
    _REQUIRED_IGNORES = {
        ".compost/codify/", ".compost/synth-log/", ".compost/checks/",
        ".compost/queue/", ".compost/shims/", ".compost/lint-log/",
    }
    if gitignore.exists():
        ignored = set(gitignore.read_text().splitlines())
        missing_ignores = _REQUIRED_IGNORES - ignored
        checks.append((".gitignore", not missing_ignores,
                       f"missing entries: {', '.join(sorted(missing_ignores))}" if missing_ignores else ""))
    else:
        checks.append((".gitignore", False, "missing — run compost init or create manually"))

    wiki_dir = repo / "wiki"
    errors: list[str] = []
    for md in wiki_dir.rglob("*.md"):
        if md.name in ("glossary.md", "index.md", "log.md"):
            continue
        fm, _ = parse_frontmatter(md)
        errs = validate_frontmatter(fm, md.relative_to(repo))
        errors.extend(errs)
    checks.append(("wiki frontmatter", not errors,
                   "; ".join(errors[:3]) + ("..." if len(errors) > 3 else "")))

    gitea_cfg = config.get("gitea", {})
    gitea_ok = all(k in gitea_cfg for k in ("url", "owner", "repo"))
    checks.append(("gitea config", gitea_ok,
                   "missing gitea.url/owner/repo in .compost.yml" if not gitea_ok else ""))

    if gitea_ok:
        token = os.environ.get("GITEA_TOKEN", "")
        if not token:
            checks.append(("gitea connectivity", False, "GITEA_TOKEN not set"))
        else:
            from compost.gitea.client import GiteaClient, GiteaError
            try:
                client = GiteaClient(
                    url=gitea_cfg["url"], owner=gitea_cfg["owner"],
                    repo=gitea_cfg["repo"], token=token,
                )
                user = client.get_authenticated_user()
                checks.append(("gitea connectivity", True, f"authenticated as {user}"))
            except GiteaError as e:
                checks.append(("gitea connectivity", False, str(e)))

        from compost.ingest.git import get_remote_url
        remote_url = get_remote_url(repo, "origin")
        expected_url_prefix = gitea_cfg["url"].rstrip("/")
        remote_ok = remote_url is not None and expected_url_prefix in remote_url
        checks.append(("gitea remote", remote_ok,
                       f"origin URL '{remote_url}' doesn't match {expected_url_prefix}"
                       if not remote_ok else ""))

    return checks


def _qmd_collections(index_name: str) -> set[str]:
    result = subprocess.run(
        ["qmd", "--index", index_name, "collection", "list"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return set()
    names: set[str] = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if line and not line.startswith("Collections") and "(" in line:
            name = line.split("(")[0].strip()
            if name:
                names.add(name)
    return names


def _render_checks(checks: list[tuple[str, bool, str]]) -> None:
    table = Table(show_header=False, box=None, padding=(0, 1))
    for name, ok, msg in checks:
        icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
        detail = f"  [dim]{msg}[/dim]" if (msg and not ok) else ""
        table.add_row(icon, name + detail)
    console.print(table)


def _print_classify_verdict(rel: Path, decision: "ClassifyDecision") -> None:
    if decision.fired:
        trigger_str = " · ".join(f"{t.kind}:{t.pattern}" for t in decision.triggers)
        console.print(f"[green]✓ FIRE[/green]   {rel}")
        console.print(f"  {trigger_str}")
        console.print(f'  "{decision.rationale}"')
    else:
        console.print(f"[dim]  no-fire[/dim]   {rel}")
        console.print("  (no triggers matched)")


def _write_assay_report(repo: Path, result) -> None:
    from datetime import datetime, timezone
    lines = [
        f"# Assay Report — {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}",
        "",
        f"**Result:** {'PASS' if result.passed else 'FAIL'}  ",
        f"**Pages checked:** {result.checked}  ",
        f"**Failures:** {len(result.failures)}",
        "",
    ]
    for f in result.failures:
        lines.append(f"## {f.page_id}")
        lines.append(f"Source: `{f.source_md.relative_to(repo)}`")
        if f.missing_fields:
            lines.append(f"Missing: {', '.join(f.missing_fields)}")
        for field, orig, rend in f.changed_fields:
            lines.append(f"- `{field}`: `{orig}` → `{rend}`")
        lines.append("")
    report_path = repo / ".compost" / "assay-report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines))


def _render_check_results(results: list) -> None:
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Findings", justify="right")
    table.add_column("Cost (USD)", justify="right")
    table.add_column("Duration (s)", justify="right")

    for r in results:
        if r.status == "pass":
            status_str = "[green]✓ pass[/green]"
        elif r.status == "fail":
            status_str = "[red]✗ FAIL[/red]"
        elif r.status == "warn":
            status_str = "[yellow]⚠ warn[/yellow]"
        else:
            status_str = "[dim]— skipped[/dim]"

        n = str(len(r.findings)) if r.status != "skipped" else "—"
        cost = f"{r.cost_usd:.4f}" if r.status != "skipped" else "—"
        dur = f"{r.duration_s:.1f}" if r.status != "skipped" else "—"
        table.add_row(r.name, status_str, n, cost, dur)

    console.print(table)

    all_findings = [f for r in results for f in r.findings]
    if all_findings:
        console.print(f"\n[red]✗ {len(all_findings)} finding(s):[/red]")
        for f in all_findings:
            console.print(f"  [bold]{f.check}[/bold]: {f.page or '(general)'}")
            console.print(f"    {f.message}")
            if f.claim_text:
                console.print(f'    claim: "{f.claim_text}"')
            if f.conflicting_page:
                console.print(f"    conflicts with {f.conflicting_page}")
                if f.conflicting_claim_text:
                    console.print(f'    "{f.conflicting_claim_text}"')


def _log_override(repo: Path, branch: str, reason: str, failed_checks: list[str]) -> None:
    """Append an override entry to wiki/log.md and commit it on main."""
    from datetime import datetime, timezone
    from compost.ingest.git import stage_and_commit

    ts = datetime.now(timezone.utc)
    log_path = repo / "wiki" / "log.md"
    entry = (
        f"\n## Override: {branch} ({ts:%Y-%m-%d %H:%M UTC})\n"
        f"Reason: {reason}\n"
        f"Findings bypassed: {', '.join(failed_checks) or 'none'}\n"
    )
    if log_path.exists():
        log_path.write_text(log_path.read_text() + entry)
    else:
        log_path.write_text(f"# Override Log\n{entry}")

    try:
        stage_and_commit(repo, [log_path], f"override: {branch} — {reason[:60]}")
        console.print("[dim]override logged → wiki/log.md[/dim]")
    except Exception as e:
        console.print(f"[yellow]⚠ could not commit override log: {e}[/yellow]")
