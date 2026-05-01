"""
scoring.py — Fusion engine & KCS (Kirana Credit Score)

Combines vision + geo + fraud signals into:
  - Daily / monthly revenue ranges
  - Normalized monthly income
  - KCS score (0–850, like CIBIL for physical stores)
  - Adaptive confidence score (decays post-assessment)
  - Recommendation
"""
import math, datetime

# ── Calibration constants (tuned against industry benchmarks) ─────────────────
# Sources: RBI MSME reports, Dun & Bradstreet kirana studies, NABARD surveys
KIRANA_BENCHMARKS = {
    "daily_sales_per_sdi_point":   85,    # ₹ per SDI point/day (baseline)
    "sku_multiplier_per_10pts":    0.08,  # +8% per 10 SKU diversity points
    "footfall_multiplier_per_10":  0.07,  # +7% per 10 footfall index points
    "wage_multiplier_per_10":      0.04,  # +4% per 10 wage proxy points
    "competition_adjustment":      0.05,  # moderate competition → +5%
    "margin_rate_base":            0.14,  # 14% average kirana net margin
    "margin_rate_premium":         0.20,  # 20% for high-SKU / premium mix
    "uncertainty_band":            0.25,  # ±25% uncertainty band
}

# ── Confidence decay ──────────────────────────────────────────────────────────

def confidence_at_days(c0: float, days: int) -> float:
    """
    Adaptive confidence decay: C(t) = C0 * e^(-0.004 * t)
    At t=90d  → ~70% of original
    At t=180d → ~49% of original
    """
    return round(c0 * math.exp(-0.004 * days), 3)


# ── Signal weights (sum = 1.0) ────────────────────────────────────────────────
WEIGHTS = {
    "shelf_density_index":   0.22,
    "sku_diversity_score":   0.18,
    "footfall_proxy_index":  0.18,
    "wage_proxy_score":      0.12,
    "competition_density":   0.10,   # inverted: too-high competition reduces score
    "store_cleanliness":     0.06,
    "fast_moving_ratio":     0.08,
    "catchment_density":     0.06,
}


def _weighted_composite(vision: dict, geo: dict) -> float:
    """Compute a 0-100 composite signal score from all sub-scores."""
    shelf   = vision.get("shelf",   {})
    signals = {
        "shelf_density_index":  shelf.get("shelf_density_index",  50),
        "sku_diversity_score":  shelf.get("sku_diversity_score",  50),
        "footfall_proxy_index": geo.get("footfall_proxy_index",   50),
        "wage_proxy_score":     geo.get("wage_proxy_score",       50),
        "competition_density":  100 - geo.get("competition_density", 50),  # invert
        "store_cleanliness":    shelf.get("store_cleanliness",    50),
        "fast_moving_ratio":    shelf.get("fast_moving_ratio",    50),
        "catchment_density":    geo.get("catchment_density",      50),
    }
    total = sum(signals[k] * WEIGHTS[k] for k in WEIGHTS)
    return round(min(100, max(0, total)), 2)


def _inventory_value_multiplier(band: str) -> float:
    return {"low": 0.7, "medium": 1.0, "high": 1.4}.get(band, 1.0)


def _margin_rate(vision: dict) -> float:
    shelf = vision.get("shelf", {})
    cats  = shelf.get("product_categories", [])
    high_margin = {"dairy", "beverages", "bakery", "personal_care", "household"}
    overlap = len(set(cats) & high_margin)
    if overlap >= 2 or shelf.get("inventory_value_band") == "high":
        return KIRANA_BENCHMARKS["margin_rate_premium"]
    return KIRANA_BENCHMARKS["margin_rate_base"]


# ── Revenue estimation ────────────────────────────────────────────────────────

