import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click
import yaml
from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from compost.ingest.classifier import ClassifyDecision

from compost.model.frontmatter import validate_frontmatter, parse_frontmatter
from compost.repo import find_repo_root, load_repo_config
from compost.session import list_sessions, find_session, format_session

console = Console()


@click.group()
@click.option("--repo", envvar="COMPOST_REPO", default=None,
              help="Path to wiki repo. Falls back to COMPOST_REPO env var then cwd walk.")
@click.pass_context
def main(ctx: click.Context, repo: str | None) -> None:
    ctx.ensure_object(dict)
    ctx.obj["repo"] = Path(repo).resolve() if repo else None


@main.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Verify repo health: qmd collections, frontmatter, CODEOWNERS, Gitea."""
    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found in cwd or any parent.[/red]")
        sys.exit(1)

    checks = _run_doctor_checks(repo)
    _render_checks(checks)
    if any(not ok for _, ok, _ in checks):
        sys.exit(1)


@main.command("mcp")
@click.pass_context
def mcp_serve(ctx: click.Context) -> None:
    """Start the MCP server (stdio transport)."""
    import asyncio
    from compost.mcp.server import run_server

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found. Run compost init first.[/red]")
        sys.exit(1)
    asyncio.run(run_server(repo))


@main.command()
@click.argument("path", default=".", type=click.Path())
@click.option("--name", required=True, help="Team name (used as qmd index name).")
def init(path: str, name: str) -> None:
    """Bootstrap a new compost wiki repo at PATH."""
    from compost.bootstrap import bootstrap_repo
    bootstrap_repo(Path(path).resolve(), name)


@main.group("session")
def session_group() -> None:
    """Inspect Claude Code session transcripts."""


@session_group.command("list")
@click.pass_context
def session_list(ctx: click.Context) -> None:
    """List sessions for the current repo."""
    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    sessions = list_sessions(repo)
    if not sessions:
        console.print("[dim]No sessions found.[/dim]")
        return

    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("ID", style="cyan")
    table.add_column("Timestamp")
    for s in sessions:
        ts = (s["ts"] or "")[:19].replace("T", " ")
        table.add_row(s["id"], ts)
    console.print(table)


@session_group.command("show")
@click.argument("session_id")
@click.option("--tools", "tools_only", is_flag=True, help="Show tool calls and results only.")
@click.option("--mcp", "mcp_only", is_flag=True, help="Show MCP tool calls and results only.")
@click.option("--snippet", default=400, show_default=True,
              help="Max chars shown per tool result.")
def session_show(session_id: str, tools_only: bool, mcp_only: bool, snippet: int) -> None:
    """Show a session transcript by ID (prefix match supported)."""
    path = find_session(session_id)
    if path is None:
        console.print(f"[red]Session not found: {session_id}[/red]")
        sys.exit(1)

    console.print(f"[dim]{path}[/dim]\n")
    output = format_session(path, tools_only=tools_only, mcp_only=mcp_only,
                            snippet_len=snippet)
    console.print(output)


# ── raw commands ─────────────────────────────────────────────────────────────

SOURCE_CHOICES = ["slack", "incident", "decision", "note", "meeting", "support"]


@main.group("raw")
def raw_group() -> None:
    """Manage raw source ingestion."""


@raw_group.command("add")
@click.option("--source", required=True, type=click.Choice(SOURCE_CHOICES),
              help="Source type.")
@click.option("--title", required=True, help="Human-readable title (slug + frontmatter).")
@click.option("--captured-by", default=None,
              help="Author. Defaults to git config user.name.")
@click.option("--origin", default="",
              help="Source reference (e.g. PagerDuty ID, Slack URL).")
@click.option("--channel", default="",
              help="Slack channel name. Required when --source=slack.")
@click.option("--no-synth", is_flag=True, default=False,
              help="Skip Tier 2 synthesis even if Tier 1 fires.")
@click.pass_context
def raw_add(ctx: click.Context, source: str, title: str, captured_by: str | None,
            origin: str, channel: str, no_synth: bool) -> None:
    """Add a raw source file from stdin, commit on a new branch, and open a Gitea PR."""
    from datetime import datetime, timezone

    from compost.ingest.git import assert_git_repo, create_branch_and_commit, get_git_user_name, push_branch
    from compost.ingest.pr import create_pr
    from typing import cast
    from compost.ingest.raw import slugify, write_raw, SOURCE_TYPES
    from compost.gitea.client import GiteaError

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    assert_git_repo(repo)

    body = click.get_text_stream("stdin").read()

    if captured_by is None:
        captured_by = get_git_user_name(repo)

    try:
        raw_file = write_raw(
            repo, cast(SOURCE_TYPES, source), title, body,
            captured_by=captured_by,
            origin=origin,
            channel=channel,
        )
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    ts = datetime.now(timezone.utc)
    branch = f"raw/{ts:%Y-%m-%d}-{slugify(title)}"

    create_branch_and_commit(
        repo, branch, [raw_file.path],
        message=f"raw: {title}",
    )

    console.print(f"[green]✓[/green] {raw_file.rel_path}")
    console.print(f"branch: {branch}")

    decision = None
    try:
        from compost.ingest.classifier import classify, log_decision
        decision = classify(raw_file.path, repo)
        log_decision(repo, raw_file.rel_path, decision)
        if decision.fired:
            trigger_str = ", ".join(f"{t.kind}:{t.pattern}" for t in decision.triggers)
            console.print(f"[green][Tier 1] FIRE[/green] — {trigger_str}")
        else:
            console.print("[dim][Tier 1] no-fire[/dim]")
    except Exception as exc:
        console.print(f"[dim]⚠ classify failed: {exc}[/dim]")

    # Tier 2 synthesis (runs before push so both commits land in one push)
    synth_result = None
    if decision and decision.fired and not no_synth:
        console.print("[dim][Tier 2] synthesizing...[/dim]")
        try:
            from compost.synth.agent import synthesize
            from compost.synth.diff_writer import apply_diffs, commit_diffs
            synth_result = synthesize(raw_file.path, repo)
            if synth_result.wiki_diffs:
                written = apply_diffs(repo, synth_result)
                commit_diffs(repo, written, synth_result.run_id)
                names = ", ".join(str(d.rel_path) for d in synth_result.wiki_diffs)
                console.print(f"[green][Tier 2][/green] {len(synth_result.wiki_diffs)} wiki page(s) updated: {names}")
            else:
                console.print("[dim][Tier 2] no wiki edits proposed[/dim]")
        except Exception as exc:
            console.print(f"[dim]⚠ synthesis failed: {exc}[/dim]")
            synth_result = None

    try:
        push_branch(repo, "origin", branch)
        client = _load_gitea_client(repo)
        pr = create_pr(repo, branch, client, decision, synth_result)
        console.print(f"PR #{pr.number}: {pr.url}")
    except (click.UsageError, GiteaError) as e:
        console.print(f"[yellow]⚠ push/PR failed: {e}[/yellow]")
        console.print("Push manually: [bold]git push origin " + branch + "[/bold]")


# ── pr commands ──────────────────────────────────────────────────────────────


@main.group("pr")
def pr_group() -> None:
    """Manage Gitea PRs for raw/* branches."""


@pr_group.command("open")
@click.pass_context
def pr_open(ctx: click.Context) -> None:
    """Print the Gitea PR URL for the current branch."""
    from compost.ingest.git import current_branch
    from compost.gitea.client import GiteaError

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    client = _load_gitea_client(repo)
    branch = current_branch(repo)

    try:
        pr = client.find_pr(branch)
    except GiteaError as e:
        console.print(f"[red]Gitea error: {e}[/red]")
        sys.exit(1)

    if pr is None:
        console.print(f"[red]No open PR for '{branch}'. Did `compost raw add` succeed?[/red]")
        sys.exit(1)

    console.print(f"PR #{pr.number}: {pr.url}  ({pr.state})")


@pr_group.command("merge")
@click.pass_context
def pr_merge(ctx: click.Context) -> None:
    """Merge the current raw/* branch via Gitea PR, then sync local repo."""
    from compost.ingest.pr import merge_pr
    from compost.gitea.client import GiteaError

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    client = _load_gitea_client(repo)

    try:
        pr = merge_pr(repo, client)
        console.print(f"[green]merged[/green] (PR #{pr.number})")
    except GiteaError as e:
        console.print(f"[red]Gitea error: {e}[/red]")
        sys.exit(1)


# ── classify commands ─────────────────────────────────────────────────────────


@main.group("classify")
def classify_group() -> None:
    """Run Tier 1 classifier on raw files."""


@classify_group.command("run")
@click.argument("raw_path", type=click.Path(exists=True, resolve_path=True, path_type=Path))
@click.option("--dry-run", is_flag=True, help="Classify without logging the result.")
@click.pass_context
def classify_run(ctx: click.Context, raw_path: Path, dry_run: bool) -> None:
    """Classify a raw file and print the Tier 1 verdict."""
    from compost.ingest.classifier import classify, log_decision

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    try:
        rel = raw_path.relative_to(repo)
    except ValueError:
        console.print(f"[red]{raw_path} is not under the repo root {repo}[/red]")
        sys.exit(1)

    decision = classify(raw_path, repo)

    if not dry_run:
        log_decision(repo, rel, decision)

    _print_classify_verdict(rel, decision)


@classify_group.command("replay")
@click.option("--since", default="7d", show_default=True,
              help="How far back to scan (e.g. 7d, 30d).")
@click.pass_context
def classify_replay(ctx: click.Context, since: str) -> None:
    """Classify all raw/*.md files modified within a rolling window (dry-run; no logging)."""
    import re
    from datetime import datetime, timedelta, timezone as tz
    from compost.ingest.classifier import classify

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    m = re.fullmatch(r"(\d+)d", since.strip())
    if not m:
        console.print("[red]--since must be in Nd format (e.g. 7d, 30d)[/red]")
        sys.exit(1)

    days = int(m.group(1))
    cutoff_ts = (datetime.now(tz.utc) - timedelta(days=days)).timestamp()

    raw_dir = repo / "raw"
    if not raw_dir.exists():
        console.print("[dim]No raw/ directory found.[/dim]")
        return

    files = sorted(
        (f for f in raw_dir.rglob("*.md") if f.stat().st_mtime >= cutoff_ts),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    if not files:
        console.print("[dim]No files found within window.[/dim]")
        return

    table = Table(show_header=True, box=None, padding=(0, 1))
    table.add_column("path")
    table.add_column("fired")
    table.add_column("triggers")

    for f in files:
        try:
            decision = classify(f, repo)
        except Exception:
            continue
        rel = f.relative_to(repo)
        fired_str = "[green]FIRE[/green]" if decision.fired else "[dim]-[/dim]"
        triggers_str = (
            ", ".join(f"{t.kind}:{t.pattern}" for t in decision.triggers)
            or "(none)"
        )
        table.add_row(str(rel), fired_str, triggers_str)

    console.print(table)


# ── synth commands ───────────────────────────────────────────────────────────


@main.group("synth")
def synth_group() -> None:
    """Tier 2 synthesis: propose wiki edits from raw files."""


@synth_group.command("run")
@click.option("--raw", "raw_rel", required=True,
              help="Raw file path relative to repo root.")
@click.option("--provider", default=None,
              help="Override .compost.yml synth.provider.")
@click.option("--model", default=None,
              help="Override .compost.yml synth.model.")
@click.option("--dry-run", is_flag=True, default=False,
              help="Propose edits but do not write to disk or git.")
@click.pass_context
def synth_run(ctx: click.Context, raw_rel: str, provider: str | None,
              model: str | None, dry_run: bool) -> None:
    """Run synthesis on a raw file and optionally commit wiki edits to the current branch."""
    from dataclasses import replace
    from compost.synth.agent import synthesize, load_synth_config
    from compost.synth.diff_writer import apply_diffs, commit_diffs

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    raw_path = repo / raw_rel
    if not raw_path.exists():
        console.print(f"[red]Raw file not found: {raw_path}[/red]")
        sys.exit(1)

    cfg = load_synth_config(repo)
    if provider:
        cfg = replace(cfg, provider=provider)
    if model:
        cfg = replace(cfg, model=model)

    result = synthesize(raw_path, repo, config=cfg, dry_run=dry_run)

    if not result.wiki_diffs:
        console.print("[dim][Tier 2] no wiki edits proposed[/dim]")
        return

    if dry_run:
        console.print(f"[dim][Tier 2] dry-run: {len(result.wiki_diffs)} wiki page(s) would be updated[/dim]")
        for d in result.wiki_diffs:
            tag = "new" if d.is_new else "update"
            console.print(f"  {d.rel_path} ({tag})")
        return

    written = apply_diffs(repo, result)
    commit_diffs(repo, written, result.run_id)
    console.print(f"[green][Tier 2][/green] {len(result.wiki_diffs)} wiki page(s) updated")
    for d in result.wiki_diffs:
        tag = "new" if d.is_new else "updated"
        console.print(f"  {d.rel_path} ({tag})")


@synth_group.command("log")
@click.option("--last", default=10, show_default=True,
              help="Number of recent runs to show.")
@click.pass_context
def synth_log(ctx: click.Context, last: int) -> None:
    """Show recent synthesis run history."""
    from compost.synth.log import read_runs

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    runs = read_runs(repo, last=last)
    if not runs:
        console.print("[dim]No synthesis runs found.[/dim]")
        return

    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Run ID", style="cyan")
    table.add_column("Timestamp")
    table.add_column("Raw file")
    table.add_column("Edits", justify="right")
    table.add_column("Contradictions", justify="right")
    table.add_column("Cost (USD)", justify="right")
    table.add_column("Duration (s)", justify="right")

    for r in runs:
        table.add_row(
            (r.get("run_id") or "?")[:8],
            (r.get("ts") or "")[:19].replace("T", " "),
            r.get("raw", "?"),
            str(r.get("wiki_edits", 0)),
            str(r.get("contradictions", 0)),
            f"{r.get('total_cost_usd', 0):.4f}",
            f"{r.get('duration_s', 0):.1f}",
        )

    console.print(table)


# ── gitea commands ────────────────────────────────────────────────────────────


@main.group("gitea")
def gitea_group() -> None:
    """Configure and manage the Gitea integration."""


@gitea_group.command("setup")
@click.option("--url", default="http://localhost:3000", show_default=True,
              help="Gitea base URL.")
@click.option("--owner", required=True, help="Gitea user or org login.")
@click.option("--repo", "repo_name", default=None,
              help="Gitea repo name. Defaults to .compost.yml 'name'.")
@click.option("--token", default=None, envvar="GITEA_TOKEN", help="API token.")
@click.option("--remote", default="origin", show_default=True, help="Git remote name.")
@click.option("--ssh", "use_ssh", is_flag=True, default=False,
              help="Use SSH clone URL for the remote (requires Gitea SSH to be running).")
@click.pass_context
def gitea_setup(ctx: click.Context, url: str, owner: str, repo_name: str | None,
                token: str | None, remote: str, use_ssh: bool) -> None:
    """Bootstrap Gitea integration: create repo, set remote, push main, update config."""
    from compost.gitea.client import GiteaClient, GiteaError
    from compost.ingest.git import push_branch, set_remote_url

    repo = ctx.obj["repo"] or find_repo_root(Path.cwd())
    if repo is None:
        console.print("[red]No .compost.yml found.[/red]")
        sys.exit(1)

    if not token:
        console.print("[red]GITEA_TOKEN not set. Export it or pass --token.[/red]")
        sys.exit(1)

    config = load_repo_config(repo)
    gitea_repo_name = repo_name or config["name"]

    client = GiteaClient(url=url, owner=owner, repo=gitea_repo_name, token=token)

    try:
        username = client.get_authenticated_user()
        console.print(f"[green]✓[/green] authenticated as {username}")
    except GiteaError as e:
        console.print(f"[red]Auth failed: {e}[/red]")
        sys.exit(1)

    try:
        client.create_repo(gitea_repo_name)
        console.print(f"[green]✓[/green] repo {owner}/{gitea_repo_name}")
    except GiteaError as e:
        if e.status == 403:
            repo_url = f"{url.rstrip('/')}/{owner}/{gitea_repo_name}"
            console.print(f"[yellow]⚠ Could not auto-create repo (token lacks create permission).[/yellow]")
            console.print(f"  Create it manually at {repo_url} then re-run setup.")
            console.print("  Continuing with remote + config setup...")
        else:
            console.print(f"[red]Repo creation failed: {e}[/red]")
            sys.exit(1)

    try:
        clone_url = client.get_clone_url(gitea_repo_name, ssh=use_ssh)
    except GiteaError:
        console.print("[red]Could not fetch repo clone URL — does the repo exist on Gitea?[/red]")
        sys.exit(1)
    set_remote_url(repo, remote, clone_url)
    console.print(f"[green]✓[/green] remote '{remote}' → {clone_url}")

    try:
        push_branch(repo, remote, "main")
        console.print(f"[green]✓[/green] pushed main")
    except click.UsageError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    config["gitea"] = {"url": url, "owner": owner, "repo": gitea_repo_name}
    config_path = repo / ".compost.yml"
    with config_path.open("w") as f:
        yaml.dump(config, f, default_flow_style=False)
    console.print("[green]✓[/green] .compost.yml updated with gitea config")

    subprocess.run(["git", "add", str(config_path)], cwd=repo, check=True, capture_output=True)
    commit = subprocess.run(
        ["git", "commit", "-m", "gitea: configure Gitea integration"],
        cwd=repo, capture_output=True, text=True,
    )
    if commit.returncode == 0:
        console.print("[green]✓[/green] .compost.yml committed")
    else:
        console.print(f"[yellow]⚠ Could not commit .compost.yml: {commit.stderr.strip()}[/yellow]")

    console.print("\n[bold]doctor:[/bold]")
    _render_checks(_run_doctor_checks(repo))


# ── helpers ───────────────────────────────────────────────────────────────────


def _load_gitea_client(repo: Path):
    """Build GiteaClient from .compost.yml gitea config + GITEA_TOKEN env var."""
    from compost.gitea.client import GiteaClient
    config = load_repo_config(repo)
    gitea_cfg = config.get("gitea")
    if not gitea_cfg:
        console.print("[red].compost.yml missing 'gitea:' config. Run: compost gitea setup[/red]")
        sys.exit(1)
    token = os.environ.get("GITEA_TOKEN", "")
    if not token:
        console.print("[red]GITEA_TOKEN env var is not set.[/red]")
        sys.exit(1)
    return GiteaClient(
        url=gitea_cfg["url"],
        owner=gitea_cfg["owner"],
        repo=gitea_cfg["repo"],
        token=token,
    )


# ─────────────────────────────────────────────────────────────────────────────

def _run_doctor_checks(repo: Path) -> list[tuple[str, bool, str]]:
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

    # ── Gitea checks ──
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


def _print_classify_verdict(rel: Path, decision: "ClassifyDecision") -> None:
    if decision.fired:
        trigger_str = " · ".join(f"{t.kind}:{t.pattern}" for t in decision.triggers)
        console.print(f"[green]✓ FIRE[/green]   {rel}")
        console.print(f"  {trigger_str}")
        console.print(f'  "{decision.rationale}"')
    else:
        console.print(f"[dim]  no-fire[/dim]   {rel}")
        console.print("  (no triggers matched)")


def _render_checks(checks: list[tuple[str, bool, str]]) -> None:
    table = Table(show_header=False, box=None, padding=(0, 1))
    for name, ok, msg in checks:
        icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
        detail = f"  [dim]{msg}[/dim]" if (msg and not ok) else ""
        table.add_row(icon, name + detail)
    console.print(table)
