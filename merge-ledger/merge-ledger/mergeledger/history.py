"""Menyimpan hasil tiap pemindaian supaya arah perubahan terlihat.

Satu angka churn 11% tidak berarti apa-apa sendirian. Yang berarti adalah
"11%, naik dari 7% bulan lalu" atau "11%, turun dari 19%". Modul ini menyimpan
jejaknya di dalam repositori supaya perbandingan itu mungkin.

Berkasnya sengaja kecil, berformat JSON biasa, dan boleh ikut di-commit —
supaya seluruh tim melihat garis dasar yang sama, bukan angka masing-masing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = ".merge-ledger/history.json"
MAX_SNAPSHOTS = 200

# Untuk metrik ini, turun berarti membaik.
LOWER_IS_BETTER = {
    "churn_rate", "copypaste_ratio", "copy_vs_move",
    "head_duplication_rate_nontest", "masking_per_kloc",
    "suppression_per_kloc", "review_queue_depth", "pickup_median_hours",
}


@dataclass
class Delta:
    key: str
    now: float
    before: float
    span_days: float

    @property
    def change(self) -> float:
        return self.now - self.before

    @property
    def pct_change(self) -> float:
        return 100 * self.change / self.before if self.before else 0.0

    @property
    def direction(self) -> str:
        """naik | turun | tetap — arah mentah, tanpa penilaian."""
        if abs(self.change) < 1e-9:
            return "tetap"
        return "naik" if self.change > 0 else "turun"

    @property
    def meaning(self) -> str:
        """membaik | memburuk | tetap — arah setelah dinilai."""
        if self.direction == "tetap":
            return "tetap"
        lower_better = self.key in LOWER_IS_BETTER
        improving = (self.change < 0) if lower_better else (self.change > 0)
        return "membaik" if improving else "memburuk"

    @property
    def significant(self) -> bool:
        """Perubahan di bawah 5% relatif dianggap derau, bukan sinyal."""
        return abs(self.pct_change) >= 5.0


class History:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.snapshots: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("snapshots", [])
            self.snapshots = [s for s in data if isinstance(s, dict)]
        except (json.JSONDecodeError, OSError):
            # Riwayat rusak tidak boleh menggagalkan pemindaian.
            self.snapshots = []

    def record(self, analysis, pull_flow=None) -> None:
        snap = {
            "at": analysis.generated_at,
            "branch": analysis.branch,
            "window_days": analysis.window_days,
            "churn_days": analysis.churn_days,
            "commits": analysis.commits_scanned,
            "lines_added": analysis.lines_added,
            "metrics": {
                "churn_rate": round(analysis.churn_rate, 3),
                "copypaste_ratio": round(analysis.copypaste_ratio, 3),
                "copy_vs_move": (
                    None if analysis.copy_vs_move == float("inf")
                    else round(analysis.copy_vs_move, 3)
                ),
                "refactor_ratio": round(analysis.refactor_ratio, 3),
                "head_duplication_rate_nontest": round(analysis.head_dup_rate_nontest, 3),
                "masking_per_kloc": round(analysis.masking_per_kloc, 3),
                "test_ratio": round(analysis.test_ratio, 3),
            },
        }
        if pull_flow and pull_flow.ok:
            snap["metrics"]["review_queue_depth"] = pull_flow.open_count
            if pull_flow.pickup_median_hours is not None:
                snap["metrics"]["pickup_median_hours"] = round(
                    pull_flow.pickup_median_hours, 2
                )
        self.snapshots.append(snap)
        self.snapshots = self.snapshots[-MAX_SNAPSHOTS:]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "note": (
                "Riwayat pemindaian Merge Ledger. Boleh di-commit supaya seluruh "
                "tim membaca garis dasar yang sama."
            ),
            "snapshots": self.snapshots,
        }
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def comparable(self, current: dict) -> list[dict]:
        """Hanya snapshot dengan pengaturan sama yang layak dibandingkan.

        Membandingkan jendela 90 hari dengan 30 hari akan menghasilkan
        'perbaikan' yang murni artefak pengaturan.
        """
        return [
            s
            for s in self.snapshots[:-1]
            if s.get("window_days") == current.get("window_days")
            and s.get("churn_days") == current.get("churn_days")
            and s.get("branch") == current.get("branch")
        ]

    def deltas(self, min_gap_days: float = 3.0) -> tuple[list[Delta], dict | None]:
        """Bandingkan pemindaian terbaru dengan pembanding terdekat yang layak.

        Pembanding harus berjarak minimal beberapa hari — dua pemindaian dalam
        satu jam akan selalu terlihat 'tetap' dan hanya jadi derau.
        """
        if len(self.snapshots) < 2:
            return [], None
        current = self.snapshots[-1]
        candidates = self.comparable(current)
        if not candidates:
            return [], None

        gap_needed = min_gap_days * 86400
        older = [s for s in candidates if current["at"] - s["at"] >= gap_needed]
        base = older[-1] if older else candidates[-1]

        span = (current["at"] - base["at"]) / 86400
        out: list[Delta] = []
        for key, now in current["metrics"].items():
            before = base["metrics"].get(key)
            if now is None or before is None:
                continue
            out.append(Delta(key=key, now=float(now), before=float(before), span_days=span))
        return out, base

    def series(self, key: str) -> list[tuple[int, float]]:
        """Deret waktu satu metrik, untuk grafik tren."""
        out = []
        for s in self.snapshots:
            v = s.get("metrics", {}).get(key)
            if v is not None:
                out.append((s["at"], float(v)))
        return out


LABELS = {
    "churn_rate": "Ditulis ulang",
    "copypaste_ratio": "Salin-tempel",
    "copy_vs_move": "Salin vs pindah",
    "refactor_ratio": "Porsi konsolidasi",
    "head_duplication_rate_nontest": "Kode kembar",
    "masking_per_kloc": "Penyamaran error",
    "test_ratio": "Porsi baris tes",
    "review_queue_depth": "Antrian review",
    "pickup_median_hours": "Waktu tunggu review",
}

UNITS = {
    "copy_vs_move": "×",
    "masking_per_kloc": "/kloc",
    "review_queue_depth": "",
    "pickup_median_hours": " jam",
}


def label_for(key: str) -> str:
    return LABELS.get(key, key)


def unit_for(key: str) -> str:
    return UNITS.get(key, "%")
