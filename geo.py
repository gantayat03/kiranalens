"""
geo.py — Geo-intelligence signals using OpenStreetMap Overpass API (free, no key)

Queries:
  - ATM density            → micro-market wage proxy
  - Transit stops          → footfall proxy
  - Auto-rickshaw stands   → local disposable income proxy
  - Competing stores       → competition density
  - Road type              → street-level footfall estimate
  - Schools / offices      → catchment demand
"""
import math, random, requests
from functools import lru_cache

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"


# ── Overpass helpers ──────────────────────────────────────────────────────────

def _overpass_query(lat: float, lng: float, radius_m: int, filters: str) -> list[dict]:
    """Run a generic Overpass QL query and return list of elements."""
    query = f"""
    [out:json][timeout:20];
    (
      {filters}
    );
    out center;
    """
    # Replace {{lat}}, {{lng}}, {{r}} placeholders
    query = query.replace("{{lat}}", str(lat))\
                 .replace("{{lng}}", str(lng))\
                 .replace("{{r}}",   str(radius_m))
    try:
        resp = requests.post(OVERPASS_URL, data={"data": query}, timeout=25)
        resp.raise_for_status()
        return resp.json().get("elements", [])
    except Exception as e:
        print(f"[geo] Overpass error: {e}")
        return []


def _count_nearby(lat, lng, radius_m, tag_filter) -> int:
    """Count OSM nodes/ways matching a tag within radius."""
    filters = f'node[{tag_filter}](around:{radius_m},{{lat}},{{lng}});'
    filters = filters.replace("{lat}", str(lat)).replace("{lng}", str(lng))
    query = f'[out:json][timeout:15]; ({filters}); out count;'
    try:
        resp = requests.post(OVERPASS_URL, data={"data": query}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        # Overpass count response has a special element
        for el in data.get("elements", []):
            if el.get("type") == "count":
                return el.get("tags", {}).get("total", 0)
        return len(data.get("elements", []))
    except Exception as e:
        print(f"[geo] count error: {e}")
        return 0


def _reverse_geocode(lat, lng) -> dict:
    """Get road/area name from coordinates."""
    try:
        resp = requests.get(NOMINATIM_URL, params={
            "lat": lat, "lon": lng, "format": "json"
        }, headers={"User-Agent": "KiranaLens/1.0"}, timeout=10)
        return resp.json()
    except Exception:
        return {}


# ── scoring helpers ───────────────────────────────────────────────────────────

def _road_type_score(road_class: str) -> int:
    """Map OSM highway type to footfall score 0-100."""
    mapping = {
        "primary":     90, "secondary":  80, "tertiary":   65,
        "residential": 45, "service":    35, "footway":    30,
        "unclassified":40, "trunk":      85, "motorway":    5,
    }
    return mapping.get(road_class, 40)


def _normalize(value: float, lo: float, hi: float) -> float:
    """Clamp and scale value to 0-100."""
    return round(min(100, max(0, (value - lo) / (hi - lo) * 100)), 1)


# ── mock data ─────────────────────────────────────────────────────────────────

def _mock_geo(lat: float, lng: float) -> dict:
    """Return plausible-looking mock geo signals."""
    return {
        "catchment_density":    random.randint(40, 85),
        "footfall_proxy_index": random.randint(45, 80),
        "atm_count_500m":       random.randint(1, 6),
        "transit_count_500m":   random.randint(2, 10),
        "auto_stand_count_500m":random.randint(0, 4),
        "competition_count_500m":random.randint(2, 8),
        "competition_density":  random.randint(30, 70),
        "road_type":            random.choice(["lane","market_street","residential"]),
        "road_footfall_score":  random.randint(40, 70),
        "wage_proxy_score":     random.randint(40, 75),
        "area_name":            "Mock Locality, India",
        "mock":                 True,
    }


# ── public API ────────────────────────────────────────────────────────────────

def get_geo_signals(lat: float, lng: float, mock: bool = False) -> dict:
    """
    Return geo-intelligence signals for a GPS coordinate.

    Keys returned:
      catchment_density      (0-100) population proxy
      footfall_proxy_index   (0-100) road + POI based foot traffic
      atm_count_500m         raw count
      transit_count_500m     raw count
      auto_stand_count_500m  raw count
      competition_count_500m raw count
      competition_density    (0-100)
      road_type              string
      road_footfall_score    (0-100)
      wage_proxy_score       (0-100)  ATM + auto-stand composite
      area_name              string
    """
    if mock or (lat == 0.0 and lng == 0.0):
        return _mock_geo(lat, lng)

    try:
        # ── parallel counts (500 m radius) ──
        atm_count      = _count_nearby(lat, lng, 500, '"amenity"="atm"')
        bank_count     = _count_nearby(lat, lng, 500, '"amenity"="bank"')
        transit_count  = _count_nearby(lat, lng, 500, '"public_transport"="stop_position"')
        bus_count      = _count_nearby(lat, lng, 500, '"highway"="bus_stop"')
        school_count   = _count_nearby(lat, lng, 500, '"amenity"="school"')
        office_count   = _count_nearby(lat, lng, 300, '"building"="office"')
        kirana_count   = _count_nearby(lat, lng, 500, '"shop"~"convenience|grocery|supermarket"')
        auto_count     = _count_nearby(lat, lng, 300, '"amenity"="taxi"')  # OSM tag for auto stands

        # ── reverse geocode for road type ──
        geo_data  = _reverse_geocode(lat, lng)
        road_info = geo_data.get("address", {})
        road_type = road_info.get("road", "unclassified")

        # map OSM road name to class (heuristic)
        road_class = "residential"
        for keyword, cls in [("main", "secondary"), ("road", "tertiary"),
                              ("highway", "primary"), ("marg", "secondary"),
                              ("lane", "service"), ("nagar", "residential")]:
            if keyword in road_type.lower():
                road_class = cls
                break

        road_score = _road_type_score(road_class)

        # ── derived scores ──
        poi_score = min(100, (school_count * 8 + office_count * 10 +
                              transit_count * 6 + bus_count * 5))
        footfall_proxy = round((road_score * 0.5 + poi_score * 0.5), 1)

        # wage proxy: ATMs + banks + auto stands → disposable income proxy
        wage_raw   = atm_count * 12 + bank_count * 8 + auto_count * 5
        wage_score = _normalize(wage_raw, 0, 60)

        # competition: moderate = good (demand signal), high = margin pressure
        comp_score = _normalize(kirana_count, 0, 15)   # 0 → bad, 5 → ideal, 15+ → saturated

        # catchment density: POI + transport composite
        catchment = _normalize(
            school_count + office_count + transit_count + bus_count, 0, 25
        )

        area_name = (road_info.get("suburb") or
                     road_info.get("neighbourhood") or
                     road_info.get("city_district") or
                     "Unknown locality")

        return {
            "catchment_density":     round(catchment, 1),
            "footfall_proxy_index":  round(footfall_proxy, 1),
            "atm_count_500m":        atm_count,
            "transit_count_500m":    transit_count + bus_count,
            "auto_stand_count_500m": auto_count,
            "competition_count_500m":kirana_count,
            "competition_density":   round(comp_score, 1),
            "road_type":             road_class,
            "road_footfall_score":   road_score,
            "wage_proxy_score":      round(wage_score, 1),
            "area_name":             area_name,
            "mock":                  False,
        }

    except Exception as e:
        print(f"[geo] Pipeline error: {e}. Falling back to mock.")
        return _mock_geo(lat, lng)
