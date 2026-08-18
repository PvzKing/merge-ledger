"""Antarmuka baris perintah untuk Merge Ledger."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import os

from . import github, gitio, report
from .history import DEFAULT_PATH, History
from .metrics import Analyzer
from .scoring import overall_verdicts

BANNER = "merge-ledger"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mergeledger",
        description=(
            "Ukur apa yang terjadi pada kode setelah di-merge: berapa yang "
            "ditulis ulang, berapa yang menyalin, dan di area mana."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Contoh:\n"
            "  mergeledger .                       laporan 90 hari terakhir\n"
            "  mergeledger ~/repo --days 180       rentang lebih panjang\n"
            "  mergeledger . --json hasil.json     untuk diolah lebih lanjut\n"
            "  mergeledger . --max-churn 15        gagal kalau churn di atas 15%\n"
        ),
    )
    p.add_argument("repo", nargs="?", default=".", help="path repositori git (bawaan: .)")
    p.add_argument("--days", type=int, default=90, help="rentang riwayat, hari (bawaan 90)")
    p.add_argument(
        "--churn-days", type=int, default=14,
        help="batas umur baris untuk dihitung ditulis ulang (bawaan 14)",
    )
    p.add_argument("--branch", default=None, help="cabang yang dianalisa (bawaan: HEAD)")
    p.add_argument(
        "--max-commits", type=int, default=1500,
        help="batas commit yang dipindai (bawaan 1500)",
    )
    p.add_argument(
        "-o", "--out", default="merge-ledger.html",
        help="berkas laporan HTML (bawaan merge-ledger.html)",
    )
    p.add_argument("--json", dest="json_out", default=None, help="tulis hasil sebagai JSON")
    p.add_argument(
        "--no-head-scan", action="store_true",
        help="lewati pemindaian duplikasi seluruh repo (lebih cepat)",
    )
    p.add_argument(
        "--ai-pattern", action="append", default=None,
        help="regex tambahan penanda commit AI, bisa diulang",
    )
    p.add_argument("--quiet", action="store_true", help="jangan tampilkan progres")
    p.add_argument(
        "--workers", type=int, default=0,
        help="jumlah proses git paralel (0 = sesuaikan dengan mesin)",
    )

    h = p.add_argument_group("riwayat dan tren")
    h.add_argument(
        "--history", default=None,
        help=f"berkas riwayat (bawaan <repo>/{DEFAULT_PATH})",
    )
    h.add_argument("--no-history", action="store_true", help="jangan simpan riwayat")

    gh = p.add_argument_group("antrian review (GitHub, bersifat pilihan)")
    gh.add_argument("--no-github", action="store_true", help="lewati pengambilan data PR")
    gh.add_argument(
        "--token", default=None,
        help="token GitHub; bawaan dari GITHUB_TOKEN atau GH_TOKEN",
    )
    gh.add_argument(
        "--slug", default=None,
        help="owner/repo bila tidak bisa ditebak dari remote",
    )
    gh.add_argument(
        "--pickup-sample", type=int, default=40,
        help="berapa PR disampel untuk waktu tunggu review (bawaan 40)",
    )
    p.add_argument("--no-html", action="store_true", help="jangan tulis berkas HTML")

    g = p.add_argument_group("gerbang CI (keluar dengan kode 1 bila terlampaui)")
    g.add_argument("--max-churn", type=float, default=None, help="ambang %% ditulis ulang")
    g.add_argument("--max-copypaste", type=float, default=None, help="ambang %% salin-tempel")
    g.add_argument("--max-duplication", type=float, default=None, help="ambang %% kode kembar, di luar berkas tes")
    g.add_argument("--max-area-score", type=float, default=None, help="ambang skor area tertinggi")
    g.add_argument(
        "--max-stale-prs", type=int, default=None,
        help="ambang jumlah PR yang menunggu lebih dari 14 hari",
    )
    return p


def _progress(quiet: bool):
    if quiet:
        return lambda *_: None

    def show(done: int, total: int) -> None:
        width = 28
        filled = int(width * done / total) if total else width
        bar = "█" * filled + "·" * (width - filled)
        pct = 100 * done / total if total else 100
        end = "\n" if done >= total else ""
        sys.stderr.write(f"\r  memindai {bar} {pct:5.1f}%  {done}/{total}{end}")
        sys.stderr.flush()

    return show


def _default_workers() -> int:
    """Satu inti tidak untung dari paralelisme, hanya kena biaya tambahan."""
    cpu = os.cpu_count() or 1
    return 1 if cpu <= 1 else min(8, cpu * 2)


def _trend_lines(deltas, base) -> list[str]:
    if not deltas or not base:
        return []
    from .history import label_for, unit_for

    out = [f"  dibanding {int(deltas[0].span_days)} hari lalu:"]
    shown = 0
    for d in sorted(deltas, key=lambda x: -abs(x.pct_change)):
        if not d.significant:
            continue
        tanda = "+" if d.change > 0 else ""
        out.append(
            f"     {label_for(d.key):<22} {d.before:>7.1f} -> {d.now:.1f}"
            f"{unit_for(d.key)}  ({tanda}{d.pct_change:.0f}%) {d.meaning}"
        )
        shown += 1
        if shown >= 4:
            break
    if shown == 0:
        out.append("     tidak ada perubahan berarti")
    return out


def _summary(a, flow=None) -> str:
    v = overall_verdicts(a)
    rows = [
        ("ditulis ulang", f"{a.churn_rate:.1f}%", v["churn"]),
        ("salin vs pindah", f"{a.copy_vs_move:.2f}×" if a.copy_vs_move != float("inf") else "∞",
         "perhatian" if a.copy_vs_move > 1 else "baik"),
        ("kode kembar", f"{a.head_dup_rate_nontest:.1f}%", v["duplication"]),
        ("penyamaran error", f"{a.masking_per_kloc:.2f}/kloc", v["masking"]),
    ]
    if flow is not None and flow.ok:
        from .github import verdict as fv

        v2 = fv(flow)
        rows.append(("antri review", f"{flow.open_count} PR", v2["queue"]))
        rows.append(
            ("waktu ke merge", f"{flow.lead_median_hours / 24:.1f} hari", v2["lead"])
        )
        if flow.pickup_median_hours is not None:
            rows.append((
                "tunggu reviewer", f"{flow.pickup_median_hours:.1f} jam",
                v2.get("pickup", "baik"),
            ))

    out = []
    for label, value, verdict in rows:
        mark = {"baik": "·", "waspada": "!", "perhatian": "!!"}[verdict]
        out.append(f"  {mark:<2} {label:<18} {value:>10}   {verdict}")
    if flow is not None and not flow.ok:
        out.append(f"     (antrian review: {flow.reason})")
    return "\n".join(out)


def _check_gates(args, a, flow=None) -> list[str]:
    failures = []
    if args.max_churn is not None and a.churn_rate > args.max_churn:
        failures.append(
            f"ditulis ulang {a.churn_rate:.1f}% melewati ambang {args.max_churn:.1f}%"
        )
    if args.max_copypaste is not None and a.copypaste_ratio > args.max_copypaste:
        failures.append(
            f"salin-tempel {a.copypaste_ratio:.1f}% melewati ambang {args.max_copypaste:.1f}%"
        )
    if (args.max_duplication is not None
            and a.head_dup_rate_nontest > args.max_duplication):
        failures.append(
            f"kode kembar {a.head_dup_rate_nontest:.1f}% melewati ambang "
            f"{args.max_duplication:.1f}%"
        )
    if args.max_area_score is not None:
        from .scoring import score_area

        worst = max(
            (s.total for s in (score_area(ar) for ar in a.areas) if s), default=0.0
        )
        if worst > args.max_area_score:
            failures.append(
                f"skor area tertinggi {worst:.1f} melewati ambang {args.max_area_score:.1f}"
            )
    if args.max_stale_prs is not None and flow is not None and flow.ok:
        if flow.stale_count > args.max_stale_prs:
            failures.append(
                f"{flow.stale_count} PR menunggu lebih dari 14 hari, "
                f"melewati ambang {args.max_stale_prs}"
            )
    return failures


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(args.repo).expanduser().resolve()

    if not gitio.is_git_repo(repo):
        print(f"{BANNER}: {repo} bukan repositori git.", file=sys.stderr)
        return 2

    workers = args.workers or _default_workers()

    analyzer = Analyzer(
        repo=repo,
        window_days=args.days,
        churn_days=args.churn_days,
        branch=args.branch,
        max_commits=args.max_commits,
        ai_patterns=args.ai_pattern,
        skip_head_scan=args.no_head_scan,
        workers=workers,
        progress=_progress(args.quiet),
    )

    try:
        analysis = analyzer.run()
    except gitio.GitError as e:
        print(f"{BANNER}: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ndibatalkan.", file=sys.stderr)
        return 130

    if analysis.commits_scanned == 0:
        print(
            f"{BANNER}: tidak ada commit dalam {args.days} hari terakhir. "
            "Coba naikkan --days.",
            file=sys.stderr,
        )
        return 2

    flow = None
    if not args.no_github:
        if not args.quiet:
            sys.stderr.write("  mengambil data antrian review...\r")
            sys.stderr.flush()
        flow = github.fetch(
            repo,
            window_days=args.days,
            token=args.token,
            slug=args.slug,
            pickup_sample=args.pickup_sample,
        )
        if not args.quiet:
            sys.stderr.write(" " * 40 + "\r")

    history = None
    deltas: list = []
    base = None
    if not args.no_history:
        hpath = Path(args.history).expanduser() if args.history else repo / DEFAULT_PATH
        try:
            history = History(hpath)
            history.record(analysis, flow)
            deltas, base = history.deltas()
            history.save()
        except OSError as e:
            analysis.warnings.append(f"Riwayat tidak bisa disimpan: {e}")
            history = None

    if not args.no_html:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            report.render(analysis, flow=flow, deltas=deltas, base=base, history=history),
            encoding="utf-8",
        )

    if args.json_out:
        jp = Path(args.json_out).expanduser()
        jp.parent.mkdir(parents=True, exist_ok=True)
        jp.write_text(
            report.to_json(analysis, flow=flow, deltas=deltas, base=base),
            encoding="utf-8",
        )

    if not args.quiet:
        print(f"\n{analysis.repo_name} · {analysis.branch} · {args.days} hari · "
              f"{analysis.commits_scanned} commit\n")
        print(_summary(analysis, flow))
        print()
        for line in _trend_lines(deltas, base):
            print(line)
        if deltas:
            print()
        if analysis.attribution_note:
            print(f"  {analysis.attribution_note}")
        if not args.no_html:
            print(f"  laporan: {Path(args.out).expanduser().resolve()}")
        if args.json_out:
            print(f"  json:    {Path(args.json_out).expanduser().resolve()}")
        for w in analysis.warnings:
            print(f"  catatan: {w}")

    failures = _check_gates(args, analysis, flow)
    if failures:
        print("\n" + f"{BANNER}: gerbang tidak terlewati", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
