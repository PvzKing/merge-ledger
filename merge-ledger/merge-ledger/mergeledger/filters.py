"""Menentukan file mana yang dihitung, dan siapa yang menulis sebuah commit."""

from __future__ import annotations

import re
from dataclasses import dataclass

SOURCE_EXTENSIONS = {
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte",
    ".go", ".rs", ".java", ".kt", ".kts", ".scala", ".rb", ".php", ".cs", ".swift",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".m", ".mm", ".sh", ".bash", ".sql", ".ex",
    ".exs", ".erl", ".dart", ".lua", ".pl", ".r", ".jl", ".hs", ".clj", ".groovy",
}

# Pola path yang dikecualikan: kode pihak ketiga, hasil build, dan file yang
# dihasilkan mesin. Kalau ini ikut terhitung, semua metrik jadi omong kosong.
EXCLUDED_PATTERNS = [
    r"(^|/)node_modules/",
    r"(^|/)vendor/",
    r"(^|/)third_party/",
    r"(^|/)dist/",
    r"(^|/)build/",
    r"(^|/)out/",
    r"(^|/)target/",
    r"(^|/)\.next/",
    r"(^|/)coverage/",
    r"(^|/)__pycache__/",
    r"(^|/)site-packages/",
    r"(^|/)migrations?/",
    r"(^|/)generated/",
    r"\.min\.(js|css)$",
    r"\.bundle\.js$",
    r"\.lock$",
    r"(^|/)(package-lock|yarn|pnpm-lock|poetry\.lock|Cargo\.lock|composer\.lock)",
    r"\.(pb|pb2)\.(go|py)$",
    r"_pb2\.py$",
    r"\.generated\.",
    r"(^|/)testdata/",
    r"(^|/)fixtures?/",
    r"(^|/)snapshots?/",
    r"\.snap$",
]

_EXCLUDED_RE = re.compile("|".join(EXCLUDED_PATTERNS))

TEST_PATTERNS = re.compile(
    r"(^|/)tests?/|(^|/)spec/|_test\.|\.test\.|\.spec\.|test_[^/]*$|_spec\.",
)


def is_source_file(path: str) -> bool:
    if _EXCLUDED_RE.search(path):
        return False
    dot = path.rfind(".")
    if dot == -1:
        return False
    return path[dot:].lower() in SOURCE_EXTENSIONS


def is_test_file(path: str) -> bool:
    return bool(TEST_PATTERNS.search(path))


def top_level_area(path: str, depth: int = 2) -> str:
    """Kelompokkan file ke area, dipakai untuk tabel risiko per modul."""
    parts = [p for p in path.split("/") if p]
    if len(parts) <= 1:
        return "(root)"
    return "/".join(parts[: min(depth, len(parts) - 1)])


# --- Atribusi penulis -------------------------------------------------------

DEFAULT_AI_MARKERS = [
    r"co-authored-by:.*claude",
    r"co-authored-by:.*copilot",
    r"co-authored-by:.*cursor",
    r"co-authored-by:.*devin",
    r"co-authored-by:.*aider",
    r"co-authored-by:.*codex",
    r"co-authored-by:.*gemini",
    r"generated with \[?claude",
    r"🤖 generated",
    r"\bassisted-by:\s*\w",
    r"^\s*ai-generated:\s*true",
]

AI_EMAIL_MARKERS = re.compile(
    r"(noreply@anthropic|copilot@|bot@cursor|devin@|@users\.noreply\.github\.com.*bot)",
    re.I,
)

AI_NAME_MARKERS = re.compile(r"\b(claude|copilot|cursor|devin|aider|codex|\[bot\])\b", re.I)


@dataclass
class Attributor:
    """Menebak apakah sebuah commit ditulis dengan bantuan AI.

    Deteksi ini berbasis jejak yang ditinggalkan alat (trailer commit, nama
    author). Kalau tim tidak menandai commit sama sekali, hasilnya akan nol —
    itu bukan bug, itu artinya jejaknya memang tidak ada.
    """

    extra_patterns: list[str] | None = None

    def __post_init__(self) -> None:
        pats = list(DEFAULT_AI_MARKERS) + list(self.extra_patterns or [])
        self._re = re.compile("|".join(f"(?:{p})" for p in pats), re.I | re.M)

    def is_ai(self, message: str, author_name: str, author_email: str) -> bool:
        if self._re.search(message):
            return True
        if AI_EMAIL_MARKERS.search(author_email):
            return True
        if AI_NAME_MARKERS.search(author_name):
            return True
        return False

    def coverage_note(self, total: int, ai: int) -> str:
        if total == 0:
            return "Tidak ada commit dalam rentang ini."
        if ai == 0:
            return (
                "Tidak ada commit yang menandai bantuan AI. Angka perbandingan "
                "AI vs manusia tidak bisa dihitung sampai tim mulai menandai "
                "commit (mis. trailer Co-Authored-By)."
            )
        pct = 100 * ai / total
        return f"{pct:.0f}% commit membawa penanda bantuan AI."
