from __future__ import annotations

import sys
from pathlib import Path

import click

from compost.cli._helpers import console

SOURCE_CHOICES = [
    "slack", "incident", "decision", "note", "meeting", "support",
    "checkpoint", "commits",
]


@click.group("raw")
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
@click.option("--assay", is_flag=True, default=False,
              help="Run round-trip assay after synthesis. Blocks push if assay fails.")
@click.pass_context
def raw_add(ctx: click.Context, source: str, title: str, captured_by: str | None,
            origin: str, channel: str, no_synth: bool, assay: bool) -> None:
    """Add a raw source file from stdin, commit on a new branch, and open a Gitea PR."""
    from datetime import datetime, timezone
    from typing import cast

    from compost.ingest.git import assert_git_repo, create_branch_and_commit, get_git_user_name, push_branch
    from compost.ingest.pr import create_pr
    from compost.ingest.raw import slugify, write_raw, SOURCE_TYPES
    from compost.gitea.client import GiteaError
    from compost.repo import find_repo_root
    from compost.cli._helpers import _load_gitea_client

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
    branch = f"raw/{ts:%Y-%m-%dT%H%M%S}-{slugify(title)}"

    decision = None
    classifications_path = repo / ".compost" / "classifications.jsonl"
    try:
        from compost.ingest.classifier import classify, log_decision
        decision = classify(raw_file.path, repo)
        log_decision(repo, raw_file.rel_path, decision)
    except Exception as exc:
        console.print(f"[dim]⚠ classify failed: {exc}[/dim]")

    commit_files = [raw_file.path]
    if classifications_path.exists():
        commit_files.append(classifications_path)
    create_branch_and_commit(
        repo, branch, commit_files,
        message=f"raw: {title}",
    )

    console.print(f"[green]✓[/green] {raw_file.rel_path}")
    console.print(f"branch: {branch}")

    if decision is not None:
        if decision.fired:
            trigger_str = ", ".join(f"{t.kind}:{t.pattern}" for t in decision.triggers)
            console.print(f"[green][Tier 1] FIRE[/green] — {trigger_str}")
        else:
            console.print("[dim][Tier 1] no-fire[/dim]")

    synth_result = None
    if decision and decision.fired and not no_synth:
        from compost.worker.worker import worker_mode
        mode = worker_mode(repo)
        if mode == "async":
            from compost.worker.queue import enqueue as _enqueue
            _enqueue(repo, str(raw_file.rel_path), branch, source="cli")
            console.print("[dim][Tier 2] queued for async worker[/dim]")
        else:
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

    if assay and synth_result and synth_result.wiki_diffs:
        console.print("[dim][Assay] running round-trip validation...[/dim]")
        try:
            from compost.codify.codegen import codify as _codify
            from compost.codify.compiler import compile_kt
            from compost.codify.renderer import render_wiki
            from compost.codify.compare import compare as _compare
            _codify(repo)
            cr = compile_kt(repo)
            if not cr.success:
                console.print("[red][Assay] compile failed — push blocked[/red]")
                for err in cr.errors:
                    console.print(f"  line {err.kt_line}: {err.message}")
                sys.exit(1)
            rendered = render_wiki(repo)
            assay_result = _compare(repo, rendered)
            if assay_result.passed:
                console.print(f"[green][Assay] PASS[/green] — {assay_result.checked} page(s)")
            else:
                console.print(f"[red][Assay] FAIL — {len(assay_result.failures)} failure(s) — push blocked[/red]")
                for f in assay_result.failures:
                    console.print(f"  {f.page_id}: {f.missing_fields} {f.changed_fields}")
                sys.exit(1)
        except RuntimeError as e:
            console.print(f"[yellow]⚠ assay error (kotlinc/java not available?): {e}[/yellow]")
            console.print("[yellow]Continuing without assay validation.[/yellow]")

    try:
        push_branch(repo, "origin", branch)
        client = _load_gitea_client(repo)
        pr = create_pr(repo, branch, client, decision, synth_result)
        console.print(f"PR #{pr.number}: {pr.url}")
    except (click.UsageError, GiteaError) as e:
        console.print(f"[yellow]⚠ push/PR failed: {e}[/yellow]")
        console.print("Push manually: [bold]git push origin " + branch + "[/bold]")
