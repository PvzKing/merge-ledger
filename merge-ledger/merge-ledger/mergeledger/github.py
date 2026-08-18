"""Mengukur antrian review dari GitHub.

Riwayat git tahu apa yang terjadi pada kode setelah masuk. Yang tidak
diketahuinya: berapa lama kode itu menunggu di depan pintu. Padahal justru di
situ letak sumbatannya menurut data 2026 — waktu tulis turun, waktu tunggu
review naik.

Modul ini bersifat pilihan dan tidak pernah boleh menggagalkan pemindaian.
Kalau tidak ada jaringan, tidak ada token, atau remote-nya bukan GitHub,
laporan tetap terbit tanpa bagian ini.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import gitio

API = "https://api.github.com"
UA = "merge-ledger/0.2"
HOUR = 3600

_REMOTE_RE = re.compile(
    r"(?:github\.com[:/])(?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?/?$"
)

AI_PR_MARKERS = re.compile(
    r"\b(claude|copilot|cursor|devin|aider|codex|\bagent\b)\b|\[bot\]", re.I
)


def detect_slug(repo: Path) -> str | None:
    """Ambil owner/repo dari remote, kalau memang GitHub."""
    for remote in ("origin", "upstream"):
        try:
            url = gitio.run_git(repo, ["remote", "get-url", remote]).strip()
        except gitio.GitError:
            continue
        m = _REMOTE_RE.search(url)
        if m:
            return f"{m['owner']}/{m['repo']}"
    return None


def _parse_time(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


@dataclass
class PullFlow:
    ok: bool = False
    reason: str = ""
    slug: str = ""
    authenticated: bool = False
    api_calls: int = 0

    open_count: int = 0
    open_ready_count: int = 0          # tanpa draft
    open_median_age_hours: float = 0.0
    open_p90_age_hours: float = 0.0
    stale_count: int = 0               # terbuka lebih dari 14 hari
    oldest_age_days: float = 0.0

    merged_count: int = 0
    lead_median_hours: float = 0.0
    lead_p90_hours: float = 0.0
    abandoned_count: int = 0           # ditutup tanpa merge

    pickup_median_hours: float | None = None
    pickup_p90_hours: float | None = None
    pickup_sampled: int = 0
    never_reviewed: int = 0            # merge tanpa satu pun review

    ai_pr_count: int = 0
    ai_lead_median_hours: float | None = None
    human_lead_median_hours: float | None = None

    warnings: list[str] = field(default_factory=list)

    @property
    def acceptance_rate(self) -> float:
        total = self.merged_count + self.abandoned_count
        return 100 * self.merged_count / total if total else 0.0

    @property
    def stale_share(self) -> float:
        return 100 * self.stale_count / self.open_count if self.open_count else 0.0


class GitHubClient:
    def __init__(self, token: str | None = None, timeout: int = 20) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.timeout = timeout
        self.calls = 0
        self.rate_left: int | None = None

    def get(self, path: str, params: dict | None = None) -> list | dict | None:
        url = f"{API}{path}"
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": UA,
            **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                self.calls += 1
                left = r.headers.get("X-RateLimit-Remaining")
                if left is not None:
                    self.rate_left = int(left)
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            self.calls += 1
            if e.code in (403, 429):
                # GitHub memakai 403 untuk batas laju maupun izin. Pembedanya
                # ada di header kuota dan isi pesan, bukan di status.
                left = e.headers.get("X-RateLimit-Remaining")
                try:
                    body = e.read().decode("utf-8", "replace")[:300]
                except Exception:
                    body = ""
                if left == "0" or "rate limit" in body.lower() or e.code == 429:
                    reset = e.headers.get("X-RateLimit-Reset")
                    raise RateLimited(_reset_hint(reset)) from e
                raise AccessDenied(e.code) from e
            if e.code == 401:
                raise AccessDenied(e.code) from e
            if e.code == 404:
                raise NotFound() from e
            return None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise Offline()


def _reset_hint(reset: str | None) -> str:
    if not reset:
        return ""
    try:
        wait = int(reset) - int(time.time())
    except ValueError:
        return ""
    if wait <= 0:
        return ""
    return f" Kuota pulih dalam {max(1, wait // 60)} menit."


class RateLimited(Exception):
    def __init__(self, hint: str = "") -> None:
        self.hint = hint


class AccessDenied(Exception):
    def __init__(self, code: int) -> None:
        self.code = code


class NotFound(Exception):
    pass


class Offline(Exception):
    pass


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _is_ai_pr(pr: dict) -> bool:
    haystack = " ".join([
        pr.get("title") or "",
        (pr.get("head") or {}).get("ref") or "",
        ((pr.get("user") or {}).get("login")) or "",
        " ".join(l.get("name", "") for l in pr.get("labels") or []),
    ])
    return bool(AI_PR_MARKERS.search(haystack))


def fetch(
    repo: Path,
    window_days: int = 90,
    token: str | None = None,
    slug: str | None = None,
    pickup_sample: int = 40,
    max_pages: int = 3,
    client: "GitHubClient | None" = None,
) -> PullFlow:
    """Ambil gambaran antrian review. Gagal dengan tenang, tidak melempar."""
    flow = PullFlow()
    flow.slug = slug or detect_slug(repo) or ""
    if not flow.slug:
        flow.reason = "remote bukan GitHub, bagian antrian review dilewati"
        return flow

    client = client or GitHubClient(token=token)
    flow.authenticated = bool(getattr(client, "token", None))
    now = int(time.time())
    cutoff = now - window_days * 86400

    try:
        open_prs = _paginate(client, flow.slug, "open", max_pages)
        closed_prs = _paginate(client, flow.slug, "closed", max_pages, cutoff=cutoff)
    except RateLimited as e:
        flow.reason = (
            "batas laju GitHub tercapai. Pasang GITHUB_TOKEN untuk kuota "
            "yang jauh lebih besar." + getattr(e, "hint", "")
        )
        return flow
    except AccessDenied as e:
        flow.reason = f"akses ditolak GitHub (HTTP {e.code}); periksa token"
        return flow
    except NotFound:
        flow.reason = f"repositori {flow.slug} tidak terjangkau atau bersifat privat"
        return flow
    except Offline:
        flow.reason = "tidak ada koneksi ke GitHub, bagian antrian review dilewati"
        return flow

    # --- yang sedang menunggu ---
    ages = []
    for pr in open_prs:
        created = _parse_time(pr.get("created_at"))
        if created is None:
            continue
        age_h = (now - created) / HOUR
        ages.append(age_h)
        if not pr.get("draft"):
            flow.open_ready_count += 1
        if age_h > 14 * 24:
            flow.stale_count += 1
    flow.open_count = len(ages)
    if ages:
        flow.open_median_age_hours = statistics.median(ages)
        flow.open_p90_age_hours = _percentile(ages, 0.9)
        flow.oldest_age_days = max(ages) / 24

    # --- yang sudah selesai ---
    leads, ai_leads, human_leads = [], [], []
    merged: list[dict] = []
    for pr in closed_prs:
        created = _parse_time(pr.get("created_at"))
        merged_at = _parse_time(pr.get("merged_at"))
        closed_at = _parse_time(pr.get("closed_at"))
        if created is None:
            continue
        if merged_at:
            if merged_at < cutoff:
                continue
            lead = (merged_at - created) / HOUR
            leads.append(lead)
            merged.append(pr)
            if _is_ai_pr(pr):
                flow.ai_pr_count += 1
                ai_leads.append(lead)
            else:
                human_leads.append(lead)
        elif closed_at and closed_at >= cutoff:
            flow.abandoned_count += 1

    flow.merged_count = len(merged)
    if leads:
        flow.lead_median_hours = statistics.median(leads)
        flow.lead_p90_hours = _percentile(leads, 0.9)
    if len(ai_leads) >= 3:
        flow.ai_lead_median_hours = statistics.median(ai_leads)
    if len(human_leads) >= 3:
        flow.human_lead_median_hours = statistics.median(human_leads)

    # --- waktu tunggu sampai disentuh reviewer ---
    # Butuh satu panggilan per PR, jadi hanya dilakukan bila ada token.
    if merged and client.token:
        _measure_pickup(client, flow, merged, pickup_sample)
    elif merged:
        flow.warnings.append(
            "Waktu tunggu sampai reviewer pertama butuh GITHUB_TOKEN "
            "(satu panggilan API per PR)."
        )

    flow.api_calls = client.calls
    flow.ok = True
    return flow


def _paginate(client, slug, state, max_pages, cutoff=None) -> list[dict]:
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        params = {
            "state": state, "per_page": 100, "page": page,
            "sort": "created" if state == "open" else "updated",
            "direction": "desc",
        }
        batch = client.get(f"/repos/{slug}/pulls", params)
        if not batch or not isinstance(batch, list):
            break
        out.extend(batch)
        if len(batch) < 100:
            break
        if cutoff and state == "closed":
            oldest = _parse_time(batch[-1].get("updated_at"))
            if oldest and oldest < cutoff:
                break
    return out


def _measure_pickup(client, flow: PullFlow, merged: list[dict], sample: int) -> None:
    picks = []
    for pr in merged[:sample]:
        created = _parse_time(pr.get("created_at"))
        author = ((pr.get("user") or {}).get("login")) or ""
        try:
            reviews = client.get(f"/repos/{flow.slug}/pulls/{pr['number']}/reviews",
                                 {"per_page": 100})
        except (RateLimited, AccessDenied, NotFound, Offline):
            flow.warnings.append("Pengukuran waktu tunggu terhenti di tengah jalan.")
            break
        if not isinstance(reviews, list):
            continue
        stamps = [
            _parse_time(r.get("submitted_at"))
            for r in reviews
            if ((r.get("user") or {}).get("login")) != author
        ]
        stamps = [s for s in stamps if s and created and s >= created]
        if not stamps:
            flow.never_reviewed += 1
            continue
        picks.append((min(stamps) - created) / HOUR)

    flow.pickup_sampled = len(picks)
    if picks:
        flow.pickup_median_hours = statistics.median(picks)
        flow.pickup_p90_hours = _percentile(picks, 0.9)


def verdict(flow: PullFlow) -> dict[str, str]:
    """Vonis sederhana. Ambangnya sengaja longgar; yang penting arahnya."""
    def band(v, good, bad):
        return "baik" if v < good else ("waspada" if v < bad else "perhatian")

    out = {
        "queue": band(flow.stale_share, 15, 35),
        "lead": band(flow.lead_median_hours, 24, 72),
    }
    if flow.pickup_median_hours is not None:
        out["pickup"] = band(flow.pickup_median_hours, 4, 24)
    return out
