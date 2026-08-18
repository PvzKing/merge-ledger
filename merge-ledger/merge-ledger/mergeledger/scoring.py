"""Penilaian risiko yang bisa dibaca manusia.

Prinsipnya: tidak ada skor tanpa rincian. Siapa pun yang melihat angka 72
harus bisa langsung tahu dari mana 72 itu datang, kalau tidak mereka akan
mengabaikannya — dan mereka benar untuk mengabaikannya.
"""

from __future__ import annotations

from dataclasses import dataclass

# (baik, waspada) — di bawah nilai pertama dianggap sehat, di atas nilai kedua
# dianggap bermasalah. Angka ini titik awal, bukan kebenaran; tiap tim
# sebaiknya menyetelnya setelah punya dasar sendiri.
THRESHOLDS = {
    "churn": (8.0, 18.0),        # % baris muda yang ditulis ulang
    "copypaste": (4.0, 12.0),    # % baris baru yang menyalin blok lain
    "masking": (2.0, 6.0),       # penyamaran error per 1000 baris
    "rewrite": (1.5, 3.0),       # rata-rata sentuhan ulang per file
}

WEIGHTS = {"churn": 40, "copypaste": 25, "rewrite": 20, "masking": 15}


@dataclass
class Component:
    key: str
    label: str
    value: float
    unit: str
    norm: float      # 0..1
    points: float
    verdict: str     # baik | waspada | perhatian


@dataclass
class RiskScore:
    total: float
    components: list[Component]

    @property
    def band(self) -> str:
        if self.total >= 55:
            return "perhatian"
        if self.total >= 30:
            return "waspada"
        return "baik"

    @property
    def headline(self) -> Component | None:
        """Komponen penyumbang poin terbesar — ini yang harus dibaca duluan."""
        if not self.components:
            return None
        return max(self.components, key=lambda c: c.points)


def _norm(value: float, key: str) -> tuple[float, str]:
    good, bad = THRESHOLDS[key]
    if value <= good:
        return (0.5 * value / good if good else 0.0), "baik"
    if value >= bad:
        over = min((value - bad) / bad, 1.0) if bad else 1.0
        return min(1.0, 0.85 + 0.15 * over), "perhatian"
    span = bad - good
    return 0.5 + 0.35 * ((value - good) / span), "waspada"


def score_area(area, min_lines: int = 200) -> RiskScore | None:
    """Skor untuk satu area. Area dengan aktivitas terlalu kecil dilewati:
    persentase dari 30 baris tidak berarti apa-apa."""
    if area.added < min_lines:
        return None

    rewrites_per_file = area.rewrites / area.files if area.files else 0.0
    masking_per_kloc = 1000 * area.masking / area.added if area.added else 0.0

    raw = [
        ("churn", "Ditulis ulang", area.churn_rate, "%"),
        ("copypaste", "Salin-tempel", area.copypaste_rate, "%"),
        ("rewrite", "Sentuhan ulang per file", rewrites_per_file, "×"),
        ("masking", "Penyamaran error", masking_per_kloc, "/kloc"),
    ]

    comps: list[Component] = []
    total = 0.0
    for key, label, value, unit in raw:
        norm, verdict = _norm(value, key)
        points = WEIGHTS[key] * norm
        total += points
        comps.append(
            Component(
                key=key, label=label, value=value, unit=unit,
                norm=norm, points=points, verdict=verdict,
            )
        )
    return RiskScore(total=round(total, 1), components=comps)


def verdict_for(key: str, value: float) -> str:
    return _norm(value, key)[1]


def overall_verdicts(analysis) -> dict[str, str]:
    """Vonis untuk empat angka utama di kepala laporan."""
    return {
        "churn": verdict_for("churn", analysis.churn_rate),
        "copypaste": verdict_for("copypaste", analysis.copypaste_ratio),
        "masking": verdict_for("masking", analysis.masking_per_kloc),
        "duplication": (
            # dinilai dari kode bukan-tes: pengulangan di tes sering disengaja
            "baik" if analysis.head_dup_rate_nontest < 8
            else "waspada" if analysis.head_dup_rate_nontest < 18
            else "perhatian"
        ),
    }
