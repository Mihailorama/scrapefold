"""Per-site-class escalation ladders.

Three concerns share this module:

1. **Site classification** — ``URL_PATTERNS`` is a data table; ``classify_url``
   walks it and returns a ``SiteClass``. For sites detectable only after a
   probe response (Cloudflare, Datadome, …), ``SIGNATURES`` lists the
   response-pattern matchers consumed by ``detection.py`` for
   re-classification mid-walk.

2. **Ladder declaration** — ``LADDERS: dict[SiteClass, Ladder]`` maps each
   class to an ordered tuple of ``SequentialStep`` or ``RaceStep``. Race
   semantics (winner / cancel / budget accounting) are encoded as data on
   the step, not router convention.

3. **Walk-time contracts** — ``is_step_allowed`` enforces ``Policy``;
   ``check_budget`` enforces the per-walk ``WalkBudget`` (cost, elapsed,
   engines_tried, visited_site_classes, reclassifications) so the router
   never invokes a step it cannot afford.

Pure module: data + functions, no I/O, no engine imports.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import ClassVar, Literal

from scrapefold.engines.base import BillingUnit

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Site classification taxonomy — 33 classes
# ---------------------------------------------------------------------------

SiteClass = Literal[
    # --- LinkedIn (5) ---
    "linkedin_profile",
    "linkedin_company",
    "linkedin_post",
    "linkedin_job",
    "linkedin_sales_navigator",
    # --- Amazon (2) ---
    "amazon_product",
    "amazon_search",
    # --- Other social (10) ---
    "twitter",
    "instagram",
    "tiktok",
    "facebook",
    "youtube",
    "reddit",
    "telegram",
    "vk",
    "max",
    "russian_social",
    # --- Search engines (3) ---
    "serp_google",
    "serp_bing",
    "serp_yandex",
    # --- Easy content (4) ---
    "wikipedia",
    "news_site",
    "government",
    "ecommerce_other",
    # --- Paywall (1) ---
    "paywall_news",
    # --- Russian protected (1) ---
    "yandex_protected",
    # --- Anti-bot vendor (4) ---
    "cloudflare_protected",
    "datadome_protected",
    "akamai_protected",
    "perimeterx_protected",
    # --- Difficulty (2) ---
    "js_spa",
    "static_general",
]


# ---------------------------------------------------------------------------
# Core dataclasses — sum type for steps
# ---------------------------------------------------------------------------

BudgetMode = Literal["inherit", "reset_user_fast_track", "reset_fresh_session"]
BudgetReason = Literal["cost", "elapsed", "engines_count", "reclassifications"]


@dataclass(frozen=True)
class _StepBase:
    """Fields shared by Sequential and Race steps."""

    estimated_cost_usd: float = 0.0
    billing_unit: BillingUnit = "call"
    geography: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    legal_constraints: frozenset[str] = frozenset()
    budget_mode: BudgetMode = "inherit"
    max_extra_cost_usd: float | None = None


@dataclass(frozen=True)
class SequentialStep(_StepBase):
    """Try one engine. If it fails, the walk advances to the next step."""

    engine: str = ""


@dataclass(frozen=True)
class RaceStep(_StepBase):
    """Run multiple engines concurrently, pick a winner.

    See ``docs/conventions/golden-rules.md`` for default-selection rationale.
    """

    engines: tuple[str, ...] = ()
    winner_policy: Literal[
        "first_non_suspicious",
        "first_complete",
        "highest_text_length",
    ] = "first_non_suspicious"
    cancel_policy: Literal[
        "cancel_immediately",
        "cancel_with_grace",
        "let_finish",
    ] = "cancel_immediately"
    cancel_grace_ms: int = 0
    budget_accounting: Literal["winner_only", "sum_all", "max"] = "winner_only"


LadderStep = SequentialStep | RaceStep
Ladder = tuple[LadderStep, ...]


# ---------------------------------------------------------------------------
# WalkBudget — mutable state carried across reclassifications
# ---------------------------------------------------------------------------


@dataclass
class WalkBudget:
    """Mutable budget for a single ``scrape()`` walk."""

    elapsed_ms: int = 0
    cost_usd: float = 0.0
    engines_tried: set[str] = field(default_factory=set)
    visited_site_classes: set[SiteClass] = field(default_factory=set)
    reclassifications: int = 0

    MAX_RECLASSIFICATIONS: ClassVar[int] = 3


class BudgetExceeded(Exception):  # noqa: N818
    """Raised when continuing the walk would breach a budget ceiling."""

    def __init__(self, reason: BudgetReason) -> None:
        super().__init__(reason)
        self.reason: BudgetReason = reason


class AllEnginesFailed(Exception):  # noqa: N818
    """Raised when every step in the ladder failed or was skipped.

    Carries structured failure data so consumers can introspect what was
    tried without parsing the exception's string form.
    """

    def __init__(self, url: str, failures: list[str]) -> None:
        self.url = url
        self.failures = list(failures)
        super().__init__(f"all engines failed for {url}: {self.failures}")


# ---------------------------------------------------------------------------
# Policy — user-driven gating
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Policy:
    """Walk-level policy carried in ``ScrapeOptions.extra["policy"]``."""

    paid_allowed: bool = True
    legal_constraints_blocked: frozenset[str] = frozenset()
    geography_required: str | None = None


DEFAULT_POLICY: dict[SiteClass, Policy] = {
    # Government sites: commercial scraping APIs often have ToS friction
    # plus user-confirmation needs. Default to free-only; user may override.
    "government": Policy(paid_allowed=False),
}


def get_default_policy(site_class: SiteClass) -> Policy:
    return DEFAULT_POLICY.get(site_class, Policy())


# ---------------------------------------------------------------------------
# URL classification — data table
# ---------------------------------------------------------------------------

# Ordered specific-first. The first regex that matches wins.
# Adding a new entry: ALSO add a row to GOLDEN_CORPUS in tests so the
# parametrized classification test enforces correctness.
URL_PATTERNS: tuple[tuple[re.Pattern[str], SiteClass], ...] = (
    (re.compile(r"linkedin\.com/sales/"), "linkedin_sales_navigator"),
    (re.compile(r"linkedin\.com/in/"), "linkedin_profile"),
    (re.compile(r"linkedin\.com/company/"), "linkedin_company"),
    (re.compile(r"linkedin\.com/(posts|pulse)/"), "linkedin_post"),
    (re.compile(r"linkedin\.com/jobs/"), "linkedin_job"),
    (re.compile(r"amazon\.[a-z.]+/(dp|gp/product)/"), "amazon_product"),
    (re.compile(r"amazon\.[a-z.]+/s\?"), "amazon_search"),
    (re.compile(r"(twitter|x)\.com/"), "twitter"),
    (re.compile(r"instagram\.com/"), "instagram"),
    (re.compile(r"tiktok\.com/"), "tiktok"),
    (re.compile(r"(facebook|fb)\.com/"), "facebook"),
    (re.compile(r"(youtube\.com|youtu\.be)/"), "youtube"),
    (re.compile(r"reddit\.com/"), "reddit"),
    (re.compile(r"(t\.me|telegram\.me|telegram\.dog)/"), "telegram"),
    (re.compile(r"(vk\.com|vk\.ru)/"), "vk"),
    (re.compile(r"(//|\.)max\.ru/"), "max"),
    (re.compile(r"(mail|my)\.ru/"), "russian_social"),
    (re.compile(r"google\.[a-z.]+/search"), "serp_google"),
    (re.compile(r"bing\.com/search"), "serp_bing"),
    (re.compile(r"yandex\.[a-z.]+/search"), "serp_yandex"),
    (re.compile(r"wikipedia\.org/"), "wikipedia"),
    (re.compile(r"\.(gov|gov\.[a-z]{2}|mil)(/|$)"), "government"),
)


def classify_url(url: str) -> SiteClass:
    """Return a ``SiteClass`` for the URL, or ``"static_general"`` as fallback."""
    for pattern, cls in URL_PATTERNS:
        if pattern.search(url):
            return cls
    return "static_general"


# ---------------------------------------------------------------------------
# Golden corpus — every classifiable URL→class pair, used by the parametrized
# test. Lives in lib (not tests/) so the router, CLI introspection, and
# tests read from one source of truth.
# ---------------------------------------------------------------------------

GOLDEN_CORPUS: tuple[tuple[str, SiteClass], ...] = (
    ("https://www.linkedin.com/sales/people/foo", "linkedin_sales_navigator"),
    ("https://www.linkedin.com/in/john-doe/", "linkedin_profile"),
    ("https://www.linkedin.com/company/acme/", "linkedin_company"),
    ("https://www.linkedin.com/posts/foo_activity-1", "linkedin_post"),
    ("https://www.linkedin.com/pulse/foo-bar", "linkedin_post"),
    ("https://www.linkedin.com/jobs/view/1234", "linkedin_job"),
    ("https://www.amazon.com/dp/B07XYZ", "amazon_product"),
    ("https://www.amazon.co.uk/gp/product/B07XYZ", "amazon_product"),
    ("https://www.amazon.de/s?k=foo", "amazon_search"),
    ("https://twitter.com/elonmusk", "twitter"),
    ("https://x.com/elonmusk/status/1", "twitter"),
    ("https://www.instagram.com/foo/", "instagram"),
    ("https://www.tiktok.com/@foo", "tiktok"),
    ("https://www.tiktok.com/@foo/video/123", "tiktok"),
    ("https://www.facebook.com/natgeo", "facebook"),
    ("https://www.youtube.com/watch?v=abc", "youtube"),
    ("https://www.reddit.com/r/python/", "reddit"),
    ("https://t.me/s/durov", "telegram"),
    ("https://t.me/durov/123", "telegram"),
    ("https://telegram.me/durov", "telegram"),
    ("https://vk.com/durov", "vk"),
    ("https://vk.ru/durov", "vk"),
    ("https://max.ru/u/foo", "max"),
    ("https://web.max.ru/u/foo", "max"),
    ("https://my.mail.ru/foo", "russian_social"),
    ("https://www.google.com/search?q=foo", "serp_google"),
    ("https://www.bing.com/search?q=foo", "serp_bing"),
    ("https://yandex.ru/search/?text=foo", "serp_yandex"),
    ("https://en.wikipedia.org/wiki/Python", "wikipedia"),
    ("https://www.example.gov.uk/services", "government"),
    ("https://www.fbi.gov/about", "government"),
    ("https://example.com/blog/post", "static_general"),
)


# ---------------------------------------------------------------------------
# Signatures — for response-content reclassification.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Signature:
    target: SiteClass
    body_phrases_all: tuple[str, ...] = ()
    body_phrases_any: tuple[str, ...] = ()
    cookie_names: tuple[str, ...] = ()
    header_names: tuple[str, ...] = ()
    status_codes: tuple[int, ...] = ()
    min_matches: int = 2
    """Threshold to prevent single-phrase false positives reclassifying
    unrelated 5xx pages."""


# Vendor-anti-bot first (Datadome / PerimeterX / Akamai before Cloudflare —
# CF phrases sometimes appear on unrelated 5xx pages).
SIGNATURES: tuple[Signature, ...] = (
    Signature(
        target="datadome_protected",
        cookie_names=("datadome",),
        body_phrases_any=("/captcha-delivery/", "geo.captcha-delivery.com"),
        min_matches=2,
    ),
    Signature(
        target="perimeterx_protected",
        cookie_names=("_px", "_px2", "_px3", "_pxhd"),
        header_names=("x-px",),
        min_matches=2,
    ),
    Signature(
        target="akamai_protected",
        cookie_names=("_abck", "bm_sz", "ak_bmsc"),
        min_matches=2,
    ),
    Signature(
        target="cloudflare_protected",
        body_phrases_any=(
            "Just a moment...",
            "cf-browser-verification",
            "Checking your browser",
        ),
        cookie_names=("__cf_bm", "cf_clearance"),
        header_names=("cf-ray",),
        min_matches=2,
    ),
)


# ---------------------------------------------------------------------------
# Per-class ladders
# ---------------------------------------------------------------------------

# Coarse per-call USD estimates used by the ladders below.
_FREE = 0.0
_LOW = 0.0005  # $0.50 per 1k
_MED = 0.001  # $1.00 per 1k
_HIGH = 0.0015  # $1.50 per 1k


def _seq(engine: str, *, cost: float = 0.0, **kwargs: object) -> SequentialStep:
    return SequentialStep(engine=engine, estimated_cost_usd=cost, **kwargs)  # type: ignore[arg-type]


def _race(*engines: str, **kwargs: object) -> RaceStep:
    return RaceStep(engines=tuple(engines), **kwargs)  # type: ignore[arg-type]


_GENERAL_LADDER: Ladder = (
    _seq("requests", cost=_FREE),  # T0
    _race("scrapling_stealth", "crawl4ai"),  # T1 free JS
    _race("cloakbrowser", "obscura"),  # T2 free stealth
    _race(
        "firecrawl",
        "scrapingbee",
        "scrapingdog",
        "cloudflare",
        "jina",
        budget_accounting="sum_all",  # paid engines: each attempted req is billed
    ),  # T3
    _seq("brightdata_unlocker_sync", cost=_HIGH),  # T4
)


LADDERS: dict[SiteClass, Ladder] = {
    # LinkedIn — vendor-specialized endpoints first, never plain HTTP.
    "linkedin_profile": (
        _race(
            "apify_linkedin",
            "anysite",
            "scrapingdog",
            "exa",
            "socialcrawl",
            budget_accounting="sum_all",
        ),
        _seq("brightdata_unlocker_sync", cost=_HIGH),
    ),
    "linkedin_company": (
        _race(
            "apify_linkedin",
            "anysite",
            "scrapingdog",
            "exa",
            "socialcrawl",
            budget_accounting="sum_all",
        ),
        _seq("brightdata_unlocker_sync", cost=_HIGH),
    ),
    "linkedin_post": (
        _race("apify_linkedin", "anysite", "socialcrawl", budget_accounting="sum_all"),
        _seq("brightdata_unlocker_sync", cost=_HIGH),
    ),
    "linkedin_job": (
        _race("apify_linkedin", "anysite", "scrapingdog", budget_accounting="sum_all"),
        _seq("brightdata_unlocker_sync", cost=_HIGH),
    ),
    "linkedin_sales_navigator": (
        _race("apify_linkedin", "anysite", budget_accounting="sum_all"),
        _seq("brightdata_unlocker_sync", cost=_HIGH),
    ),
    # Amazon
    "amazon_product": (
        _seq("scrapingdog", cost=_LOW),
        _seq("brightdata_unlocker_sync", cost=_HIGH),
        _seq("firecrawl", cost=_MED),
    ),
    "amazon_search": (
        _seq("scrapingdog", cost=_LOW),
        _race("brightdata_unlocker_sync", "scrapingbee", budget_accounting="sum_all"),
    ),
    # Twitter / X
    "twitter": (
        _race(
            "scrapecreators",
            "socialcrawl",
            "apify_actor",
            "scrapingdog",
            budget_accounting="sum_all",
        ),
        _seq("brightdata_unlocker_sync", cost=_HIGH),
    ),
    # Other social
    "instagram": (
        _race(
            "scrapecreators",
            "socialcrawl",
            "apify_actor",
            "anysite",
            budget_accounting="sum_all",
        ),
        _seq("brightdata_unlocker_sync", cost=_HIGH),
    ),
    "tiktok": (
        # TikTok has no plain-HTTP path — lead with structured social gateways
        # and the Apify TikTok actor, then fall back to a stealth unlocker.
        _race(
            "scrapecreators",
            "socialcrawl",
            "apify_actor",
            budget_accounting="sum_all",
        ),
        _seq("brightdata_unlocker_sync", cost=_HIGH),
    ),
    "facebook": (
        _race("socialcrawl", "apify_actor", "anysite", budget_accounting="sum_all"),
        _seq("brightdata_unlocker_sync", cost=_HIGH),
    ),
    "youtube": (
        _race(
            "socialcrawl",
            "scrapecreators",
            "apify_actor",
            "anysite",
            budget_accounting="sum_all",
        ),
        _seq("brightdata_unlocker_sync", cost=_HIGH),
    ),
    "reddit": (
        # Reddit has a public JSON API at /.json — try the free path alongside
        # social structured endpoints first.
        # PROBE_SCOPE = per_domain on the requests engine validates viability.
        _race(
            "scrapecreators",
            "socialcrawl",
            "apify_actor",
            "requests",
            budget_accounting="sum_all",
        ),
        _race("scrapling_stealth", "crawl4ai"),
        _seq("firecrawl", cost=_MED),
    ),
    # Telegram — public channels render as server-side HTML at t.me/s/<channel>;
    # the dedicated free engine parses them into normalized posts. Plain HTTP and
    # stealth are cheap fallbacks if the preview shape changes.
    "telegram": (
        _seq("telegram"),
        _seq("requests"),
        _race("scrapling_stealth", "crawl4ai"),
    ),
    # VK (vk.com / vk.ru) — Russian anti-bot needs a stealth browser; structured
    # data requires the VK API (no free path), so this is HTML-only for now.
    "vk": (
        _race("scrapling_stealth", "cloakbrowser"),
        _seq("scrapingdog", cost=_LOW),
        _seq("brightdata_unlocker_sync", cost=_HIGH),
    ),
    # Max (max.ru) — VK's newer messenger; JS web app, no public scraping API.
    # Lead with stealth browsers, then paid unlockers.
    "max": (
        _race("scrapling_stealth", "cloakbrowser"),
        _seq("firecrawl", cost=_MED),
        _seq("brightdata_unlocker_sync", cost=_HIGH),
    ),
    "russian_social": (
        # my.mail.ru and other RU social — Russian anti-bot needs a stealth
        # browser; paid options often blocked by Roskomnadzor geofences.
        _race("scrapling_stealth", "cloakbrowser"),
        _seq("scrapingdog", cost=_LOW),
    ),
    # SERP
    "serp_google": (
        _seq("scrapingdog", cost=_LOW),
        _seq("scrapingbee", cost=_MED),
        _seq("brightdata_unlocker_sync", cost=_HIGH),
    ),
    "serp_bing": (
        _seq("scrapingdog", cost=_LOW),
        _seq("scrapingbee", cost=_MED),
    ),
    "serp_yandex": (
        _race("scrapingdog", "scrapling_stealth", budget_accounting="sum_all"),
        _seq("brightdata_unlocker_sync", cost=_HIGH),
    ),
    # Easy content — never escalate to paid (save cost)
    "wikipedia": (_seq("requests"),),
    "news_site": (
        _seq("requests"),
        _seq("scrapling_stealth"),
    ),
    "government": (
        # DEFAULT_POLICY blocks paid for government — only free engines anyway.
        _seq("requests"),
        _seq("scrapling_stealth"),
    ),
    "ecommerce_other": (
        _seq("requests"),
        _race("scrapling_stealth", "crawl4ai"),
        _race("cloakbrowser", "obscura"),
        _race("firecrawl", "scrapingbee", budget_accounting="sum_all"),
    ),
    # Paywall — needs a real browser to get past front-page gate
    "paywall_news": (
        _race("scrapling_stealth", "cloakbrowser"),
        _seq("firecrawl", cost=_MED),
        _seq("brightdata_unlocker_sync", cost=_HIGH),
    ),
    # Russian protected (Yandex captcha)
    "yandex_protected": (
        _race("cloakbrowser", "obscura"),
        _seq("brightdata_unlocker_sync", cost=_HIGH),
    ),
    # Anti-bot vendor — reclassification targets
    "cloudflare_protected": (
        _race("cloakbrowser", "obscura", "scrapling_stealth"),
        _race("firecrawl", "scrapingbee", budget_accounting="sum_all"),
        _seq("brightdata_unlocker_sync", cost=_HIGH),
    ),
    "datadome_protected": (
        _race("cloakbrowser", "obscura"),
        _seq("brightdata_unlocker_sync", cost=_HIGH),
    ),
    "akamai_protected": (
        _seq("cloakbrowser"),
        _race("brightdata_unlocker_sync", "scrapingbee", budget_accounting="sum_all"),
    ),
    "perimeterx_protected": (
        _seq("cloakbrowser"),
        _seq("brightdata_unlocker_sync", cost=_HIGH),
    ),
    # Difficulty class
    "js_spa": (
        _race("scrapling_stealth", "crawl4ai"),
        _race("cloakbrowser", "obscura"),
        _seq("firecrawl", cost=_MED),
        _seq("brightdata_unlocker_sync", cost=_HIGH),
    ),
    "static_general": _GENERAL_LADDER,
}


def get_ladder(site_class: SiteClass | str) -> Ladder:
    """Return the escalation ladder for a given site class.

    Falls back to the general ladder for unknown classes.
    """
    return LADDERS.get(site_class, _GENERAL_LADDER)  # type: ignore[arg-type]


def step_engines(step: LadderStep) -> tuple[str, ...]:
    """Return the engine name(s) referenced by a step."""
    if isinstance(step, SequentialStep):
        return (step.engine,)
    return step.engines


def flatten_ladder(ladder: Ladder) -> tuple[str, ...]:
    """Flatten a ladder into the ordered tuple of engine names — for docs/CLI."""
    out: list[str] = []
    for step in ladder:
        out.extend(step_engines(step))
    return tuple(out)


# ---------------------------------------------------------------------------
# Walk-time contracts — pure functions, consumed by the router.
# ---------------------------------------------------------------------------


def is_step_allowed(step: LadderStep, policy: Policy) -> tuple[bool, str | None]:
    """Return ``(allowed, reason_or_None)``.

    Called BEFORE ``check_budget`` for every step. On ``False`` the router
    logs the reason at DEBUG and advances to the next step.
    """
    if not policy.paid_allowed and step.estimated_cost_usd > 0.0:
        return False, "paid_not_allowed"
    if step.legal_constraints:
        blocked = step.legal_constraints & policy.legal_constraints_blocked
        if blocked:
            return False, f"legal_blocked:{','.join(sorted(blocked))}"
    if (
        policy.geography_required
        and step.geography
        and policy.geography_required not in step.geography
    ):
        return False, f"geography:{policy.geography_required}_not_in_{step.geography}"
    return True, None


def estimate_step_cost(
    step: LadderStep,
    avg_response_mb: float = 2.0,
) -> float:
    """Convert ``estimated_cost_usd`` (per ``billing_unit``) to a per-call USD estimate.

    ``avg_response_mb`` only applies for ``billing_unit == "gb"``. Default
    2 MB is conservative; engines with large payloads override via
    ``opts.extra["avg_response_mb"]``.
    """
    if step.billing_unit == "gb":
        return step.estimated_cost_usd * (avg_response_mb / 1024)
    return step.estimated_cost_usd


def check_budget(
    step: LadderStep,
    walk: WalkBudget,
    *,
    timeout_s: int,
    max_engines: int,
    max_cost_usd: float,
    avg_response_mb: float = 2.0,
) -> None:
    """Raise ``BudgetExceeded`` if invoking ``step`` would breach any ceiling.

    For a ``RaceStep``, the engine-count ceiling counts the entire fan-out so
    a 3-engine race cannot start with only 1 engine slot remaining.
    """
    if walk.elapsed_ms / 1000 >= timeout_s:
        raise BudgetExceeded("elapsed")
    if len(walk.engines_tried) + len(step_engines(step)) > max_engines:
        raise BudgetExceeded("engines_count")
    step_cost = estimate_step_cost(step, avg_response_mb=avg_response_mb)
    if step.max_extra_cost_usd is not None and step_cost > step.max_extra_cost_usd:
        raise BudgetExceeded("cost")
    if walk.cost_usd + step_cost > max_cost_usd:
        raise BudgetExceeded("cost")
    if walk.reclassifications >= WalkBudget.MAX_RECLASSIFICATIONS:
        raise BudgetExceeded("reclassifications")


__all__ = [
    "DEFAULT_POLICY",
    "GOLDEN_CORPUS",
    "LADDERS",
    "SIGNATURES",
    "URL_PATTERNS",
    "AllEnginesFailed",
    "BudgetExceeded",
    "BudgetMode",
    "BudgetReason",
    "Ladder",
    "LadderStep",
    "Policy",
    "RaceStep",
    "SequentialStep",
    "Signature",
    "SiteClass",
    "WalkBudget",
    "check_budget",
    "classify_url",
    "estimate_step_cost",
    "flatten_ladder",
    "get_default_policy",
    "get_ladder",
    "is_step_allowed",
    "step_engines",
]
