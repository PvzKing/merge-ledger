"""Membuat laporan HTML satu file, tanpa jaringan, tanpa dependensi.

Laporan ini sering dibuka dari laptop orang lain, dilampirkan di email, atau
disimpan sebagai artefak CI. Jadi semuanya inline: tidak ada CDN, tidak ada
font eksternal, tidak ada permintaan jaringan sama sekali.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone

from . import history as hist
from .metrics import Analysis
from .scoring import overall_verdicts, score_area

CSS = """
:root{
  --paper:#EFF1EE; --card:#FBFCFA; --ink:#131A1E; --muted:#5C666B;
  --rule:#D5DAD6; --rule-soft:#E4E8E4;
  --steel:#2C5D74; --pine:#2C6E55; --ochre:#9C6A17; --oxide:#93312A;
  --fill-soft:#DFE4E0;
  --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
  --sans:ui-sans-serif,system-ui,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}
@media (prefers-color-scheme:dark){
  :root{
    --paper:#11161A; --card:#171E22; --ink:#E3E8E5; --muted:#93A0A5;
    --rule:#2B3439; --rule-soft:#222A2E;
    --steel:#77AAC6; --pine:#66B392; --ochre:#D5A653; --oxide:#DB7B71;
    --fill-soft:#252E33;
  }
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font-family:var(--sans); font-size:15px; line-height:1.6;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1080px; margin:0 auto; padding:48px 28px 96px}
.mono{font-family:var(--mono); font-variant-numeric:tabular-nums}
.eyebrow{
  font-family:var(--mono); font-size:11px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--muted);
}
h1{font-family:var(--mono); font-size:26px; font-weight:600; margin:6px 0 4px; letter-spacing:-.01em}
h2{font-size:19px; font-weight:600; margin:0 0 4px}
h3{font-size:15px; font-weight:600; margin:0 0 6px}
p{margin:0 0 12px}
a{color:var(--steel)}
.masthead{border-bottom:2px solid var(--ink); padding-bottom:18px; margin-bottom:8px}
.meta{font-family:var(--mono); font-size:12px; color:var(--muted); margin-top:10px}
.meta span{margin-right:18px; white-space:nowrap; display:inline-block}
section{margin-top:44px}
.sec-head{display:flex; align-items:baseline; gap:12px; border-bottom:1px solid var(--rule); padding-bottom:8px; margin-bottom:18px}
.sec-head .num{font-family:var(--mono); font-size:12px; color:var(--muted); margin-right:12px}
.lede{color:var(--muted); font-size:14px; margin:0}

.grid4{display:grid; grid-template-columns:repeat(4,1fr); gap:1px; background:var(--rule); border:1px solid var(--rule)}
.cell{background:var(--card); padding:18px 16px 16px}
.cell .k{font-family:var(--mono); font-size:10.5px; letter-spacing:.12em; text-transform:uppercase; color:var(--muted)}
.figure{font-family:var(--mono); font-size:34px; font-weight:600; letter-spacing:-.02em; margin:10px 0 2px; font-variant-numeric:tabular-nums}
.figure .u{font-size:15px; font-weight:400; color:var(--muted); margin-left:2px}
.cell .note{font-size:12.5px; color:var(--muted); line-height:1.45; margin-top:8px}
.chip{
  display:inline-block; font-family:var(--mono); font-size:10px; letter-spacing:.1em;
  text-transform:uppercase; padding:2px 7px; border:1px solid currentColor; border-radius:2px;
}
.baik{color:var(--pine)} .waspada{color:var(--ochre)} .perhatian{color:var(--oxide)}
.bar-baik{background:var(--pine)} .bar-waspada{background:var(--ochre)} .bar-perhatian{background:var(--oxide)}

table{width:100%; border-collapse:collapse; font-size:13.5px}
th{
  font-family:var(--mono); font-size:10.5px; letter-spacing:.1em; text-transform:uppercase;
  color:var(--muted); text-align:left; font-weight:400; padding:0 10px 8px 0;
  border-bottom:1px solid var(--rule); white-space:nowrap;
}
td{padding:10px 10px 10px 0; border-bottom:1px solid var(--rule-soft); vertical-align:top}
td.n, th.n{text-align:right; font-family:var(--mono); font-variant-numeric:tabular-nums; padding-left:18px; white-space:nowrap}
th:last-child, td:last-child{padding-right:0}
td.n .chip{margin-left:8px}
tr:last-child td{border-bottom:none}
.path{font-family:var(--mono); font-size:12.5px; word-break:break-all}
.sub{color:var(--muted); font-size:12px}

.track{height:6px; display:block; background:var(--fill-soft); position:relative; margin-top:6px; min-width:70px}
.track i{position:absolute; left:0; top:0; bottom:0; display:block}

