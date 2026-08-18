"""Lapisan tipis di atas perintah git.

Semua interaksi dengan repositori lewat sini supaya sisa program tidak perlu
tahu format keluaran git.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

REC = "\x1e"  # pemisah antar commit
FLD = "\x1f"  # pemisah antar field


class GitError(RuntimeError):
    pass


def run_git(repo: Path, args: list[str], check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if check and proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()
        msg = detail[0] if detail else "penyebab tidak diketahui"
        raise GitError(f"perintah git {args[0]} gagal: {msg[:200]}")
    return proc.stdout


def is_git_repo(repo: Path) -> bool:
    try:
        out = run_git(repo, ["rev-parse", "--is-inside-work-tree"])
    except GitError:
        return False
    return out.strip() == "true"


def repo_name(repo: Path) -> str:
    try:
        url = run_git(repo, ["remote", "get-url", "origin"]).strip()
        if url:
            return url.rstrip("/").removesuffix(".git").split("/")[-1]
    except GitError:
        pass
    return repo.resolve().name


def current_branch(repo: Path) -> str:
    try:
        return run_git(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    except GitError:
        return "?"


@dataclass
class FileChange:
    """Satu file yang berubah dalam satu commit."""

    path: str
    old_path: str | None
    added: int
    deleted: int
    is_binary: bool = False


@dataclass
class Commit:
    sha: str
    parent: str | None
    timestamp: int
    author_name: str
    author_email: str
    subject: str
    body: str
    files: list[FileChange] = field(default_factory=list)

    @property
    def short(self) -> str:
        return self.sha[:8]

    @property
    def message(self) -> str:
        return f"{self.subject}\n{self.body}"


def list_commits(
    repo: Path,
    since: str,
    branch: str | None = None,
    include_merges: bool = False,
    max_commits: int | None = None,
) -> list[Commit]:
    """Ambil commit beserta statistik per file dalam satu panggilan git."""
    fmt = REC + FLD.join(["%H", "%P", "%at", "%an", "%ae", "%s", "%b"])
    args = [
        "log",
        f"--since={since}",
        f"--pretty=format:{fmt}",
        "--numstat",
        "--find-renames",
    ]
    if not include_merges:
        args.append("--no-merges")
    if max_commits:
        args.append(f"--max-count={max_commits}")
    if branch:
        args.append(branch)

    try:
        raw = run_git(repo, args)
    except GitError as e:
        # Repositori yang baru dibuat belum punya HEAD. Itu bukan galat,
        # hanya berarti tidak ada apa-apa untuk dihitung.
        if "does not have any commits" in str(e) or "unknown revision" in str(e):
            return []
        raise
    commits: list[Commit] = []

    for chunk in raw.split(REC):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        head, _, tail = chunk.partition("\n")
        parts = head.split(FLD)
        if len(parts) < 7:
            continue
        sha, parents, ts, an, ae, subject, body_head = parts[:7]

        # Baris numstat dimulai setelah body. Body bisa multi-baris, jadi kita
        # pisahkan dengan mendeteksi pola numstat: "<angka|-> TAB <angka|-> TAB path".
        body_lines = [body_head]
        file_lines: list[str] = []
        in_files = False
        for line in tail.split("\n"):
            if not in_files and _looks_like_numstat(line):
                in_files = True
            if in_files:
                if line.strip():
                    file_lines.append(line)
            else:
                body_lines.append(line)

        files = [f for f in (_parse_numstat(l) for l in file_lines) if f]
        parent = parents.split()[0] if parents.strip() else None

        commits.append(
            Commit(
                sha=sha,
                parent=parent,
                timestamp=int(ts),
                author_name=an,
                author_email=ae,
                subject=subject,
                body="\n".join(body_lines).strip(),
                files=files,
            )
        )
    return commits


def _looks_like_numstat(line: str) -> bool:
    parts = line.split("\t")
    if len(parts) < 3:
        return False
    a, d = parts[0], parts[1]
    return (a.isdigit() or a == "-") and (d.isdigit() or d == "-")


def _parse_numstat(line: str) -> FileChange | None:
    parts = line.split("\t")
    if len(parts) < 3:
        return None
    a, d, path = parts[0], parts[1], "\t".join(parts[2:])
    old_path = None

    # Rename muncul sebagai "old => new" atau "dir/{old => new}/file".
    if "=>" in path:
        old_path, path = _resolve_rename(path)

    if a == "-" or d == "-":
        return FileChange(path=path, old_path=old_path, added=0, deleted=0, is_binary=True)
    return FileChange(path=path, old_path=old_path, added=int(a), deleted=int(d))


def _resolve_rename(path: str) -> tuple[str, str]:
    if "{" in path and "}" in path:
        pre, rest = path.split("{", 1)
        mid, post = rest.split("}", 1)
        old_mid, _, new_mid = mid.partition(" => ")
        old = f"{pre}{old_mid}{post}".replace("//", "/")
        new = f"{pre}{new_mid}{post}".replace("//", "/")
        return old, new
    old, _, new = path.partition(" => ")
    return old.strip(), new.strip()


@dataclass
class Hunk:
    """Satu blok perubahan dalam satu file."""

    path: str
    old_start: int
    old_count: int
    added: list[str]
    removed: list[str]


def commit_hunks(repo: Path, sha: str, paths: list[str] | None = None) -> list[Hunk]:
    """Ambil hunk per file dengan konteks nol supaya mudah diurai."""
    args = ["show", sha, "-U0", "--format=", "--find-renames", "--no-color"]
    if paths:
        args += ["--", *paths]
    try:
        raw = run_git(repo, args)
    except GitError:
        return []

    hunks: list[Hunk] = []
    path = None
    cur: Hunk | None = None

    for line in raw.split("\n"):
        if line.startswith("diff --git "):
            if cur:
                hunks.append(cur)
                cur = None
            path = None
        elif line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("+++ ") and line.endswith("/dev/null"):
            path = None
        elif line.startswith("@@"):
            if cur:
                hunks.append(cur)
                cur = None
            if path is None:
                continue
            old_start, old_count = _parse_hunk_header(line)
            cur = Hunk(path=path, old_start=old_start, old_count=old_count, added=[], removed=[])
        elif cur is not None:
            if line.startswith("+"):
                cur.added.append(line[1:])
            elif line.startswith("-"):
                cur.removed.append(line[1:])
    if cur:
        hunks.append(cur)
    return hunks


def _parse_hunk_header(line: str) -> tuple[int, int]:
    # Format: @@ -12,3 +12,4 @@ opsional konteks
    try:
        old_part = line.split(" ")[1]  # "-12,3"
        old_part = old_part.lstrip("-")
        if "," in old_part:
            start, count = old_part.split(",", 1)
            return int(start), int(count)
        return int(old_part), 1
    except (IndexError, ValueError):
        return 0, 0


def blame_lines(
    repo: Path, rev: str, path: str, ranges: list[tuple[int, int]]
) -> list[tuple[str, int]]:
    """Kembalikan (sha asal, author-time) untuk tiap baris pada rentang diminta.

    Satu panggilan blame menangani banyak rentang sekaligus supaya hemat proses.
    """
    if not ranges:
        return []
    args = ["blame", "--line-porcelain", "--no-progress"]
    used = 0
    for start, count in ranges:
        if count <= 0 or start <= 0:
            continue
        args += ["-L", f"{start},+{count}"]
        used += 1
    if used == 0:
        return []
    args += [rev, "--", path]

    try:
        raw = run_git(repo, args)
    except GitError:
        return []

    out: list[tuple[str, int]] = []
    pending_sha: str | None = None
    for line in raw.split("\n"):
        if not line:
            continue
        if len(line) >= 40 and line[:40].isalnum() and " " in line:
            head = line.split(" ", 1)[0]
            if len(head) == 40:
                pending_sha = head
                continue
        if line.startswith("author-time ") and pending_sha:
            try:
                out.append((pending_sha, int(line.split(" ", 1)[1])))
            except ValueError:
                pass
            pending_sha = None
    return out


def list_tracked_files(repo: Path, rev: str = "HEAD") -> list[str]:
    try:
        raw = run_git(repo, ["ls-tree", "-r", "--name-only", rev])
    except GitError:
        return []
    return [l for l in raw.split("\n") if l.strip()]


def read_file_at(repo: Path, rev: str, path: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"{rev}:{path}"],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if proc.returncode != 0:
        return None
    return proc.stdout
