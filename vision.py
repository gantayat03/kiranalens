import os
"""
vision.py — Image analysis using Ollama + LLaVA (local, free)

Real mode  : requires `ollama pull llava:13b` (one-time ~8 GB download)
Mock mode  : returns deterministic fake signals — use for demos without Ollama
"""
import base64, json, random, requests
from pathlib import Path

OLLAMA_URL  = "http://localhost:11434/api/generate"
LLAVA_MODEL = "llava:13b"

# ── prompts ───────────────────────────────────────────────────────────────────

SHELF_PROMPT = """
You are analyzing an image of an Indian kirana (grocery) store interior.
Respond ONLY with a JSON object — no markdown, no explanation.

{
  "shelf_density_index": <0-100, percentage of visible shelf space occupied>,
  "sku_diversity_score": <0-100, variety of product categories>,
  "inventory_value_band": <"low"|"medium"|"high">,
  "fast_moving_ratio":   <0-100, estimated % of fast-moving vs slow goods>,
  "refill_signal":       <"overstocked"|"normal"|"partially_empty">,
  "store_cleanliness":   <0-100>,
  "visible_brands":      [list up to 5 brand names you can see],
  "product_categories":  [list categories e.g. "staples","beverages","snacks"],
  "notes":               "<one sentence observation>"
}
"""

EXTERIOR_PROMPT = """
You are analyzing the exterior / street-view image of an Indian kirana store.
Respond ONLY with a JSON object — no markdown, no explanation.

{
  "store_size_estimate":  <"small"|"medium"|"large">,
  "frontage_width_m":     <estimated frontage in metres, 1-20>,
  "street_type":          <"arterial"|"lane"|"market_street"|"residential">,
  "footpath_present":     <true|false>,
  "visible_customer_count": <0-10, count of visible customers>,
  "signage_quality":      <"none"|"basic"|"good"|"professional">,
  "shadow_angle_estimate": <estimated angle of shadows in degrees, 0-180, or null if no shadows>,
  "time_of_day_guess":    <"morning"|"afternoon"|"evening"|"unknown">,
  "notes":                "<one sentence observation>"
}
"""

COUNTER_PROMPT = """
You are analyzing the counter area of an Indian kirana store.
Respond ONLY with a JSON object — no markdown, no explanation.

{
  "counter_organisation": <0-100, how organised the counter looks>,
  "premium_products_visible": <true|false, any premium/high-margin items at counter>,
  "digital_payment_visible":  <true|false, UPI/QR code visible>,
  "loose_items_present":      <true|false, loose grain/spices sold by weight>,
  "estimated_daily_customers": <"low:<20"|"medium:20-60"|"high:>60">,
  "notes": "<one sentence observation>"
}
"""


