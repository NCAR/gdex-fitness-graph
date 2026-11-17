"""Commit and push a per-user graph file to the data repo after it is saved.

Serialized across gunicorn workers by an flock inside ``.git``. Each user owns a
different file, so conflicts are not expected; a rejected push is rebased once.
"""
import fcntl
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

TIMEOUT = 20


class GitSyncError(Exception):
    pass


def _git(repo, *args, identity=(), check=True):
    cmd = ["git", "-C", str(repo), *identity, *args]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitSyncError(f"git {args[0]} failed: {exc}") from exc
    if check and result.returncode != 0:
        raise GitSyncError(f"git {args[0]} failed: {(result.stderr or result.stdout).strip()}")
    return result


@contextmanager
def _lock(repo):
    git_dir = Path(repo) / ".git"
    if not git_dir.is_dir():
        raise GitSyncError(f"{repo} is not a git checkout")
    with open(git_dir / "fitness-sync.lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def sync_file(repo, path, user):
    repo = Path(repo).resolve()
    try:
        rel = str(Path(path).resolve().relative_to(repo))
    except ValueError as exc:
        raise GitSyncError(f"{path} is outside the git checkout {repo}") from exc
    identity = ("-c", f"user.name={user}", "-c", f"user.email={user}@gdex-fitness-graph.invalid")
    with _lock(repo):
        _git(repo, "add", "--", rel)
        staged = _git(repo, "diff", "--cached", "--quiet", "--", rel, check=False)
        if staged.returncode == 1:
            _git(repo, "commit", "-m", f"Update graph for {user}", identity=identity)
        elif staged.returncode != 0:
            raise GitSyncError("git diff failed")
        # Always push: this also delivers commits left over from an earlier failed push.
        if _git(repo, "push", "origin", "HEAD", check=False).returncode == 0:
            return
        pulled = _git(repo, "pull", "--rebase", "--autostash", "origin", "HEAD", identity=identity, check=False)
        if pulled.returncode != 0:
            _git(repo, "rebase", "--abort", check=False)
            raise GitSyncError(f"git pull --rebase failed: {(pulled.stderr or pulled.stdout).strip()}")
        _git(repo, "push", "origin", "HEAD")
