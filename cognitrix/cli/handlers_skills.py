"""CLI handlers for the `cognitrix skills` subcommand."""

import asyncio
import json
import sys
from pathlib import Path

from rich import print as rprint
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

from cognitrix.skills.manager import get_skill_manager
from cognitrix.skills.parser import SkillParser, SkillParseError
from cognitrix.skills.models import SkillEventType

console = Console()


def manage_skills(args):
    """Entry point for `cognitrix skills` subcommand."""
    asyncio.run(_manage_skills_async(args))


async def _manage_skills_async(args):
    """Async handler for skill management commands."""
    manager = get_skill_manager()

    # Ensure skills are discovered
    await manager.discover_all()

    if args.validate:
        await _validate_skill(args.validate)
    elif args.show:
        await _show_skill(manager, args.show)
    elif args.create:
        await _create_skill(manager, args)
    elif args.install:
        await _install_skill(manager, args.install)
    elif args.remove:
        await _remove_skill(manager, args.remove)
    elif args.run:
        skill_args = ' '.join(args.args) if hasattr(args, 'args') and args.args else ''
        await _run_skill(manager, args.run, skill_args)
    elif args.search:
        await _search_skills(manager, args.search)
    elif args.list or not args.name:
        await _list_skills(manager)
    elif args.name:
        await _show_skill(manager, args.name)


async def _list_skills(manager):
    """List all installed skills."""
    skills = await manager.list_skills()

    if not skills:
        rprint("[yellow]No skills installed.[/yellow]")
        rprint("Install skills with: cognitrix skills --install <path|url|name>")
        rprint("Create a new skill: cognitrix skills --create")
        return

    table = Table(title="Installed Skills", show_lines=True)
    table.add_column("Name", style="cyan", min_width=20)
    table.add_column("Description", style="white", max_width=60)
    table.add_column("Category", style="green")
    table.add_column("Version", style="dim")
    table.add_column("Scope", style="yellow")

    for skill in skills:
        scope = "project" if skill.source_path and '.agents' in str(skill.source_path) and not str(skill.source_path).startswith(str(Path.home())) else "global"
        if skill.source_path and 'builtin' in str(skill.source_path):
            scope = "built-in"
        table.add_row(
            f"/{skill.name}",
            skill.description[:60] + ("..." if len(skill.description) > 60 else ""),
            skill.category,
            skill.version,
            scope,
        )

    console.print(table)


async def _show_skill(manager, name: str):
    """Show details of a specific skill."""
    skill = await manager.get_skill(name)
    if not skill:
        rprint(f"[red]Skill '{name}' not found.[/red]")
        return

    # Metadata panel
    meta = f"""[cyan]Name:[/cyan] /{skill.name}
[cyan]Description:[/cyan] {skill.description}
[cyan]Version:[/cyan] {skill.version}
[cyan]Author:[/cyan] {skill.author or '(none)'}
[cyan]Category:[/cyan] {skill.category}
[cyan]Tags:[/cyan] {', '.join(skill.tags) if skill.tags else '(none)'}
[cyan]User Invocable:[/cyan] {skill.user_invocable}
[cyan]Model Invocable:[/cyan] {not skill.disable_model_invocation}
[cyan]Context:[/cyan] {skill.context or 'same'}
[cyan]Allowed Tools:[/cyan] {', '.join(skill.allowed_tools) if skill.allowed_tools else 'all'}
[cyan]Safety:[/cyan] risk={skill.safety.risk_level.value}, approval={skill.safety.requires_approval}
[cyan]Source:[/cyan] {skill.source_path or skill.source_url or '(unknown)'}"""

    console.print(Panel(meta, title=f"Skill: {skill.name}", border_style="cyan"))

    if skill.argument_hint:
        rprint(f"\n[green]Usage:[/green] /{skill.name} {skill.argument_hint}")

    if skill.body:
        console.print(Panel(skill.body, title="Instructions", border_style="dim"))


async def _create_skill(manager, args):
    """Interactive skill creation wizard."""
    rprint("[bold cyan]Create a New Skill[/bold cyan]\n")

    name = Prompt.ask("Skill name (lowercase, hyphens)", default="my-skill")
    description = Prompt.ask("Description (what it does & when to use)")
    category = Prompt.ask("Category", default="general")

    scope = Prompt.ask(
        "Scope",
        choices=["global", "project"],
        default="global",
    )
    project_scope = scope == "project"

    rprint("\n[dim]Enter the skill instructions (markdown). Type 'END' on a new line when done:[/dim]")
    body_lines = []
    while True:
        line = input()
        if line.strip() == 'END':
            break
        body_lines.append(line)
    body = '\n'.join(body_lines)

    try:
        manifest = await manager.create_skill(
            name=name,
            description=description,
            body=body,
            category=category,
            project_scope=project_scope,
        )
        rprint(f"\n[green]✓ Skill '{manifest.name}' created at {manifest.source_path}[/green]")
    except Exception as e:
        rprint(f"\n[red]✗ Failed to create skill: {e}[/red]")