def _encode_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _query_llava(prompt: str, image_path: str) -> dict:
    """Send one image + prompt to Ollama LLaVA and return parsed JSON."""
    payload = {
        "model":  LLAVA_MODEL,
        "prompt": prompt,
        "images": [_encode_image(image_path)],
        "stream": False,
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
    resp.raise_for_status()
    raw = resp.json().get("response", "{}")
    # strip any accidental markdown fences
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    return json.loads(raw)


# ── mock data ─────────────────────────────────────────────────────────────────

def _mock_shelf() -> dict:
    return {
        "shelf_density_index":  random.randint(55, 88),
        "sku_diversity_score":  random.randint(60, 90),
        "inventory_value_band": random.choice(["medium", "high"]),
        "fast_moving_ratio":    random.randint(55, 80),
        "refill_signal":        random.choice(["normal", "partially_empty"]),
        "store_cleanliness":    random.randint(55, 85),
        "visible_brands":       ["Parle-G", "Maggi", "Amul", "Tata Salt"],
        "product_categories":   ["staples", "beverages", "snacks", "dairy"],
        "notes":                "Mock: well-stocked shelf with diverse FMCG brands.",
    }

def _mock_exterior() -> dict:
    return {
        "store_size_estimate":    "medium",
        "frontage_width_m":       random.randint(8, 14),
        "street_type":            random.choice(["lane", "market_street"]),
        "footpath_present":       True,
        "visible_customer_count": random.randint(1, 5),
        "signage_quality":        "basic",
        "shadow_angle_estimate":  random.randint(30, 150),
        "time_of_day_guess":      "afternoon",
        "notes":                  "Mock: busy lane-facing store with moderate foot traffic.",
    }

def _mock_counter() -> dict:
    return {
        "counter_organisation":      random.randint(55, 85),
        "premium_products_visible":  True,
        "digital_payment_visible":   True,
        "loose_items_present":       True,
        "estimated_daily_customers": "medium:20-60",
        "notes":                     "Mock: organised counter with UPI QR visible.",
    }


# ── public API ────────────────────────────────────────────────────────────────
def analyze_images(image_paths: list[str], mock: bool = False) -> dict:
    """
    Analyze a list of images and return aggregated vision signals.
    """
    # --- ADD THIS CHECK RIGHT HERE ---
    
    # ---------------------------------

    shelf_results    = []
    exterior_results = []
    counter_results  = []
def analyze_images(image_paths: list[str], mock: bool = False) -> dict:
    """
    Analyze a list of images and return aggregated vision signals.

    Returns:
        {
          shelf:    {...},   # aggregated shelf signals
          exterior: {...},   # first exterior / storefront reading
          counter:  {...},   # counter signals
          image_count: int,
          consistency_score: float,   # 0-1 multi-image coherence
          consistency_flags: [str],
        }
    """
    if os.environ.get("USE_MOCK_VISION") == "1":
          mock = True
          print("[vision] CI Environment detected: Forcing Mock Mode")
      
    shelf_results    = []
    exterior_results = []
    counter_results  = []

    for idx, path in enumerate(image_paths):
        fname = Path(path).name.lower()
        # heuristic: guess image type from filename or position
        if any(k in fname for k in ["ext", "front", "outside", "street"]):
            img_type = "exterior"
        elif any(k in fname for k in ["counter", "cash", "desk"]):
            img_type = "counter"
        else:
            img_type = "shelf" if idx % 3 != 1 else "exterior"

        if mock:
            if img_type == "exterior":
                exterior_results.append(_mock_exterior())
            elif img_type == "counter":
                counter_results.append(_mock_counter())
            else:
                shelf_results.append(_mock_shelf())
        else:
            try:
                if img_type == "exterior":
                    exterior_results.append(_query_llava(EXTERIOR_PROMPT, path))
                elif img_type == "counter":
                    counter_results.append(_query_llava(COUNTER_PROMPT, path))
                else:
                    shelf_results.append(_query_llava(SHELF_PROMPT, path))
            except Exception as e:
                print(f"[vision] LLaVA error on {path}: {e}. Using mock.")
                if img_type == "exterior":
                    exterior_results.append(_mock_exterior())
                elif img_type == "counter":
                    counter_results.append(_mock_counter())
                else:
                    shelf_results.append(_mock_shelf())

    # ── aggregate shelf ──
    def avg(lst, key, default=0):
        vals = [x.get(key, default) for x in lst if isinstance(x.get(key), (int, float))]
        return round(sum(vals) / len(vals), 1) if vals else default

    shelf_agg = {
        "shelf_density_index":  avg(shelf_results, "shelf_density_index", 50),
        "sku_diversity_score":  avg(shelf_results, "sku_diversity_score", 50),
        "fast_moving_ratio":    avg(shelf_results, "fast_moving_ratio",   50),
        "store_cleanliness":    avg(shelf_results, "store_cleanliness",   50),
        "inventory_value_band": _mode([x.get("inventory_value_band","medium") for x in shelf_results]),
        "refill_signal":        _mode([x.get("refill_signal","normal")        for x in shelf_results]),
        "product_categories":   list({c for x in shelf_results for c in x.get("product_categories", [])}),
        "visible_brands":       list({b for x in shelf_results for b in x.get("visible_brands", [])}),
    }

    exterior_agg = exterior_results[0] if exterior_results else _mock_exterior()
    counter_agg  = counter_results[0]  if counter_results  else _mock_counter()

    # ── multi-image consistency check ──
    consistency_score, consistency_flags = _consistency_check(
        shelf_results, exterior_results, counter_results
    )

    return {
        "shelf":              shelf_agg,
        "exterior":           exterior_agg,
        "counter":            counter_agg,
        "image_count":        len(image_paths),
        "consistency_score":  consistency_score,
        "consistency_flags":  consistency_flags,
    }


def _mode(lst):
    if not lst:
        return "unknown"
    return max(set(lst), key=lst.count)


def _consistency_check(shelves, exteriors, counters) -> tuple[float, list[str]]:
    """
    Cross-validate signals across images.
    Returns (score 0-1, list of flag strings).
    """
    flags  = []
    score  = 1.0
    deduct = 0.0

    # flag if only 1 image provided
    total = len(shelves) + len(exteriors) + len(counters)
    if total < 2:
        flags.append("limited_view_coverage")
        deduct += 0.15

    # flag if shelf images disagree strongly on density
    if len(shelves) >= 2:
        densities = [s.get("shelf_density_index", 50) for s in shelves]
        if max(densities) - min(densities) > 35:
            flags.append("shelf_density_inconsistency")
            deduct += 0.20

    # flag overstocked combined with low customer count
    refill_signals = [s.get("refill_signal") for s in shelves]
    if "overstocked" in refill_signals:
        cust = exteriors[0].get("visible_customer_count", 3) if exteriors else 3
        if cust <= 1:
            flags.append("overstocked_low_customer_count")
            deduct += 0.15

    # flag if all exterior images have same shadow angle (likely same photo)
    if len(exteriors) >= 2:
        angles = [e.get("shadow_angle_estimate") for e in exteriors
                  if e.get("shadow_angle_estimate") is not None]
        if len(angles) >= 2 and (max(angles) - min(angles)) < 5:
            flags.append("identical_shadow_angles_possible_duplicate")
            deduct += 0.10

    return round(max(0.0, score - deduct), 2), flags
