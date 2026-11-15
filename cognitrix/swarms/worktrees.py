"""A separate copy of a git repository for a swarm worker that edits files.

When several workers of one swarm may change files in the same repository,
each gets its own git worktree on its own branch, so they never touch the
same file. The worker's changes are committed on that branch when it
finishes, and the lead merges them back (``merge``).

Everything here is blocking git; call it with ``asyncio.to_thread``.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

GIT_TIMEOUT = 60
BRANCH_PREFIX = 'cognitrix-swarm'


class WorktreeError(Exception):
    pass


def _git(cwd: str | Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    try:
        done = subprocess.run(
            ['git', '-C', str(cwd), *args],
            capture_output=True, text=True, timeout=GIT_TIMEOUT, encoding='utf-8', errors='replace',
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        raise WorktreeError('git is not installed') from None
    except subprocess.TimeoutExpired:
        raise WorktreeError(f'git {args[0]} took too long') from None
    if check and done.returncode != 0:
        raise WorktreeError((done.stderr or done.stdout or f'git {args[0]} failed').strip()[:500])
    return done


def repo_root(folder: str | Path) -> str | None:
    """The top folder of the git repository ``folder`` is, or None when it
    is not the top of one (a subfolder gets no separate copy)."""
    try:
        path = Path(folder).resolve()
        if not path.is_dir():
            return None
        done = _git(path, 'rev-parse', '--show-toplevel', check=False)
    except (WorktreeError, OSError):
        return None
    if done.returncode != 0:
        return None
    try:
        top = Path(done.stdout.strip()).resolve()
    except OSError:
        return None
    return str(top) if top == path else None


def _safe(text: str) -> str:
    return re.sub(r'[^A-Za-z0-9_.-]', '-', text).strip('-.')[:40] or 'worker'


INSIDE_DIR = '.cognitrix-swarm'


def _ignore_locally(root: Path) -> None:
    """Keep the copies made inside a repository out of its ``git status``
    (in .git/info/exclude, which is never committed)."""
    try:
        common = _git(root, 'rev-parse', '--git-common-dir').stdout.strip()
        exclude = (root / common if not Path(common).is_absolute() else Path(common)) / 'info' / 'exclude'
        line = f'/{INSIDE_DIR}/'
        text = exclude.read_text(encoding='utf-8') if exclude.is_file() else ''
        if line not in text.splitlines():
            exclude.parent.mkdir(parents=True, exist_ok=True)
            exclude.write_text(text + ('' if not text or text.endswith('\n') else '\n') + line + '\n', encoding='utf-8')
    except (WorktreeError, OSError):
        pass


def create(repo: str, swarm_id: str, worker: str, *, inside: bool = False) -> dict[str, Any]:
    """Make a worktree for ``worker``: next to the repository, or with
    ``inside`` in a folder of the repository that git is told to ignore (for
    servers that only allow folders under the project). Returns
    {'repo', 'path', 'branch', 'base'}."""
    root = Path(repo)
    short = _safe(swarm_id)[:8]
    branch = f'{BRANCH_PREFIX}/{short}/{_safe(worker)}'
    holder = root / INSIDE_DIR if inside else root.parent / f'.{root.name}-swarm'
    path = holder / f'{short}-{_safe(worker)}'
    if path.exists():
        raise WorktreeError(f'{path} already exists')
    base = _git(root, 'rev-parse', 'HEAD').stdout.strip()
    if inside:
        _ignore_locally(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(root, 'worktree', 'add', '-b', branch, str(path), 'HEAD')
    return {'repo': str(root), 'path': str(path), 'branch': branch, 'base': base}


def commit(tree: dict[str, Any], message: str) -> dict[str, Any]:
    """Commit whatever the worker left in its worktree. Returns the tree with
    'changed' (files that differ from where it started) filled in."""
    path = tree.get('path') or ''
    if not path or not Path(path).is_dir():
        return {**tree, 'changed': []}
    _git(path, 'add', '-A')
    if _git(path, 'status', '--porcelain').stdout.strip():
        _git(path, '-c', 'user.name=Cognitrix swarm', '-c', 'user.email=swarm@cognitrix.local',
             'commit', '-q', '-m', message[:200] or 'Swarm worker changes')
    base = tree.get('base') or 'HEAD~1'
    names = _git(path, 'diff', '--name-only', base, 'HEAD', check=False).stdout.split('\n')
    return {**tree, 'changed': [n for n in (x.strip() for x in names) if n][:200]}


def merge(tree: dict[str, Any]) -> tuple[bool, str]:
    """Merge a worker's branch into the repository's current branch.

    (True, summary) when it merged; (False, why) when it could not, with the
    repository left as it was.
    """
    repo, branch = tree.get('repo') or '', tree.get('branch') or ''
    if not repo or not branch:
        return False, 'This worker has no separate copy to merge.'
    if not tree.get('changed'):
        return True, 'It changed no files, so there was nothing to merge.'
    if _git(repo, 'status', '--porcelain', '--untracked-files=no').stdout.strip():
        return False, ('The folder has changes that are not committed. Commit or put them aside first, '
                       'then merge again.')
    done = _git(repo, '-c', 'user.name=Cognitrix swarm', '-c', 'user.email=swarm@cognitrix.local',
                'merge', '--no-ff', '--no-edit', branch, check=False)
    if done.returncode != 0:
        conflicts = _git(repo, 'diff', '--name-only', '--diff-filter=U', check=False).stdout.split()
        _git(repo, 'merge', '--abort', check=False)
        listed = ', '.join(conflicts[:20]) or (done.stderr or done.stdout).strip()[:300]
        return False, f'Its changes clash with what is already there: {listed}. Nothing was merged.'
    return True, f'Merged {len(tree.get("changed") or [])} changed file(s) from {branch}.'


def remove(tree: dict[str, Any], *, delete_branch: bool = True) -> None:
    """Take the worktree away (after a merge, or when its work is dropped)."""
    repo, path, branch = tree.get('repo') or '', tree.get('path') or '', tree.get('branch') or ''
    if not repo:
        return
    try:
        if path and Path(path).exists():
            _git(repo, 'worktree', 'remove', '--force', path, check=False)
        _git(repo, 'worktree', 'prune', check=False)
        if delete_branch and branch:
            _git(repo, 'branch', '-D', branch, check=False)
        parent = Path(path).parent if path else None
        if parent is not None and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except (WorktreeError, OSError):
        pass