.why{display:flex; flex-wrap:wrap; gap:6px; margin-top:7px}
.why b{ margin:0 6px 6px 0;
  font-family:var(--mono); font-size:10.5px; font-weight:400; letter-spacing:.04em;
  color:var(--muted); border:1px solid var(--rule); padding:1px 6px; white-space:nowrap;
}
.why b.hot{color:var(--oxide); border-color:currentColor}

.chart{width:100%; height:auto; display:block; margin:6px 0 0}
.legend span{margin-right:18px}
.legend{display:flex; gap:18px; font-family:var(--mono); font-size:11px; color:var(--muted); margin-top:10px}
.legend i{display:inline-block; width:10px; height:10px; margin-right:6px; vertical-align:-1px}

.cols{display:grid; grid-template-columns:1fr 1fr; gap:28px}
.panel{border:1px solid var(--rule); background:var(--card); padding:18px}
.kv{display:flex; justify-content:space-between; gap:16px; column-gap:16px; padding:7px 0; border-bottom:1px solid var(--rule-soft); font-size:13.5px}
.kv:last-child{border-bottom:none}
.kv span:last-child{font-family:var(--mono); font-variant-numeric:tabular-nums}
.callout{border-left:2px solid var(--ochre); padding:2px 0 2px 14px; color:var(--muted); font-size:13.5px; margin:16px 0 0}
.delta{font-family:var(--mono); font-size:11px; margin-top:6px; display:block}
.membaik{color:var(--pine)} .memburuk{color:var(--oxide)} .tetap{color:var(--muted)}
.spark{display:block; margin-top:4px}
.flow-grid{display:grid; grid-template-columns:repeat(3,1fr); gap:1px; background:var(--rule); border:1px solid var(--rule)}
.flow-grid .cell .figure{font-size:26px}
.skipped{border:1px dashed var(--rule); padding:14px 16px; color:var(--muted); font-size:13.5px}
.method dt{font-family:var(--mono); font-size:12.5px; margin-top:14px; color:var(--ink)}
.method dd{margin:3px 0 0; color:var(--muted); font-size:13.5px}
footer{margin-top:56px; padding-top:16px; border-top:1px solid var(--rule); font-size:12px; color:var(--muted)}
@media (max-width:820px){
  .grid4{grid-template-columns:repeat(2,1fr)}
  .cols{grid-template-columns:1fr}
  .flow-grid{grid-template-columns:1fr}
  .wrap{padding:32px 18px 64px}
  .figure{font-size:28px}
}
@media print{
  body{background:#fff} .wrap{max-width:none}
  section{break-inside:avoid}
}
"""


BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
         "Jul", "Ags", "Sep", "Okt", "Nov", "Des"]


def tanggal(ts: int, with_time: bool = False) -> str:
    d = datetime.fromtimestamp(ts, timezone.utc)
    s = f"{d.day} {BULAN[d.month - 1]}"
    if with_time:
        s += f" {d.year} {d:%H:%M} UTC"
    return s


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def fmt(n, digits: int = 1) -> str:
    if n == float("inf"):
        return "∞"
    if isinstance(n, float):
        return f"{n:,.{digits}f}".replace(",", ".")
    return f"{n:,}".replace(",", ".")


def _chip(verdict: str) -> str:
    return f'<span class="chip {verdict}">{verdict}</span>'


def _track(pct: float, verdict: str) -> str:
    w = max(1.5, min(100.0, pct))
    return f'<span class="track"><i class="bar-{verdict}" style="width:{w:.1f}%"></i></span>'


def _delta_note(deltas: dict, key: str) -> str:
    d = deltas.get(key)
    if d is None:
        return ""
    if not d.significant:
        return (
            f'<span class="delta tetap">tetap dibanding {fmt(d.span_days, 0)} '
            "hari lalu</span>"
        )
    panah = "naik" if d.direction == "naik" else "turun"
    return (
        f'<span class="delta {d.meaning}">{panah} dari {fmt(d.before, 1)} '
        f"({fmt(abs(d.pct_change), 0)}%) dalam {fmt(d.span_days, 0)} hari — "
        f"{d.meaning}</span>"
    )


def _headline_cells(a: Analysis, deltas: dict | None = None) -> str:
    deltas = deltas or {}
    v = overall_verdicts(a)
    cells = [
        (
            "Ditulis ulang cepat",
            fmt(a.churn_rate),
            "%",
            v["churn"],
            f"{fmt(a.churned_lines, 0)} dari {fmt(a.lines_added, 0)} baris baru sudah "
            f"diubah lagi dalam {a.churn_days} hari sejak ditulis.",
        ),
        (
            "Salin vs pindah",
            fmt(a.copy_vs_move, 2) if a.copy_vs_move != float("inf") else "∞",
            "×",
            "perhatian" if a.copy_vs_move > 1 else "baik",
            f"{fmt(a.copypaste_lines, 0)} baris menyalin blok lain, "
            f"{fmt(a.moved_lines, 0)} baris dipindah untuk konsolidasi. "
            "Di atas 1× artinya lebih banyak menyalin daripada merapikan.",
        ),
        (
            "Kode kembar (tanpa tes)",
            fmt(a.head_dup_rate_nontest),
            "%",
            v["duplication"],
            f"{fmt(a.head_dup_lines_nontest, 0)} dari {fmt(a.head_lines_nontest, 0)} "
            f"baris bukan-tes berada di dalam blok yang berulang. "
            f"Termasuk berkas tes angkanya {fmt(a.head_dup_rate)}%.",
        ),
        (
            "Penyamaran error",
            fmt(a.masking_per_kloc, 2),
            "/kloc",
            v["masking"],
            f"{fmt(a.masking_added, 0)} konstruksi penelan error ditambahkan "
            "(except kosong, catch kosong, err diabaikan).",
        ),
    ]
    keys = ["churn_rate", "copy_vs_move", "head_duplication_rate_nontest",
            "masking_per_kloc"]
    out = []
    for (label, value, unit, verdict, note), key in zip(cells, keys):
        out.append(
            f'<div class="cell"><div class="k">{esc(label)}</div>'
            f'<div class="figure">{esc(value)}<span class="u">{esc(unit)}</span></div>'
            f"{_chip(verdict)}{_delta_note(deltas, key)}"
            f'<div class="note">{esc(note)}</div></div>'
        )
    return '<div class="grid4">' + "".join(out) + "</div>"


def _weekly_chart(a: Analysis) -> str:
    weeks = a.weeks
    if not weeks:
        return '<p class="lede">Belum ada data mingguan.</p>'

    W, H = 940, 230
    pad_l, pad_r, pad_t, pad_b = 46, 10, 14, 34
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b
    peak = max((w.added for w in weeks), default=1) or 1

    step = plot_w / len(weeks)
    bw = min(46.0, step * 0.62)

    bars = []
    labels = []
    for i, w in enumerate(weeks):
        x = pad_l + step * i + (step - bw) / 2
        h_add = plot_h * (w.added / peak)
        h_chu = plot_h * (min(w.churned, w.added) / peak)
        y_add = pad_t + plot_h - h_add
        y_chu = pad_t + plot_h - h_chu
        bars.append(
            f'<rect x="{x:.1f}" y="{y_add:.1f}" width="{bw:.1f}" height="{h_add:.1f}" '
            f'fill="var(--fill-soft)"/>'
        )
        if h_chu > 0.4:
            bars.append(
                f'<rect x="{x:.1f}" y="{y_chu:.1f}" width="{bw:.1f}" height="{h_chu:.1f}" '
                f'fill="var(--oxide)" opacity="0.85"/>'
            )
        every = 1 if len(weeks) <= 14 else (2 if len(weeks) <= 30 else 4)
        if i % every == 0:
            wk = tanggal(w.start_ts) if w.start_ts else w.label.split("-")[-1]
            labels.append(
                f'<text x="{x + bw / 2:.1f}" y="{H - 12}" text-anchor="middle" '
                f'font-family="var(--mono)" font-size="10" fill="var(--muted)">{esc(wk)}</text>'
            )

    grid = []
    for frac in (0, 0.5, 1.0):
        y = pad_t + plot_h * (1 - frac)
        grid.append(
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{W - pad_r}" y2="{y:.1f}" '
            f'stroke="var(--rule)" stroke-width="1"/>'
        )
        grid.append(
            f'<text x="{pad_l - 8}" y="{y + 3.5:.1f}" text-anchor="end" '
            f'font-family="var(--mono)" font-size="10" fill="var(--muted)">'
            f"{fmt(int(peak * frac), 0)}</text>"
        )

    return (
        f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" '
        f'aria-label="Baris ditulis per minggu dengan porsi yang ditulis ulang">'
        + "".join(grid) + "".join(bars) + "".join(labels) + "</svg>"
        '<div class="legend">'
        '<span><i style="background:var(--fill-soft)"></i>Baris ditulis</span>'
        '<span><i style="background:var(--oxide)"></i>Yang kemudian ditulis ulang cepat</span>'
        "</div>"
    )


def _area_table(a: Analysis) -> str:
    scored = []
    for area in a.areas:
        s = score_area(area)
        if s:
            scored.append((area, s))
    if not scored:
        return (
            '<p class="lede">Tidak ada area dengan aktivitas cukup untuk dinilai '
            "(minimal 200 baris baru dalam rentang ini).</p>"
        )
    scored.sort(key=lambda t: t[1].total, reverse=True)

    rows = []
    for area, s in scored[:14]:
        why = []
        for c in sorted(s.components, key=lambda c: c.points, reverse=True):
            hot = " hot" if c.verdict == "perhatian" else ""
            val = fmt(c.value, 2 if c.unit == "×" else 1)
            why.append(f'<b class="{hot.strip()}">{esc(c.label)} {val}{esc(c.unit)}</b>')
        rows.append(
            "<tr>"
            f'<td><span class="path">{esc(area.name)}</span>'
            f'<div class="why">{"".join(why)}</div></td>'
            f'<td class="n">{fmt(area.files, 0)}</td>'
            f'<td class="n">{fmt(area.added, 0)}</td>'
            f'<td class="n">{fmt(area.churn_rate)}%</td>'
            f'<td class="n">{fmt(s.total)}{_chip(s.band)}'
            f"{_track(s.total, s.band)}</td>"
            "</tr>"
        )

    return (
        "<table><thead><tr>"
        "<th>Area</th><th class='n'>File</th><th class='n'>Baris baru</th>"
        "<th class='n'>Ditulis ulang</th><th class='n'>Skor perhatian</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _files_table(a: Analysis, limit: int = 12) -> str:
    hot = [f for f in a.files if f.touches >= 2 and f.added >= 40][:limit]
    if not hot:
        return '<p class="lede">Tidak ada file yang menonjol dalam rentang ini.</p>'
    rows = []
    for f in hot:
        when = tanggal(f.last_touch)
        rows.append(
            "<tr>"
            f'<td><span class="path">{esc(f.path)}</span></td>'
            f'<td class="n">{fmt(f.touches, 0)}</td>'
            f'<td class="n">{fmt(len(f.authors), 0)}</td>'
            f'<td class="n">{fmt(f.added, 0)}</td>'
            f'<td class="n">{fmt(f.churn_rate)}%</td>'
            f'<td class="n sub">{esc(when)}</td>'
            "</tr>"
        )
    return (
        "<table><thead><tr><th>File</th><th class='n'>Disentuh</th>"
        "<th class='n'>Penulis</th><th class='n'>Baris baru</th>"
        "<th class='n'>Ditulis ulang</th><th class='n'>Terakhir</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _attribution_panel(a: Analysis) -> str:
    if a.commits_ai == 0:
        return (
            '<div class="panel"><h3>AI vs manusia</h3>'
            f'<p class="lede">{esc(a.attribution_note)}</p>'
            '<p class="callout">Sampai penandaan itu ada, semua angka di laporan ini '
            "berlaku untuk seluruh kode tanpa pembedaan. Cara termurah memulainya: "
            "wajibkan trailer <code>Co-Authored-By</code> pada commit yang dibantu agen, "
            "atau label PR yang konsisten.</p></div>"
        )
    return (
        '<div class="panel"><h3>AI vs manusia</h3>'
        f'<div class="kv"><span>Commit dengan penanda AI</span>'
        f"<span>{fmt(a.commits_ai, 0)} / {fmt(a.commits_scanned, 0)}</span></div>"
        f'<div class="kv"><span>Baris ditulis oleh commit AI</span>'
        f"<span>{fmt(a.added_ai, 0)}</span></div>"
        f'<div class="kv"><span>Ditulis ulang — asal AI</span>'
        f"<span>{fmt(a.churn_rate_ai)}%</span></div>"
        f'<div class="kv"><span>Ditulis ulang — asal manusia</span>'
        f"<span>{fmt(a.churn_rate_human)}%</span></div>"
        f'<div class="kv"><span>Selisih</span><span>'
        f"{fmt(a.churn_rate_ai - a.churn_rate_human)} poin</span></div>"
        '<p class="callout">Bandingkan selisihnya, bukan angka mutlaknya. '
        "Rasio yang sehat menurut acuan industri 2026: kode AI tidak lebih dari "
        "1,5× kode manusia.</p></div>"
    )


def _volume_panel(a: Analysis) -> str:
    return (
        '<div class="panel"><h3>Volume</h3>'
        f'<div class="kv"><span>Commit dipindai</span><span>{fmt(a.commits_scanned, 0)}</span></div>'
        f'<div class="kv"><span>Baris ditambah</span><span>{fmt(a.lines_added, 0)}</span></div>'
        f'<div class="kv"><span>Baris dihapus</span><span>{fmt(a.lines_deleted, 0)}</span></div>'
        f'<div class="kv"><span>Baris dipindah antar file</span><span>{fmt(a.moved_lines, 0)}</span></div>'
        f'<div class="kv"><span>Porsi konsolidasi</span><span>{fmt(a.refactor_ratio)}%</span></div>'
        f'<div class="kv"><span>Porsi baris tes</span><span>{fmt(a.test_ratio)}%</span></div>'
        f'<div class="kv"><span>Lama pemindaian</span><span>{fmt(a.scan_seconds)} dtk</span></div>'
        "</div>"
    )


def _masking_panel(a: Analysis) -> str:
    if not a.masking_breakdown and not a.suppression_breakdown:
        return ""
    parts = []
    if a.masking_breakdown:
        rows = "".join(
            f'<div class="kv"><span>{esc(label)}</span><span>{fmt(n, 0)}</span></div>'
            for label, n in a.masking_breakdown.most_common(8)
        )
        parts.append(
            f'<div class="panel"><h3>Penelan error saat berjalan</h3>{rows}'
            '<p class="callout">Kegagalan yang tidak pernah sampai ke log atau '
            "pemanggil. Ini yang membuat bug jadi sulit dilacak berbulan-bulan "
            "kemudian.</p></div>"
        )
    if a.suppression_breakdown:
        rows = "".join(
            f'<div class="kv"><span>{esc(label)}</span><span>{fmt(n, 0)}</span></div>'
            for label, n in a.suppression_breakdown.most_common(8)
        )
        parts.append(
            f'<div class="panel"><h3>Penekan alat statis</h3>{rows}'
            f'<div class="kv"><span>Per 1000 baris</span>'
            f"<span>{fmt(a.suppression_per_kloc, 2)}</span></div>"
            '<p class="callout">Dihitung terpisah dari penelan error. Ini bukan '
            "kesalahan, tapi tiap satu berarti ada peringatan alat yang dimatikan "
            "alih-alih dijawab.</p></div>"
        )
    return '<div class="cols">' + "".join(parts) + "</div>"


def _sparkline(points: list[tuple[int, float]], width: int = 260, height: int = 34) -> str:
    if len(points) < 3:
        return ""
    vals = [v for _, v in points]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    step = width / (len(vals) - 1)
    coords = " ".join(
        f"{i * step:.1f},{height - 2 - (height - 6) * (v - lo) / span:.1f}"
        for i, v in enumerate(vals)
    )
    last_x = width
    last_y = height - 2 - (height - 6) * (vals[-1] - lo) / span
    return (
        f'<svg class="spark" viewBox="0 0 {width + 4} {height}" width="{width + 4}" '
        f'height="{height}" role="img" aria-label="tren {len(vals)} pemindaian terakhir">'
        f'<polyline points="{coords}" fill="none" stroke="var(--steel)" '
        f'stroke-width="1.5" stroke-linejoin="round"/>'
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="2.5" fill="var(--steel)"/></svg>'
    )


def _trend_section(deltas: dict, base: dict | None, history) -> str:
    if not deltas or not base:
        return ""
    when = tanggal(base["at"])
    rows = []
    for key in ["churn_rate", "copy_vs_move", "head_duplication_rate_nontest",
                "refactor_ratio", "masking_per_kloc", "review_queue_depth",
                "pickup_median_hours"]:
        d = deltas.get(key)
        if d is None:
            continue
        arah = "tetap" if not d.significant else d.direction
        kelas = "tetap" if not d.significant else d.meaning
        unit = hist.unit_for(key)
        rows.append(
            "<tr>"
            f"<td>{esc(hist.label_for(key))}</td>"
            f'<td class="n">{fmt(d.before, 2 if unit == "×" else 1)}{esc(unit)}</td>'
            f'<td class="n">{fmt(d.now, 2 if unit == "×" else 1)}{esc(unit)}</td>'
            f'<td class="n {kelas}">{esc(arah)}</td>'
            f'<td class="n {kelas}">{esc(kelas)}</td>'
            "</tr>"
        )
    if not rows:
        return ""

    spark = ""
    if history is not None:
        pts = history.series("churn_rate")
        if len(pts) >= 3:
            spark = (
                '<p class="lede" style="margin-top:18px">Ditulis ulang, '
                f"{len(pts)} pemindaian terakhir:</p>{_sparkline(pts)}"
            )

    return f"""
<section>
  <div class="sec-head"><span class="num">0X</span><h2>Arah</h2></div>
  <p class="lede">Dibandingkan dengan pemindaian {esc(when)}, pada pengaturan
  yang sama. Perubahan di bawah 5% dianggap derau dan ditandai tetap.</p>
  <table><thead><tr><th>Ukuran</th><th class="n">Sebelumnya</th>
  <th class="n">Sekarang</th><th class="n">Arah</th><th class="n">Artinya</th>
  </tr></thead><tbody>{"".join(rows)}</tbody></table>
  {spark}
</section>"""


def _flow_section(flow) -> str:
    if flow is None:
        return ""
    if not flow.ok:
        return f"""
<section>
  <div class="sec-head"><span class="num">0Y</span><h2>Antrian review</h2></div>
  <div class="skipped">Bagian ini dilewati: {esc(flow.reason)}</div>
</section>"""

    from .github import verdict as flow_verdict

    v = flow_verdict(flow)
    cells = [
        ("Menunggu direview", fmt(flow.open_count, 0), "PR", v["queue"],
         f"{fmt(flow.stale_count, 0)} di antaranya sudah terbuka lebih dari 14 hari. "
         f"Yang tertua {fmt(flow.oldest_age_days, 0)} hari."),
        ("Waktu sampai merge", fmt(flow.lead_median_hours / 24), "hari", v["lead"],
         f"Nilai tengah dari {fmt(flow.merged_count, 0)} PR. Sepersepuluh terlambat "
         f"butuh {fmt(flow.lead_p90_hours / 24)} hari atau lebih."),
    ]
    if flow.pickup_median_hours is not None:
        cells.append(
            ("Tunggu reviewer pertama", fmt(flow.pickup_median_hours), "jam",
             v.get("pickup", "baik"),
             f"Dari {fmt(flow.pickup_sampled, 0)} PR. "
             f"{fmt(flow.never_reviewed, 0)} PR di-merge tanpa satu pun review.")
        )
    else:
        cells.append(
            ("Tingkat penerimaan", fmt(flow.acceptance_rate, 0), "%", "baik",
             f"{fmt(flow.merged_count, 0)} PR masuk, "
             f"{fmt(flow.abandoned_count, 0)} ditutup tanpa merge.")
        )

    grid = "".join(
        f'<div class="cell"><div class="k">{esc(l)}</div>'
        f'<div class="figure">{esc(val)}<span class="u">{esc(u)}</span></div>'
        f'{_chip(vd)}<div class="note">{esc(n)}</div></div>'
        for l, val, u, vd, n in cells
    )

    banding = ""
    if flow.ai_lead_median_hours and flow.human_lead_median_hours:
        selisih = flow.ai_lead_median_hours / flow.human_lead_median_hours
        banding = (
            '<div class="panel" style="margin-top:24px"><h3>PR bertanda agen</h3>'
            f'<div class="kv"><span>Jumlah</span><span>{fmt(flow.ai_pr_count, 0)} '
            f"dari {fmt(flow.merged_count, 0)}</span></div>"
            f'<div class="kv"><span>Waktu sampai merge — agen</span>'
            f"<span>{fmt(flow.ai_lead_median_hours)} jam</span></div>"
            f'<div class="kv"><span>Waktu sampai merge — manusia</span>'
            f"<span>{fmt(flow.human_lead_median_hours)} jam</span></div>"
            f'<div class="kv"><span>Perbandingan</span><span>{fmt(selisih, 2)}×</span></div>'
            '<p class="callout">Acuan 2026: PR agen rata-rata menunggu sekitar 5× '
            "lebih lama. Kalau angka di atas jauh di atas 1×, sumbatannya ada di "
            "konteks — reviewer harus menyusun ulang maksud PR dari nol.</p></div>"
        )

    catatan = ""
    if flow.warnings:
        catatan = '<p class="lede" style="margin-top:16px">' + " ".join(
            esc(w) for w in flow.warnings) + "</p>"

    return f"""
<section>
  <div class="sec-head"><span class="num">0Y</span><h2>Antrian review</h2></div>
  <p class="lede">Dari GitHub, {esc(flow.slug)}. Bagian ini menjawab pertanyaan
  yang tidak bisa dijawab riwayat git: berapa lama kode menunggu sebelum masuk.</p>
  <div class="flow-grid">{grid}</div>
  {banding}
  {catatan}
</section>"""


METHOD = [
    (
        "Arah perubahan",
        "Tiap pemindaian disimpan ke .merge-ledger/history.json. Perbandingan "
        "hanya dilakukan antar pemindaian dengan cabang, jendela waktu, dan "
        "ambang churn yang sama — kalau tidak, 'perbaikan' bisa muncul hanya "
        "karena pengaturannya diubah. Perubahan di bawah 5% dianggap derau.",
    ),
    (
        "Antrian review",
        "Diambil dari GitHub, bersifat pilihan. Waktu tunggu sampai reviewer "
        "pertama membutuhkan GITHUB_TOKEN karena perlu satu panggilan API per "
        "PR; tanpa token, yang tersedia adalah kedalaman antrian dan waktu "
        "sampai merge. Kalau GitHub tidak terjangkau, laporan tetap terbit "
        "tanpa bagian ini.",
    ),
    (
        "Ditulis ulang cepat (churn)",
        "Untuk tiap baris yang dihapus atau diubah, alat ini menanyakan umur baris "
        "itu lewat git blame. Kalau umurnya di bawah ambang (bawaan 14 hari), baris "
        "itu dihitung sebagai kode yang belum matang saat di-merge. Penyebutnya "
        "adalah seluruh baris baru dalam rentang waktu.",
    ),
    (
        "Salin-tempel dan pemindahan",
        "Dalam satu commit, blok lima baris atau lebih yang muncul lebih dari sekali "
        "di antara baris baru dihitung sebagai salinan. Baris yang hilang dari satu "
        "file dan muncul di file lain dihitung sebagai pemindahan — tanda "
        "konsolidasi. Rasio keduanya lebih informatif daripada angka mutlaknya.",
    ),
    (
        "Kode kembar di HEAD",
        "Berbeda dari dua di atas yang melihat perubahan, ini memindai kondisi kode "
        "saat ini: berapa persen baris berada di dalam blok lima baris yang muncul "
        "lebih dari sekali di seluruh repositori. Angka utama tidak menyertakan "
        "berkas tes, karena pengulangan di tes sering disengaja dan tidak berbahaya; "
        "angka gabungannya tetap ditampilkan sebagai pembanding.",
    ),
    (
        "Penyamaran error",
        "Pola yang menelan kegagalan tanpa menanganinya: except telanjang, blok "
        "tangkap kosong, error yang dibuang ke variabel kosong. Deteksinya sadar "
        "konteks — `pass` yang berdiri sendiri tidak dihitung, hanya `pass` tepat "
        "setelah blok penangkap. Penekan alat statis seperti noqa dan ts-ignore "
        "dihitung terpisah karena sifatnya berbeda.",
    ),
    (
        "Yang tidak dihitung",
        "Kode pihak ketiga, hasil build, file terkunci, migrasi, dan berkas yang "
        "dihasilkan mesin dikeluarkan dari semua perhitungan. File tes tetap "
        "dihitung, tapi porsinya dilaporkan terpisah.",
    ),
    (
        "Batas alat ini",
        "Alat ini membaca riwayat git, bukan maksud penulisnya. Churn tinggi bisa "
        "berarti kode buru-buru, tapi bisa juga berarti tim sedang bereksperimen "
        "dengan sehat di area baru. Angka di sini adalah bahan percakapan, bukan "
        "penilaian kinerja — dan sengaja tidak pernah dipecah per individu.",
    ),
]


def _renumber(page: str) -> str:
    """Nomori ulang bagian sesuai urutan tampil.

    Sebagian bagian bersifat pilihan (arah, antrian review), jadi nomornya
    tidak bisa ditulis mati di sumber.
    """
    counter = iter(range(1, 99))
    return re.sub(
        r'<span class="num">[^<]*</span>',
        lambda _: f'<span class="num">{next(counter):02d}</span>',
        page,
    )


def render(a: Analysis, flow=None, deltas=None, base=None, history=None) -> str:
    gen = tanggal(a.generated_at, with_time=True)
    dmap = {d.key: d for d in (deltas or [])}
    trend = _trend_section(dmap, base, history)
    flow_html = _flow_section(flow)
    warn = ""
    if a.warnings:
        warn = "<footer>" + "<br>".join(esc(w) for w in a.warnings) + "</footer>"

    method = "".join(
        f"<dt>{esc(t)}</dt><dd>{esc(d)}</dd>" for t, d in METHOD
    )

    page = f"""<!DOCTYPE html>
<html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Merge Ledger — {esc(a.repo_name)}</title>
<style>{CSS}</style></head>
<body><div class="wrap">

<header class="masthead">
  <div class="eyebrow">Merge Ledger — pemindaian dasar</div>
  <h1>{esc(a.repo_name)}</h1>
  <div class="meta">
    <span>cabang {esc(a.branch)}</span>
    <span>{a.window_days} hari terakhir</span>
    <span>{fmt(a.commits_scanned, 0)} commit</span>
    <span>ambang churn {a.churn_days} hari</span>
    <span>dibuat {esc(gen)}</span>
  </div>
</header>

<section>
  <div class="sec-head"><span class="num">01</span><h2>Empat angka</h2></div>
  {_headline_cells(a, dmap)}
</section>
{trend}
{flow_html}

<section>
  <div class="sec-head"><span class="num">02</span><h2>Yang ditulis, dan yang tidak bertahan</h2></div>
  <p class="lede">Batang terang adalah baris yang ditulis minggu itu. Bagian gelap
  adalah porsi yang sudah ditulis ulang dalam {a.churn_days} hari. Batang gelap
  yang membesar berarti kode masuk lebih cepat daripada ia dipahami.</p>
  {_weekly_chart(a)}
</section>

<section>
  <div class="sec-head"><span class="num">0A</span><h2>Area yang perlu dilihat</h2></div>
  <p class="lede">Skor adalah gabungan empat komponen dengan bobot tetap. Rincian
  di bawah tiap nama area menunjukkan komponen mana yang menyumbang paling besar,
  urut dari yang terbesar.</p>
  {_area_table(a)}
</section>

<section>
  <div class="sec-head"><span class="num">0B</span><h2>File yang berulang kali disentuh</h2></div>
  <p class="lede">File yang sering kembali ke meja kerja. Ini kandidat pertama
  untuk tes karakterisasi sebelum disentuh lagi.</p>
  {_files_table(a)}
</section>

<section>
  <div class="sec-head"><span class="num">0C</span><h2>Rincian</h2></div>
  <div class="cols">
    {_attribution_panel(a)}
    {_volume_panel(a)}
  </div>
  <div style="margin-top:28px">{_masking_panel(a)}</div>
</section>

<section class="method">
  <div class="sec-head"><span class="num">0D</span><h2>Cara angka ini dihitung</h2></div>
  <dl>{method}</dl>
</section>

{warn}
</div></body></html>"""
    return _renumber(page)


def to_json(a: Analysis, flow=None, deltas=None, base=None) -> str:
    v = overall_verdicts(a)
    payload = {
        "repo": a.repo_name,
        "branch": a.branch,
        "generated_at": a.generated_at,
        "window_days": a.window_days,
        "churn_days": a.churn_days,
        "commits": {
            "scanned": a.commits_scanned,
            "total_in_window": a.commits_total,
            "ai_marked": a.commits_ai,
            "truncated": a.truncated,
        },
        "lines": {
            "added": a.lines_added,
            "deleted": a.lines_deleted,
            "churned": a.churned_lines,
            "moved": a.moved_lines,
            "copypaste": a.copypaste_lines,
            "tests": a.test_added,
        },
        "counts": {
            "error_masking": a.masking_added,
            "error_masking_by_kind": dict(a.masking_breakdown),
            "static_suppression": a.suppression_added,
            "static_suppression_by_kind": dict(a.suppression_breakdown),
        },
        "metrics": {
            "churn_rate": round(a.churn_rate, 2),
            "churn_rate_ai": round(a.churn_rate_ai, 2),
            "churn_rate_human": round(a.churn_rate_human, 2),
            "copypaste_ratio": round(a.copypaste_ratio, 2),
            "copy_vs_move": (
                None if a.copy_vs_move == float("inf") else round(a.copy_vs_move, 3)
            ),
            "refactor_ratio": round(a.refactor_ratio, 2),
            "head_duplication_rate": round(a.head_dup_rate, 2),
            "head_duplication_rate_nontest": round(a.head_dup_rate_nontest, 2),
            "masking_per_kloc": round(a.masking_per_kloc, 3),
            "suppression_per_kloc": round(a.suppression_per_kloc, 3),
            "test_ratio": round(a.test_ratio, 2),
        },
        "verdicts": v,
        "areas": [
            {
                "name": ar.name,
                "files": ar.files,
                "added": ar.added,
                "churn_rate": round(ar.churn_rate, 2),
                "copypaste_rate": round(ar.copypaste_rate, 2),
                "score": s.total,
                "band": s.band,
                "components": [
                    {
                        "key": c.key,
                        "value": round(c.value, 3),
                        "points": round(c.points, 1),
                        "verdict": c.verdict,
                    }
                    for c in s.components
                ],
            }
            for ar, s in (
                (ar, score_area(ar)) for ar in a.areas
            )
            if s
        ],
        "hot_files": [
            {
                "path": f.path,
                "touches": f.touches,
                "authors": len(f.authors),
                "added": f.added,
                "churn_rate": round(f.churn_rate, 2),
            }
            for f in a.files[:25]
            if f.touches >= 2
        ],
        "weeks": [
            {"week": w.label, "added": w.added, "churned": w.churned, "commits": w.commits}
            for w in a.weeks
        ],
        "warnings": a.warnings,
    }

    if flow is not None:
        payload["review_flow"] = (
            {
                "available": True,
                "repo": flow.slug,
                "authenticated": flow.authenticated,
                "open_count": flow.open_count,
                "open_ready_count": flow.open_ready_count,
                "stale_count": flow.stale_count,
                "stale_share": round(flow.stale_share, 1),
                "oldest_age_days": round(flow.oldest_age_days, 1),
                "merged_count": flow.merged_count,
                "abandoned_count": flow.abandoned_count,
                "acceptance_rate": round(flow.acceptance_rate, 1),
                "lead_median_hours": round(flow.lead_median_hours, 2),
                "lead_p90_hours": round(flow.lead_p90_hours, 2),
                "pickup_median_hours": (
                    None if flow.pickup_median_hours is None
                    else round(flow.pickup_median_hours, 2)
                ),
                "never_reviewed": flow.never_reviewed,
                "ai_pr_count": flow.ai_pr_count,
                "ai_lead_median_hours": flow.ai_lead_median_hours,
                "human_lead_median_hours": flow.human_lead_median_hours,
                "warnings": flow.warnings,
            }
            if flow.ok
            else {"available": False, "reason": flow.reason}
        )

    if deltas:
        payload["trend"] = {
            "compared_with": base["at"] if base else None,
            "span_days": round(deltas[0].span_days, 1),
            "changes": [
                {
                    "key": d.key,
                    "before": d.before,
                    "now": d.now,
                    "pct_change": round(d.pct_change, 1),
                    "direction": d.direction,
                    "meaning": d.meaning,
                    "significant": d.significant,
                }
                for d in deltas
            ],
        }
    return json.dumps(payload, indent=2, ensure_ascii=False)