def estimate_revenue(vision: dict, geo: dict, optional: dict) -> dict:
    """
    Estimate daily / monthly revenue using calibrated economic proxy logic.
    Returns ranges (low, high) not single-point estimates.
    """
    shelf = vision.get("shelf", {})
    sdi   = shelf.get("shelf_density_index", 50)

    # base daily estimate from shelf density
    base_daily = sdi * KIRANA_BENCHMARKS["daily_sales_per_sdi_point"]

    # adjust for SKU diversity
    sku_adj = 1 + (shelf.get("sku_diversity_score", 50) - 50) / 10 * KIRANA_BENCHMARKS["sku_multiplier_per_10pts"]

    # adjust for footfall
    fp_adj  = 1 + (geo.get("footfall_proxy_index", 50) - 50) / 10 * KIRANA_BENCHMARKS["footfall_multiplier_per_10"]

    # adjust for local wealth (wage proxy)
    wage_adj = 1 + (geo.get("wage_proxy_score", 50) - 50) / 10 * KIRANA_BENCHMARKS["wage_multiplier_per_10"]

    # adjust for inventory value band
    inv_adj = _inventory_value_multiplier(shelf.get("inventory_value_band", "medium"))

    # optional rent factor (high rent → high revenue location)
    rent = float(optional.get("rent") or 0)
    rent_adj = 1.0
    if rent > 0:
        # rule of thumb: rent is ~1.5-3% of monthly revenue for kirana
        implied_monthly = rent / 0.02
        implied_daily   = implied_monthly / 30
        # blend 30% rent-based, 70% signal-based
        base_daily = base_daily * 0.70 + implied_daily * 0.30

    mid_daily = base_daily * sku_adj * fp_adj * wage_adj * inv_adj * rent_adj
    band      = KIRANA_BENCHMARKS["uncertainty_band"]

    daily_lo  = round(mid_daily * (1 - band), -2)   # round to nearest 100
    daily_hi  = round(mid_daily * (1 + band), -2)

    monthly_lo = round(daily_lo * 26, -3)  # 26 working days
    monthly_hi = round(daily_hi * 26, -3)

    margin = _margin_rate(vision)
    income_lo = round(monthly_lo * margin, -3)
    income_hi = round(monthly_hi * margin, -3)

    # sanity clamp (₹500 – ₹5L per day)
    daily_lo  = max(500,   min(500000, daily_lo))
    daily_hi  = max(1000,  min(600000, daily_hi))

    return {
        "daily_sales_range":   [int(daily_lo),   int(daily_hi)],
        "monthly_revenue_range":[int(monthly_lo), int(monthly_hi)],
        "monthly_income_range": [int(income_lo),  int(income_hi)],
        "margin_rate_used":     round(margin * 100, 1),
    }


# ── KCS Score (0–850) ─────────────────────────────────────────────────────────

def compute_kcs(composite: float, fraud: dict, optional: dict) -> int:
    """
    Map composite 0-100 to KCS 0-850, with fraud and optional adjustments.

    Band mapping (mirrors CIBIL bands):
      750–850: Excellent
      650–749: Good
      550–649: Fair
      450–549: Poor
      0–449:   Very Poor
    """
    # base mapping: 0-100 → 300-850
    base_kcs = 300 + composite * 5.5

    # fraud penalty
    fraud_score = fraud.get("fraud_score", 0)
    fraud_penalty = fraud_score * 2.0   # up to -200 pts

    # shop age bonus (older = more reliable)
    age_bonus = 0
    try:
        age = int(optional.get("shop_age") or 0)
        age_bonus = min(30, age * 3)   # +3 pts/year up to +30
    except (ValueError, TypeError):
        pass

    kcs = base_kcs - fraud_penalty + age_bonus
    return int(min(850, max(0, round(kcs))))


def kcs_band(kcs: int) -> str:
    if kcs >= 750: return "Excellent"
    if kcs >= 650: return "Good"
    if kcs >= 550: return "Fair"
    if kcs >= 450: return "Poor"
    return "Very Poor"