async def _install_skill(manager, source: str):
    """Install a skill from path, URL, or registry name."""
    rprint(f"[cyan]Installing skill from: {source}[/cyan]")

    manifest = await manager.install_skill(source)
    if manifest:
        rprint(f"[green]✓ Installed '{manifest.name}' v{manifest.version}[/green]")
    else:
        rprint(f"[red]✗ Failed to install from '{source}'[/red]")


async def _remove_skill(manager, name: str):
    """Remove an installed skill."""
    if Confirm.ask(f"Remove skill '{name}'?"):
        removed = await manager.remove_skill(name)
        if removed:
            rprint(f"[green]✓ Removed '{name}'[/green]")
        else:
            rprint(f"[red]✗ Skill '{name}' not found[/red]")


async def _run_skill(manager, name: str, arguments: str):
    """Run a skill directly from the CLI with streaming output."""
    from cognitrix.skills.executor import SkillExecutor
    from cognitrix.providers.base import LLM
    from cognitrix.models import Agent
    from cognitrix.agents.base import AgentManager

    manifest = await manager.get_skill(name)
    if not manifest:
        rprint(f"[red]Skill '{name}' not found.[/red]")
        return

    # Initialize LLM
    llm = LLM.load_llm('openrouter')
    if not llm:
        rprint("[red]Failed to initialise LLM. Check your provider configuration.[/red]")
        return

    agent = Agent(name="skill-runner", llm=llm)
    agent_manager = AgentManager(agent)
    executor = SkillExecutor(agent_manager=agent_manager, llm=llm)

    rprint(f"\n[bold cyan]🔧 Executing skill: {manifest.name}[/bold cyan]")
    rprint("─" * 40)

    async for event in executor.execute(manifest, arguments):
        if event.type == SkillEventType.SKILL_START:
            pass
        elif event.type == SkillEventType.SKILL_CONTEXT_INJECTED:
            if event.data and event.data.get('has_dynamic_context'):
                rprint("[dim]📋 Dynamic context injected[/dim]")
        elif event.type == SkillEventType.SKILL_PROMPT_SENT:
            ctx = event.data.get('context', 'same') if event.data else 'same'
            rprint(f"[dim]📤 Prompt sent (context: {ctx})[/dim]\n")
        elif event.type == SkillEventType.SKILL_PROGRESS:
            if event.data:
                print(event.data, end="", flush=True)
        elif event.type == SkillEventType.SKILL_COMPLETE:
            rprint(f"\n\n{'─' * 40}")
            rprint("[green]✅ Skill completed[/green]")
        elif event.type == SkillEventType.SKILL_ERROR:
            rprint(f"\n[red]✗ Error: {event.data}[/red]")


async def _validate_skill(path: str):
    """Validate a SKILL.md file."""
    parser = SkillParser()
    manager = get_skill_manager()

    skill_path = Path(path)
    if not skill_path.exists():
        rprint(f"[red]File not found: {path}[/red]")
        return

    try:
        manifest = parser.parse_file(skill_path)
        errors = manager.validate_skill(manifest)

        if errors:
            rprint(f"[yellow]⚠ Validation warnings for '{manifest.name}':[/yellow]")
            for error in errors:
                rprint(f"  [yellow]• {error}[/yellow]")
        else:
            rprint(f"[green]✓ Valid skill: '{manifest.name}'[/green]")
            rprint(f"  Description: {manifest.description[:80]}")
            rprint(f"  Steps: {len(manifest.body.splitlines())} instruction lines")

    except SkillParseError as e:
        rprint(f"[red]✗ Parse error: {e}[/red]")


async def _search_skills(manager, query: str):
    """Search for skills."""
    results = await manager.search_skills(query)

    if not results:
        rprint(f"[yellow]No skills found matching '{query}'[/yellow]")
        return

    table = Table(title=f"Search Results: '{query}'")
    table.add_column("Name", style="cyan")
    table.add_column("Description", style="white", max_width=50)
    table.add_column("Category", style="green")

    for skill in results:
        table.add_row(
            f"/{skill.name}",
            skill.description[:50],
            skill.category,
        )

    console.print(table)
