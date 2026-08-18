"""Perhitungan metrik dari riwayat git.

Empat sinyal utama:

  churn        Baris yang ditulis lalu ditulis ulang dalam waktu singkat.
               Ini ukuran "kode yang belum matang saat di-merge".
  duplikasi    Blok kode yang berulang. Tiap duplikat adalah beban perawatan
               terpisah saat logikanya perlu berubah.
  pemindahan   Baris yang dipindah antar file dalam satu commit. Ini tanda
               konsolidasi alias refactoring yang sehat.
  penyamaran   Konstruksi yang menelan error diam-diam (except kosong, catch
               kosong, err diabaikan). Naik saat kode ditulis tanpa dipahami.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import gitio
from .filters import Attributor, is_source_file, is_test_file, top_level_area

DAY = 86400
BLOCK_SIZE = 5  # panjang minimum blok untuk dihitung sebagai duplikasi

_COMMENT_RE = re.compile(r"^\s*(//|#|\*|/\*|\*/|--|<!--|\"\"\"|''')")
_WS_RE = re.compile(r"\s+")

# Pola yang menelan kegagalan saat program berjalan. Sebagian butuh konteks
# baris berikutnya, karena `pass` sendirian adalah hal yang wajar di Python —
# yang bermasalah adalah `pass` tepat setelah `except`.
_MASK_STANDALONE = [
    ("except telanjang", re.compile(r"^\s*except\s*:\s*(#.*)?$")),
    ("except langsung ditelan", re.compile(r"^\s*except\b.*:\s*(pass|\.\.\.)\s*(#.*)?$")),
    ("catch kosong", re.compile(r"catch\s*\([^)]*\)\s*\{\s*\}")),
    ("promise ditelan", re.compile(r"\.catch\s*\(\s*(\(\s*\)|\w+)?\s*=>\s*\{?\s*\}?\s*\)")),
    ("rescue nil", re.compile(r"\brescue\s+nil\b")),
    ("err dibuang", re.compile(r"^\s*_\s*(,\s*_\s*)*(:)?=\s*\w+.*\berr\b|^\s*_\s*=\s*err\b")),
    ("cast ke any", re.compile(r"\bas\s+any\b")),
]

# Butuh baris berikutnya: (pembuka, badan yang berarti "tidak melakukan apa-apa")
_MASK_OPENER = re.compile(r"^\s*(except\b.*|catch\s*\([^)]*\)\s*\{|rescue\b.*)$")
_MASK_EMPTY_BODY = re.compile(r"^\s*(pass|\.\.\.|continue|return\s+(None|nil|null)?|\}|;)\s*(#.*)?$")

# Penekan alat statis. Bukan penelan error saat berjalan, jadi dihitung
# terpisah supaya tidak mengaburkan sinyal yang lebih keras.
_SUPPRESSORS = [
    ("noqa", re.compile(r"#\s*noqa")),
    ("type: ignore", re.compile(r"#\s*type:\s*ignore")),
    ("ts-ignore", re.compile(r"@ts-(ignore|nocheck|expect-error)")),
    ("eslint-disable", re.compile(r"eslint-disable")),
    ("nolint", re.compile(r"//\s*nolint")),
    ("pylint disable", re.compile(r"#\s*pylint:\s*disable")),
]


def scan_masking(added: list[str]) -> tuple[Counter, Counter]:
    """Pisahkan penelan error saat berjalan dari penekan alat statis."""
    masking: Counter = Counter()
    suppression: Counter = Counter()

    for i, line in enumerate(added):
        matched = False
        for label, rx in _MASK_STANDALONE:
            if rx.search(line):
                masking[label] += 1
                matched = True
                break
        if not matched and _MASK_OPENER.match(line):
            nxt = added[i + 1] if i + 1 < len(added) else ""
            if _MASK_EMPTY_BODY.match(nxt):
                masking["blok tangkap kosong"] += 1
                matched = True
        if matched:
            continue
        for label, rx in _SUPPRESSORS:
            if rx.search(line):
                suppression[label] += 1
                break

    return masking, suppression


def normalize(line: str) -> str | None:
    """Bentuk baku sebuah baris untuk pembandingan.

    Mengembalikan None untuk baris yang tidak layak dibandingkan: kosong,
    komentar, atau terlalu pendek (kurung tutup, dsb).
    """
    s = line.strip()
    if len(s) < 8:
        return None
    if _COMMENT_RE.match(s):
        return None
    return _WS_RE.sub(" ", s)


@dataclass
class AreaStat:
    name: str
    files: int = 0
    added: int = 0
    deleted: int = 0
    churned: int = 0
    copypaste: int = 0
    moved: int = 0
    masking: int = 0
    commits: int = 0
    rewrites: int = 0  # berapa kali file di area ini disentuh ulang

    @property
    def churn_rate(self) -> float:
        return 100 * self.churned / self.added if self.added else 0.0

    @property
    def copypaste_rate(self) -> float:
        return 100 * self.copypaste / self.added if self.added else 0.0


@dataclass
class FileStat:
    path: str
    added: int = 0
    deleted: int = 0
    churned: int = 0
    touches: int = 0
    last_touch: int = 0
    authors: set = field(default_factory=set)

    @property
    def churn_rate(self) -> float:
        return 100 * self.churned / self.added if self.added else 0.0


@dataclass
class WeekBucket:
    label: str
    start_ts: int = 0
    added: int = 0
    churned: int = 0
    commits: int = 0
    copypaste: int = 0
    moved: int = 0


@dataclass
class Analysis:
    repo_name: str = ""
    branch: str = ""
    window_days: int = 90
    churn_days: int = 14
    generated_at: int = 0
    scan_seconds: float = 0.0

    commits_total: int = 0
    commits_ai: int = 0
    commits_scanned: int = 0
    truncated: bool = False

    lines_added: int = 0
    lines_deleted: int = 0
    churned_lines: int = 0
    churned_ai: int = 0
    churned_human: int = 0
    added_ai: int = 0
    added_human: int = 0

    moved_lines: int = 0
    copypaste_lines: int = 0
    masking_added: int = 0
    masking_breakdown: Counter = field(default_factory=Counter)
    suppression_added: int = 0
    suppression_breakdown: Counter = field(default_factory=Counter)

    test_added: int = 0

    head_lines_scanned: int = 0
    head_dup_lines: int = 0
    head_dup_blocks: int = 0
    head_files_scanned: int = 0
    head_lines_nontest: int = 0
    head_dup_lines_nontest: int = 0

    areas: list[AreaStat] = field(default_factory=list)
    files: list[FileStat] = field(default_factory=list)
    weeks: list[WeekBucket] = field(default_factory=list)
    attribution_note: str = ""
    warnings: list[str] = field(default_factory=list)

    # --- turunan ---
    @property
    def churn_rate(self) -> float:
        return 100 * self.churned_lines / self.lines_added if self.lines_added else 0.0

    @property
    def churn_rate_ai(self) -> float:
        return 100 * self.churned_ai / self.added_ai if self.added_ai else 0.0

    @property
    def churn_rate_human(self) -> float:
        return 100 * self.churned_human / self.added_human if self.added_human else 0.0

    @property
    def refactor_ratio(self) -> float:
        """Porsi baris yang dipindah (konsolidasi) dari total baris ditulis."""
        total = self.lines_added + self.moved_lines
        return 100 * self.moved_lines / total if total else 0.0

    @property
    def copypaste_ratio(self) -> float:
        return 100 * self.copypaste_lines / self.lines_added if self.lines_added else 0.0

    @property
    def copy_vs_move(self) -> float:
        """Rasio salin-tempel terhadap pemindahan. Di atas 1 = lebih banyak
        menyalin daripada mengonsolidasi."""
        return self.copypaste_lines / self.moved_lines if self.moved_lines else float("inf")

    @property
    def head_dup_rate(self) -> float:
        return 100 * self.head_dup_lines / self.head_lines_scanned if self.head_lines_scanned else 0.0

    @property
    def head_dup_rate_nontest(self) -> float:
        """Angka yang sama, tapi tanpa file tes.

        Pengulangan di berkas tes sering disengaja dan tidak berbahaya, jadi
        angka gabungan bisa menakut-nakuti tanpa alasan pada repo yang
        suite-nya besar. Yang ini biasanya lebih layak ditindaklanjuti.
        """
        return (
            100 * self.head_dup_lines_nontest / self.head_lines_nontest
            if self.head_lines_nontest else 0.0
        )

    @property
    def masking_per_kloc(self) -> float:
        return 1000 * self.masking_added / self.lines_added if self.lines_added else 0.0

    @property
    def suppression_per_kloc(self) -> float:
        return 1000 * self.suppression_added / self.lines_added if self.lines_added else 0.0

    @property
    def test_ratio(self) -> float:
        return 100 * self.test_added / self.lines_added if self.lines_added else 0.0


class Analyzer:
    def __init__(
        self,
        repo: Path,
        window_days: int = 90,
        churn_days: int = 14,
        branch: str | None = None,
        max_commits: int = 1500,
        ai_patterns: list[str] | None = None,
        skip_head_scan: bool = False,
        workers: int = 8,
        progress=None,
    ) -> None:
        self.repo = repo
        self.window_days = window_days
        self.churn_days = churn_days
        self.branch = branch
        self.max_commits = max_commits
        self.attributor = Attributor(extra_patterns=ai_patterns)
        self.skip_head_scan = skip_head_scan
        self.workers = max(1, workers)
        self.progress = progress or (lambda *_: None)

    def run(self) -> Analysis:
        t0 = time.time()
        a = Analysis(
            repo_name=gitio.repo_name(self.repo),
            branch=self.branch or gitio.current_branch(self.repo),
            window_days=self.window_days,
            churn_days=self.churn_days,
            generated_at=int(time.time()),
        )

        # git log --since menyaring berdasarkan tanggal commit, sementara umur
        # baris dihitung dari tanggal penulisan (author date) lewat blame. Di
        # repo dengan alur patch, keduanya bisa terpaut berbulan-bulan. Jadi
        # jaring lebih lebar dulu, lalu saring sendiri pakai tanggal penulisan.
        since = f"{int(self.window_days * 1.5) + 30} days ago"
        commits = gitio.list_commits(
            self.repo, since=since, branch=self.branch, max_commits=None
        )
        cutoff = int(time.time()) - self.window_days * DAY
        commits = [c for c in commits if c.timestamp >= cutoff]
        commits.sort(key=lambda c: c.timestamp, reverse=True)
        a.commits_total = len(commits)
        if len(commits) > self.max_commits:
            commits = commits[: self.max_commits]
            a.truncated = True
            a.warnings.append(
                f"Riwayat dipotong di {self.max_commits} commit terbaru. "
                "Naikkan --max-commits untuk cakupan penuh."
            )
        a.commits_scanned = len(commits)

        if not commits:
            a.warnings.append("Tidak ada commit dalam rentang waktu ini.")
            a.scan_seconds = time.time() - t0
            return a

        ai_map = {
            c.sha: self.attributor.is_ai(c.message, c.author_name, c.author_email)
            for c in commits
        }
        a.commits_ai = sum(ai_map.values())
        a.attribution_note = self.attributor.coverage_note(a.commits_total, a.commits_ai)

        areas: dict[str, AreaStat] = {}
        files: dict[str, FileStat] = {}
        weeks: dict[str, WeekBucket] = {}
        area_files: dict[str, set] = defaultdict(set)

        total = len(commits)
        # Diproses per kelompok: panggilan git dijalankan paralel dalam satu
        # kelompok, lalu hasilnya diagregasi berurutan. Git adalah proses
        # terpisah, jadi thread benar-benar berjalan bersamaan di sini.
        batch = 120
        for start in range(0, total, batch):
            group = commits[start : start + batch]
            self.progress(start, total)

            paths_by_sha = {}
            for c in group:
                src = [f for f in c.files if not f.is_binary and is_source_file(f.path)]
                paths_by_sha[c.sha] = src

            hunks_by_sha = self._fetch_hunks(group, paths_by_sha)

            churn_tasks: list[tuple] = []
            for c in group:
                src = paths_by_sha[c.sha]
                if not src:
                    continue
                wk = self._process_volume(c, src, ai_map, a, areas, files, weeks, area_files)
                hunks = [h for h in hunks_by_sha.get(c.sha, []) if is_source_file(h.path)]
                if not hunks:
                    continue
                self._count_moved_and_copied(c, hunks, a, areas, wk)
                self._count_masking(hunks, a, areas)
                churn_tasks.extend(self._collect_churn_tasks(c, hunks))

            self._apply_churn(churn_tasks, ai_map, a, areas, files, weeks)

        self.progress(total, total)

        for name, st in areas.items():
            st.files = len(area_files[name])
        a.areas = sorted(areas.values(), key=lambda s: s.added, reverse=True)
        a.files = sorted(
            files.values(), key=lambda f: (f.churned, f.touches), reverse=True
        )
        a.weeks = _fill_week_gaps(weeks)

        if not self.skip_head_scan:
            self._scan_head_duplication(a)

        a.scan_seconds = time.time() - t0
        return a

    # -- per commit ---------------------------------------------------------

    def _fetch_hunks(self, group, paths_by_sha) -> dict:
        """Ambil diff untuk sekelompok commit sekaligus."""
        work = [(c.sha, [f.path for f in paths_by_sha[c.sha]]) for c in group
                if paths_by_sha[c.sha]]
        if not work:
            return {}
        out: dict = {}
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {
                pool.submit(gitio.commit_hunks, self.repo, sha, paths): sha
                for sha, paths in work
            }
            for fut in as_completed(futures):
                try:
                    out[futures[fut]] = fut.result()
                except Exception:
                    out[futures[fut]] = []
        return out

    def _process_volume(self, c, source_files, ai_map, a, areas, files, weeks, area_files):
        week_key = datetime.fromtimestamp(c.timestamp, timezone.utc).strftime("%G-W%V")
        wk = weeks.setdefault(week_key, WeekBucket(label=week_key, start_ts=c.timestamp))
        wk.start_ts = min(wk.start_ts or c.timestamp, c.timestamp)
        wk.commits += 1

        is_ai = ai_map.get(c.sha, False)

        for f in source_files:
            a.lines_added += f.added
            a.lines_deleted += f.deleted
            wk.added += f.added
            if is_test_file(f.path):
                a.test_added += f.added
            if is_ai:
                a.added_ai += f.added
            else:
                a.added_human += f.added

            area = top_level_area(f.path)
            st = areas.setdefault(area, AreaStat(name=area))
            st.added += f.added
            st.deleted += f.deleted
            st.commits += 1
            area_files[area].add(f.path)

            fs = files.setdefault(f.path, FileStat(path=f.path))
            fs.added += f.added
            fs.deleted += f.deleted
            fs.touches += 1
            fs.last_touch = max(fs.last_touch, c.timestamp)
            fs.authors.add(c.author_name)
            if fs.touches > 1:
                st.rewrites += 1
        return wk

    def _collect_churn_tasks(self, c, hunks) -> list[tuple]:
        """Siapkan permintaan blame; eksekusinya dilakukan paralel nanti."""
        if not c.parent:
            return []
        by_file: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for h in hunks:
            if h.removed and h.old_count > 0:
                by_file[h.path].append((h.old_start, h.old_count))
        if not by_file:
            return []
        rename_map = {f.path: f.old_path for f in c.files if f.old_path}
        return [
            (c.sha, c.timestamp, c.parent, path, rename_map.get(path) or path, ranges)
            for path, ranges in by_file.items()
        ]

    def _apply_churn(self, tasks, ai_map, a, areas, files, weeks) -> None:
        if not tasks:
            return
        threshold = self.churn_days * DAY

        def fetch(task):
            _, _, parent, _, blame_path, ranges = task
            return task, gitio.blame_lines(self.repo, parent, blame_path, ranges)

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for fut in as_completed([pool.submit(fetch, t) for t in tasks]):
                try:
                    task, blamed = fut.result()
                except Exception:
                    continue
                if not blamed:
                    continue
                _, ts, _, path, _, _ = task

                young = young_ai = young_human = 0
                for origin_sha, atime in blamed:
                    if ts - atime > threshold:
                        continue
                    young += 1
                    origin_is_ai = ai_map.get(origin_sha)
                    if origin_is_ai is True:
                        young_ai += 1
                    elif origin_is_ai is False:
                        young_human += 1
                if not young:
                    continue

                a.churned_lines += young
                a.churned_ai += young_ai
                a.churned_human += young_human
                week_key = datetime.fromtimestamp(ts, timezone.utc).strftime("%G-W%V")
                if week_key in weeks:
                    weeks[week_key].churned += young
                area = top_level_area(path)
                if area in areas:
                    areas[area].churned += young
                if path in files:
                    files[path].churned += young

    def _count_moved_and_copied(self, c, hunks, a, areas, wk) -> None:
        added_by_file: dict[str, list[str]] = defaultdict(list)
        removed_all: Counter = Counter()

        for h in hunks:
            for line in h.added:
                n = normalize(line)
                if n:
                    added_by_file[h.path].append(n)
            for line in h.removed:
                n = normalize(line)
                if n:
                    removed_all[n] += 1

        # Pemindahan: baris yang hilang di satu file dan muncul di file lain.
        moved_total = 0
        for path, lines in added_by_file.items():
            counts = Counter(lines)
            for line, n in counts.items():
                # hanya hitung jika baris itu juga dihapus di commit ini
                if removed_all.get(line):
                    take = min(n, removed_all[line])
                    moved_total += take
                    areas[top_level_area(path)].moved += take
        a.moved_lines += moved_total
        wk.moved += moved_total

        # Salin-tempel dalam satu commit: blok >= BLOCK_SIZE baris yang muncul
        # lebih dari sekali di antara baris yang baru ditambahkan.
        window_positions: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for path, lines in added_by_file.items():
            for i in range(len(lines) - BLOCK_SIZE + 1):
                key = _hash_block(lines[i : i + BLOCK_SIZE])
                window_positions[key].append((path, i))

        covered: dict[str, set] = defaultdict(set)
        for key, positions in window_positions.items():
            if len(positions) < 2:
                continue
            for path, i in positions[1:]:  # kemunculan pertama dianggap asli
                covered[path].update(range(i, i + BLOCK_SIZE))
        for path, idxs in covered.items():
            a.copypaste_lines += len(idxs)
            areas[top_level_area(path)].copypaste += len(idxs)
            wk.copypaste += len(idxs)

    def _count_masking(self, hunks, a, areas) -> None:
        for h in hunks:
            masking, suppression = scan_masking(h.added)
            hits = sum(masking.values())
            if hits:
                a.masking_breakdown.update(masking)
                a.masking_added += hits
                areas[top_level_area(h.path)].masking += hits
            if suppression:
                a.suppression_breakdown.update(suppression)
                a.suppression_added += sum(suppression.values())

    # -- kondisi saat ini ---------------------------------------------------

    def _scan_head_duplication(self, a: Analysis, max_files: int = 6000) -> None:
        """Hitung berapa banyak kode di HEAD yang berada di dalam blok kembar."""
        paths = [p for p in gitio.list_tracked_files(self.repo) if is_source_file(p)]
        if len(paths) > max_files:
            a.warnings.append(
                f"Pemindaian duplikasi dibatasi pada {max_files} dari {len(paths)} file."
            )
            paths = paths[:max_files]

        blocks: dict[str, list[tuple[int, int]]] = defaultdict(list)
        file_lines: dict[int, int] = {}
        is_test: dict[int, bool] = {}
        total_lines = 0

        def load(item):
            idx, p = item
            return idx, p, gitio.read_file_at(self.repo, "HEAD", p)

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            loaded = list(pool.map(load, enumerate(paths)))

        for idx, p, content in loaded:
            if content is None:
                continue
            lines = [n for n in (normalize(l) for l in content.split("\n")) if n]
            if len(lines) < BLOCK_SIZE:
                continue
            file_lines[idx] = len(lines)
            is_test[idx] = is_test_file(p)
            total_lines += len(lines)
            for i in range(len(lines) - BLOCK_SIZE + 1):
                blocks[_hash_block(lines[i : i + BLOCK_SIZE])].append((idx, i))

        covered: dict[int, set] = defaultdict(set)
        dup_blocks = 0
        for positions in blocks.values():
            if len(positions) < 2:
                continue
            dup_blocks += 1
            for idx, i in positions:
                covered[idx].update(range(i, i + BLOCK_SIZE))

        a.head_files_scanned = len(file_lines)
        a.head_lines_scanned = total_lines
        a.head_dup_blocks = dup_blocks
        a.head_dup_lines = sum(len(s) for s in covered.values())
        a.head_lines_nontest = sum(
            n for idx, n in file_lines.items() if not is_test.get(idx)
        )
        a.head_dup_lines_nontest = sum(
            len(s) for idx, s in covered.items() if not is_test.get(idx)
        )


def _fill_week_gaps(weeks: dict[str, WeekBucket]) -> list[WeekBucket]:
    """Minggu tanpa commit tetap muncul sebagai slot kosong.

    Kalau minggu sepi dihilangkan, sumbu waktu jadi bohong: jeda dua bulan
    terlihat sama dengan jeda satu minggu.
    """
    if not weeks:
        return []
    stamps = [w.start_ts for w in weeks.values() if w.start_ts]
    if not stamps:
        return [weeks[k] for k in sorted(weeks)]

    start = datetime.fromtimestamp(min(stamps), timezone.utc)
    end = datetime.fromtimestamp(max(stamps), timezone.utc)
    cur = (start - timedelta(days=start.isoweekday() - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    while cur <= end:
        key = cur.strftime("%G-W%V")
        if key not in weeks:
            weeks[key] = WeekBucket(label=key, start_ts=int(cur.timestamp()))
        cur += timedelta(days=7)
    return [weeks[k] for k in sorted(weeks)]


def _hash_block(lines: list[str]) -> str:
    return hashlib.blake2b("\n".join(lines).encode("utf-8"), digest_size=12).hexdigest()
