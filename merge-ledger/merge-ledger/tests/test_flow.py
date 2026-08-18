"""Uji untuk antrian review dan riwayat pemindaian.

Data GitHub dipalsukan supaya uji berjalan tanpa jaringan dan hasilnya bisa
dihitung tangan.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mergeledger import github  # noqa: E402
from mergeledger.history import History  # noqa: E402

HOUR = 3600
DAY = 86400


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pr(number: int, created_ago_h: float, merged_after_h: float | None = None,
       closed_after_h: float | None = None, draft: bool = False,
       login: str = "andi", title: str = "perbaikan", branch: str = "fix/x",
       labels: list[str] | None = None) -> dict:
    now = time.time()
    created = now - created_ago_h * HOUR
    return {
        "number": number,
        "created_at": iso(created),
        "merged_at": iso(created + merged_after_h * HOUR) if merged_after_h else None,
        "closed_at": (
            iso(created + (merged_after_h or closed_after_h) * HOUR)
            if (merged_after_h or closed_after_h) else None
        ),
        "draft": draft,
        "user": {"login": login},
        "title": title,
        "head": {"ref": branch},
        "labels": [{"name": n} for n in (labels or [])],
    }


class FakeClient:
    """Menirukan GitHubClient tanpa jaringan."""

    def __init__(self, open_prs, closed_prs, reviews=None, token=None) -> None:
        self.open_prs = open_prs
        self.closed_prs = closed_prs
        self.reviews = reviews or {}
        self.token = token
        self.calls = 0

    def get(self, path, params=None):
        self.calls += 1
        if path.endswith("/pulls"):
            if (params or {}).get("page", 1) > 1:
                return []
            return self.open_prs if params.get("state") == "open" else self.closed_prs
        if "/reviews" in path:
            num = int(path.split("/pulls/")[1].split("/")[0])
            return self.reviews.get(num, [])
        return None


class AntrianTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        open_prs = [
            pr(1, created_ago_h=2),         # baru
            pr(2, created_ago_h=48),        # 2 hari
            pr(3, created_ago_h=20 * 24),   # basi
            pr(4, created_ago_h=40 * 24),   # basi, tertua
            pr(5, created_ago_h=6, draft=True),
        ]
        closed_prs = [
            pr(10, created_ago_h=100, merged_after_h=10),
            pr(11, created_ago_h=200, merged_after_h=20),
            pr(12, created_ago_h=300, merged_after_h=30),
            pr(13, created_ago_h=400, merged_after_h=200),
            pr(14, created_ago_h=150, closed_after_h=10),   # ditinggalkan
        ]
        cls.flow = github.fetch(
            Path("."), window_days=90, slug="uji/repo",
            client=FakeClient(open_prs, closed_prs),
        )

    def test_antrian_terhitung(self) -> None:
        self.assertEqual(self.flow.open_count, 5)
        self.assertEqual(self.flow.open_ready_count, 4)  # draft tidak dihitung siap

    def test_pr_basi_terdeteksi(self) -> None:
        self.assertEqual(self.flow.stale_count, 2)
        self.assertAlmostEqual(self.flow.stale_share, 40.0, places=1)
        self.assertAlmostEqual(self.flow.oldest_age_days, 40, delta=0.1)

    def test_waktu_sampai_merge(self) -> None:
        # 10, 20, 30, 200 -> median 25
        self.assertEqual(self.flow.merged_count, 4)
        self.assertAlmostEqual(self.flow.lead_median_hours, 25.0, delta=0.1)

    def test_pr_ditinggalkan_tidak_dihitung_merged(self) -> None:
        self.assertEqual(self.flow.abandoned_count, 1)
        self.assertAlmostEqual(self.flow.acceptance_rate, 80.0, places=1)

    def test_tanpa_token_waktu_tunggu_dilewati(self) -> None:
        self.assertIsNone(self.flow.pickup_median_hours)
        self.assertTrue(any("GITHUB_TOKEN" in w for w in self.flow.warnings))


class WaktuTungguTest(unittest.TestCase):
    def test_waktu_tunggu_diukur_dari_review_pertama(self) -> None:
        now = time.time()
        prs = [
            pr(20, created_ago_h=100, merged_after_h=50),
            pr(21, created_ago_h=100, merged_after_h=50),
        ]
        created = now - 100 * HOUR
        reviews = {
            20: [
                {"user": {"login": "andi"}, "submitted_at": iso(created + 1 * HOUR)},
                {"user": {"login": "budi"}, "submitted_at": iso(created + 6 * HOUR)},
            ],
            21: [{"user": {"login": "citra"}, "submitted_at": iso(created + 10 * HOUR)}],
        }
        flow = github.fetch(
            Path("."), slug="uji/repo",
            client=FakeClient([], prs, reviews, token="palsu"),
        )
        # review penulis sendiri diabaikan -> 6 jam dan 10 jam, median 8
        self.assertAlmostEqual(flow.pickup_median_hours, 8.0, delta=0.1)
        self.assertEqual(flow.pickup_sampled, 2)

    def test_merge_tanpa_review_dihitung(self) -> None:
        prs = [pr(30, created_ago_h=50, merged_after_h=5)]
        flow = github.fetch(
            Path("."), slug="uji/repo",
            client=FakeClient([], prs, {30: []}, token="palsu"),
        )
        self.assertEqual(flow.never_reviewed, 1)
        self.assertIsNone(flow.pickup_median_hours)


class PenandaAiTest(unittest.TestCase):
    def test_pr_agen_dikenali_dari_cabang_dan_label(self) -> None:
        prs = [
            pr(40, created_ago_h=50, merged_after_h=40, branch="claude/tambah-fitur"),
            pr(41, created_ago_h=50, merged_after_h=40, labels=["copilot"]),
            pr(42, created_ago_h=50, merged_after_h=4, branch="fix/biasa"),
            pr(43, created_ago_h=50, merged_after_h=6, branch="fix/lain"),
            pr(44, created_ago_h=50, merged_after_h=8, branch="fix/lagi"),
            pr(45, created_ago_h=50, merged_after_h=60, login="devin[bot]"),
        ]
        flow = github.fetch(Path("."), slug="uji/repo", client=FakeClient([], prs))
        self.assertEqual(flow.ai_pr_count, 3)
        self.assertAlmostEqual(flow.human_lead_median_hours, 6.0, delta=0.1)
        self.assertAlmostEqual(flow.ai_lead_median_hours, 40.0, delta=0.1)


class GagalDenganTenangTest(unittest.TestCase):
    """Kegagalan GitHub tidak boleh menggagalkan pemindaian."""

    def test_remote_bukan_github(self) -> None:
        flow = github.fetch(Path("/tmp"), slug=None)
        self.assertFalse(flow.ok)
        self.assertIn("bukan GitHub", flow.reason)

    def test_batas_laju_dilaporkan_jelas(self) -> None:
        class Limited(FakeClient):
            def get(self, path, params=None):
                raise github.RateLimited(" Kuota pulih dalam 30 menit.")

        flow = github.fetch(Path("."), slug="uji/repo", client=Limited([], []))
        self.assertFalse(flow.ok)
        self.assertIn("GITHUB_TOKEN", flow.reason)
        self.assertIn("30 menit", flow.reason)

    def test_repo_privat_tanpa_izin(self) -> None:
        class Denied(FakeClient):
            def get(self, path, params=None):
                raise github.NotFound()

        flow = github.fetch(Path("."), slug="uji/repo", client=Denied([], []))
        self.assertFalse(flow.ok)
        self.assertIn("tidak terjangkau", flow.reason)

    def test_tanpa_jaringan(self) -> None:
        class Off(FakeClient):
            def get(self, path, params=None):
                raise github.Offline()

        flow = github.fetch(Path("."), slug="uji/repo", client=Off([], []))
        self.assertFalse(flow.ok)
        self.assertIn("koneksi", flow.reason)


class RiwayatTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "history.json"

    class FakeAnalysis:
        def __init__(self, churn: float, at: int, window: int = 90) -> None:
            self.generated_at = at
            self.branch = "main"
            self.window_days = window
            self.churn_days = 14
            self.commits_scanned = 100
            self.lines_added = 5000
            self.churn_rate = churn
            self.copypaste_ratio = 3.0
            self.copy_vs_move = 0.5
            self.refactor_ratio = 12.0
            self.head_dup_rate_nontest = 4.0
            self.masking_per_kloc = 1.0
            self.test_ratio = 30.0

    def _hist(self, entries) -> History:
        h = History(self.path)
        for churn, days_ago in entries:
            h.record(self.FakeAnalysis(churn, int(time.time()) - days_ago * DAY))
        h.save()
        return History(self.path)

    def test_pemindaian_pertama_tanpa_pembanding(self) -> None:
        h = self._hist([(10.0, 0)])
        deltas, base = h.deltas()
        self.assertEqual(deltas, [])
        self.assertIsNone(base)

    def test_arah_membaik_terdeteksi(self) -> None:
        h = self._hist([(20.0, 30), (10.0, 0)])
        deltas, base = h.deltas()
        churn = next(d for d in deltas if d.key == "churn_rate")
        self.assertEqual(churn.direction, "turun")
        self.assertEqual(churn.meaning, "membaik")
        self.assertTrue(churn.significant)
        self.assertAlmostEqual(churn.span_days, 30, delta=0.1)

    def test_naiknya_konsolidasi_dianggap_membaik(self) -> None:
        # refactor_ratio tidak masuk LOWER_IS_BETTER, jadi naik = membaik
        h = History(self.path)
        a1 = self.FakeAnalysis(10.0, int(time.time()) - 30 * DAY)
        a1.refactor_ratio = 5.0
        a2 = self.FakeAnalysis(10.0, int(time.time()))
        a2.refactor_ratio = 15.0
        h.record(a1); h.record(a2)
        d = next(x for x in h.deltas()[0] if x.key == "refactor_ratio")
        self.assertEqual(d.meaning, "membaik")

    def test_perubahan_kecil_dianggap_derau(self) -> None:
        h = self._hist([(10.0, 30), (10.2, 0)])
        churn = next(d for d in h.deltas()[0] if d.key == "churn_rate")
        self.assertFalse(churn.significant)

    def test_pengaturan_berbeda_tidak_dibandingkan(self) -> None:
        h = History(self.path)
        h.record(self.FakeAnalysis(20.0, int(time.time()) - 30 * DAY, window=30))
        h.record(self.FakeAnalysis(10.0, int(time.time()), window=90))
        deltas, base = h.deltas()
        self.assertEqual(deltas, [])
        self.assertIsNone(base)

    def test_riwayat_rusak_tidak_menggagalkan(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{bukan json", encoding="utf-8")
        h = History(self.path)
        self.assertEqual(h.snapshots, [])
        h.record(self.FakeAnalysis(10.0, int(time.time())))
        h.save()
        self.assertIn("snapshots", json.loads(self.path.read_text()))

    def test_riwayat_dipangkas(self) -> None:
        h = History(self.path)
        for i in range(250):
            h.record(self.FakeAnalysis(10.0, int(time.time()) - i * DAY))
        self.assertLessEqual(len(h.snapshots), 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