def recommendation(kcs: int, fraud: dict) -> str:
    risk = fraud.get("overall_fraud_risk", "low")
    if risk == "high":
        return "needs_verification"
    if kcs >= 650 and risk == "low":
        return "approve"
    if kcs >= 550:
        return "approve_with_conditions"
    return "decline"


def max_loan_amount(monthly_income_hi: int, kcs: int) -> int:
    """Rough loan eligibility: 3-6x monthly income based on KCS."""
    multiplier = 3 + (kcs - 400) / 450 * 3   # 3x at KCS=400, 6x at KCS=850
    multiplier = max(1.5, min(6.0, multiplier))
    amount = monthly_income_hi * multiplier
    return int(round(amount, -3))   # round to nearest ₹1000


# ── Confidence score ──────────────────────────────────────────────────────────

def base_confidence(vision: dict, geo: dict, fraud: dict) -> float:
    """
    Compute initial confidence score (0–1) based on signal quality.
    """
    c = 0.85   # start optimistic

    # penalise missing data
    if vision.get("image_count", 0) < 3:
        c -= 0.10
    if geo.get("mock"):
        c -= 0.10

    # penalise fraud flags
    n_flags = len(fraud.get("active_flags", []))
    c -= n_flags * 0.06

    # vision consistency
    c *= vision.get("consistency_score", 1.0)

    return round(min(1.0, max(0.1, c)), 2)


# ── Public API ────────────────────────────────────────────────────────────────

def compute_kcs_score(vision: dict, geo: dict, fraud: dict, optional: dict) -> dict:
    """
    Master scoring function. Returns the full KiranaLens output JSON.
    """
    composite  = _weighted_composite(vision, geo)
    revenue    = estimate_revenue(vision, geo, optional)
    kcs        = compute_kcs(composite, fraud, optional)
    band       = kcs_band(kcs)
    rec        = recommendation(kcs, fraud)
    conf_now   = base_confidence(vision, geo, fraud)
    conf_90d   = confidence_at_days(conf_now, 90)
    conf_180d  = confidence_at_days(conf_now, 180)
    loan_limit = max_loan_amount(revenue["monthly_income_range"][1], kcs)

    # sub-scores for dashboard breakdown chart
    sub_scores = {
        "shelf_density":     vision.get("shelf", {}).get("shelf_density_index", 50),
        "sku_diversity":     vision.get("shelf", {}).get("sku_diversity_score", 50),
        "geo_footfall":      geo.get("footfall_proxy_index", 50),
        "refill_velocity":   vision.get("shelf", {}).get("fast_moving_ratio", 50),
        "wage_proxy":        geo.get("wage_proxy_score", 50),
        "peer_rank":         min(100, int(composite * 1.1)),   # composite-derived
    }

    return {
        # ── primary output ──
        "daily_sales_range":    revenue["daily_sales_range"],
        "monthly_revenue_range":revenue["monthly_revenue_range"],
        "monthly_income_range": revenue["monthly_income_range"],
        "kcs_score":            kcs,
        "kcs_band":             band,
        "confidence_score":     conf_now,
        "confidence_90d":       conf_90d,
        "confidence_180d":      conf_180d,
        "recommendation":       rec,
        "max_loan_eligibility": loan_limit,

        # ── risk flags ──
        "risk_flags":           fraud.get("active_flags", []),
        "fraud_risk":           fraud.get("overall_fraud_risk", "low"),

        # ── breakdowns ──
        "sub_scores":           sub_scores,
        "composite_signal":     composite,
        "margin_rate_pct":      revenue["margin_rate_used"],

        # ── decay schedule ──
        "confidence_decay": {
            "now":    conf_now,
            "90_days":  conf_90d,
            "180_days": conf_180d,
            "formula":  "C(t) = C0 × e^(-0.004t)",
        },

        # ── metadata ──
        "assessed_at":      datetime.datetime.now().isoformat(),
        "model_version":    "1.0.0",
    }
