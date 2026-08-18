"""Uji terhadap repositori sintetis yang nilainya sudah diketahui.

Setiap metrik diuji dengan repo kecil yang isinya dirancang supaya jawabannya
bisa dihitung tangan. Kalau angka di sini bergeser, ada yang rusak.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mergeledger.filters import is_source_file, is_test_file  # noqa: E402
from mergeledger.metrics import Analyzer, normalize, scan_masking  # noqa: E402
from mergeledger.scoring import score_area  # noqa: E402

DAY = 86400


class RepoBuilder:
    """Membangun repo git dengan tanggal commit yang dikendalikan."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "uji@contoh.test")
        self._git("config", "user.name", "Penguji")

    def _git(self, *args: str, env: dict | None = None) -> None:
        subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True, capture_output=True,
            env={**os.environ, **(env or {})},
        )

    def write(self, path: str, content: str) -> None:
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def commit(self, message: str, days_ago: float) -> None:
        stamp = time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - days_ago * DAY)
        )
        env = {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
        self._git("add", "-A", env=env)
        self._git("commit", "-q", "-m", message, env=env)


def body(prefix: str, n: int, suffix: str = "") -> str:
    lines = [f"    {prefix}_{i} = hitung_{i}(masukan, konteks){suffix}" for i in range(n)]
    return "def jalankan(masukan, konteks):\n" + "\n".join(lines) + "\n"


class ChurnTest(unittest.TestCase):
    """Baris muda yang ditulis ulang dihitung; baris tua tidak."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dir = Path(tempfile.mkdtemp())
        b = RepoBuilder(cls.dir)

        b.write("svc/inti.py", body("nilai", 20))
        b.commit("tambah inti", days_ago=40)

        # 10 baris diubah pada umur 5 hari -> masuk hitungan
        lines = (cls.dir / "svc/inti.py").read_text().split("\n")
        for i in range(1, 11):
            lines[i] = f"    nilai_{i-1} = hitung_{i-1}(masukan, konteks, ulang=3)"
        b.write("svc/inti.py", "\n".join(lines))
        b.commit("perbaiki pengulangan", days_ago=35)

        b.write("api/rute.py", body("langkah", 30))
        b.commit("tambah rute", days_ago=30)

        # 5 baris diubah pada umur 28 hari -> di luar ambang 14 hari
        lines = (cls.dir / "api/rute.py").read_text().split("\n")
        for i in range(1, 6):
            lines[i] = f"    langkah_{i-1} = hitung_{i-1}(masukan, konteks, ketat=True)"
        b.write("api/rute.py", "\n".join(lines))
        b.commit("perketat rute", days_ago=2)

        cls.a = Analyzer(cls.dir, window_days=60, skip_head_scan=True,
                         progress=lambda *_: None).run()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_hanya_baris_muda_dihitung(self) -> None:
        self.assertEqual(self.a.churned_lines, 10)

    def test_baris_tua_diabaikan(self) -> None:
        # 5 baris rute berumur 28 hari tidak boleh ikut
        self.assertNotIn(self.a.churned_lines, (15, 5))

    def test_penyebut_adalah_seluruh_baris_baru(self) -> None:
        self.assertEqual(self.a.lines_added, 21 + 10 + 31 + 5)

    def test_area_terpisah(self) -> None:
        area = {x.name: x for x in self.a.areas}
        self.assertEqual(area["svc"].churned, 10)
        self.assertEqual(area["api"].churned, 0)

    def test_ambang_bisa_diubah(self) -> None:
        longgar = Analyzer(self.dir, window_days=60, churn_days=45,
                           skip_head_scan=True, progress=lambda *_: None).run()
        self.assertEqual(longgar.churned_lines, 15)


class SalinDanPindahTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dir = Path(tempfile.mkdtemp())
        b = RepoBuilder(cls.dir)

        blok = "\n".join([
            "    koneksi = kolam.ambil(batas_waktu=30)",
            "    kursor = koneksi.kursor(kamus=True)",
            "    kursor.jalankan(kueri, parameter)",
            "    baris = kursor.ambil_semua()",
            "    kursor.tutup()",
            "    kolam.lepas(koneksi)",
        ])
        b.write("svc/db.py", f"def ambil_a(kueri, parameter):\n{blok}\n\n"
                            f"def ambil_b(kueri, parameter):\n{blok}\n")
        b.commit("tambah akses db", days_ago=10)

        b.write("svc/asal.py", body("langkah", 16))
        b.commit("tambah asal", days_ago=9)

        # pindahkan 8 baris ke file lain dalam satu commit
        lines = (cls.dir / "svc/asal.py").read_text().split("\n")
        dipindah = lines[9:17]
        b.write("svc/asal.py", "\n".join(lines[:9] + lines[17:]))
        b.write("svc/bantu.py", "def bantu(masukan, konteks):\n" + "\n".join(dipindah) + "\n")
        b.commit("pindahkan ke bantu", days_ago=8)

        cls.a = Analyzer(cls.dir, window_days=60, skip_head_scan=False,
                         progress=lambda *_: None).run()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_salinan_terdeteksi_sekali(self) -> None:
        # blok 6 baris muncul dua kali; kemunculan pertama dianggap asli
        self.assertEqual(self.a.copypaste_lines, 6)

    def test_pemindahan_terdeteksi(self) -> None:
        self.assertEqual(self.a.moved_lines, 8)

    def test_duplikasi_di_head(self) -> None:
        self.assertGreater(self.a.head_dup_lines, 0)
        self.assertGreater(self.a.head_dup_rate, 10)

    def test_rasio_salin_banding_pindah(self) -> None:
        self.assertAlmostEqual(self.a.copy_vs_move, 6 / 8, places=3)


class AtribusiTest(unittest.TestCase):
    """Churn dibebankan ke penulis asal baris, bukan ke yang menghapusnya."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dir = Path(tempfile.mkdtemp())
        b = RepoBuilder(cls.dir)

        b.write("svc/agen.py", body("nilai", 12))
        b.commit("tambah fitur\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
                 days_ago=20)

        lines = (cls.dir / "svc/agen.py").read_text().split("\n")
        for i in range(1, 7):
            lines[i] = f"    nilai_{i-1} = hitung_{i-1}(masukan, konteks, aman=True)"
        b.write("svc/agen.py", "\n".join(lines))
        b.commit("perbaiki oleh manusia", days_ago=17)

        cls.a = Analyzer(cls.dir, window_days=60, skip_head_scan=True,
                         progress=lambda *_: None).run()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_commit_ai_dikenali(self) -> None:
        self.assertEqual(self.a.commits_ai, 1)

    def test_churn_dibebankan_ke_asal(self) -> None:
        # yang menulis ulang adalah manusia, tapi baris aslinya dari commit AI
        self.assertEqual(self.a.churned_ai, 6)
        self.assertEqual(self.a.churned_human, 0)

    def test_pola_tambahan_bisa_didaftarkan(self) -> None:
        a = Analyzer(self.dir, window_days=60, skip_head_scan=True,
                     ai_patterns=[r"perbaiki oleh manusia"],
                     progress=lambda *_: None).run()
        self.assertEqual(a.commits_ai, 2)


class PenyamaranTest(unittest.TestCase):
    def test_pass_biasa_tidak_dihitung(self) -> None:
        masking, _ = scan_masking([
            "class Protokol:",
            "    pass",
            "def stub():",
            "    ...",
        ])
        self.assertEqual(sum(masking.values()), 0)

    def test_pass_setelah_except_dihitung(self) -> None:
        masking, _ = scan_masking(["    except Exception:", "        pass"])
        self.assertEqual(sum(masking.values()), 1)

    def test_except_telanjang(self) -> None:
        masking, _ = scan_masking(["    except:"])
        self.assertEqual(sum(masking.values()), 1)

    def test_catch_kosong_javascript(self) -> None:
        masking, _ = scan_masking(["  } catch (e) {}"])
        self.assertEqual(sum(masking.values()), 1)

    def test_penekan_dihitung_terpisah(self) -> None:
        masking, sup = scan_masking([
            "import foo  # noqa",
            "const x = y  // @ts-ignore",
        ])
        self.assertEqual(sum(masking.values()), 0)
        self.assertEqual(sum(sup.values()), 2)


class PenyaringTest(unittest.TestCase):
    def test_kode_pihak_ketiga_dikecualikan(self) -> None:
        for p in ["node_modules/a/b.js", "vendor/x.go", "dist/app.min.js",
                  "src/x_pb2.py", "package-lock.json", "migrations/001_x.py"]:
            self.assertFalse(is_source_file(p), p)

    def test_kode_sendiri_dihitung(self) -> None:
        for p in ["src/app.ts", "svc/inti.py", "cmd/main.go", "lib/a.rb"]:
            self.assertTrue(is_source_file(p), p)

    def test_file_tes_dikenali(self) -> None:
        self.assertTrue(is_test_file("tests/test_a.py"))
        self.assertTrue(is_test_file("src/a.test.ts"))
        self.assertFalse(is_test_file("src/latest.ts"))

    def test_normalisasi_membuang_komentar_dan_baris_pendek(self) -> None:
        self.assertIsNone(normalize("   # catatan"))
        self.assertIsNone(normalize("  }"))
        self.assertEqual(normalize("  a   =  b(c)  "), "a = b(c)")


class AntarmukaTest(unittest.TestCase):
    """Bantuan dan kode keluar adalah bagian dari antarmuka, jadi diuji juga."""

    def test_bantuan_bisa_dicetak(self) -> None:
        # argparse menafsirkan % di teks bantuan; bug ini tidak muncul sampai
        # seseorang mengetik --help.
        from mergeledger.cli import build_parser

        self.assertIn("mergeledger", build_parser().format_help())

    def test_semua_ambang_terdaftar(self) -> None:
        from mergeledger.cli import build_parser

        p = build_parser()
        ns = p.parse_args(["--max-churn", "10", "--max-duplication", "20"])
        self.assertEqual(ns.max_churn, 10.0)
        self.assertEqual(ns.max_duplication, 20.0)
        self.assertIsNone(ns.max_copypaste)


class SkorTest(unittest.TestCase):
    class Area:
        def __init__(self, **kw) -> None:
            self.__dict__.update(kw)

        @property
        def churn_rate(self) -> float:
            return 100 * self.churned / self.added

        @property
        def copypaste_rate(self) -> float:
            return 100 * self.copypaste / self.added

    def _area(self, churned: int, added: int = 1000, copypaste: int = 0,
              masking: int = 0, rewrites: int = 0, files: int = 10):
        return self.Area(name="x", added=added, churned=churned, copypaste=copypaste,
                         masking=masking, rewrites=rewrites, files=files)

    def test_area_kecil_dilewati(self) -> None:
        self.assertIsNone(score_area(self._area(churned=50, added=100)))

    def test_area_sehat_bernilai_rendah(self) -> None:
        s = score_area(self._area(churned=20))
        self.assertLess(s.total, 30)
        self.assertEqual(s.band, "baik")

    def test_area_buruk_bernilai_tinggi(self) -> None:
        s = score_area(self._area(churned=300, copypaste=200, masking=20, rewrites=60))
        self.assertGreater(s.total, 80)
        self.assertEqual(s.band, "perhatian")

    def test_komponen_menjelaskan_skor(self) -> None:
        s = score_area(self._area(churned=300))
        self.assertAlmostEqual(sum(c.points for c in s.components), s.total, places=1)
        self.assertEqual(s.headline.key, "churn")


if __name__ == "__main__":
    unittest.main(verbosity=2)
