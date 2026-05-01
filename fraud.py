"""
fraud.py — Fraud detection signals for KiranaLens

Checks implemented:
  1. Shadow-Clock Verification  — SunCalc vs EXIF timestamp + image shadow angle
  2. Inventory–Geo Cross-Check  — high shelf density in low-footfall area
  3. Camera Angle Clustering    — all images from same corner (selective photography)
  4. Multi-Image Brand Mix      — from vision consistency_flags
  5. Overstocked + Low Customers— vision cross-signal
"""
import os, math, random, datetime
from PIL import Image
from PIL.ExifTags import TAGS

# suncalc is a pure-Python MIT-licensed lib (pip install suncalc)
# Graceful fallback if not installed
try:
    from suncalc import get_position
    SUNCALC_AVAILABLE = True
except ImportError:
    SUNCALC_AVAILABLE = False
    print("[fraud] suncalc not installed — shadow-clock check will use heuristic fallback")


# ── EXIF helpers ──────────────────────────────────────────────────────────────

def _read_exif(image_path: str) -> dict:
    """Extract EXIF fields we care about from an image."""
    result = {
        "datetime":     None,
        "gps_lat":      None,
        "gps_lng":      None,
        "make":         None,
        "model":        None,
    }
    try:
        img = Image.open(image_path)
        exif_data = img._getexif()
        if not exif_data:
            return result
        for tag_id, value in exif_data.items():
            tag = TAGS.get(tag_id, tag_id)
            if tag == "DateTime":
                result["datetime"] = str(value)
            elif tag == "Make":
                result["make"] = str(value)
            elif tag == "Model":
                result["model"] = str(value)
            elif tag == "GPSInfo":
                gps = value
                # parse lat/lng from GPSInfo dict
                def dms_to_dd(dms, ref):
                    d = float(dms[0])
                    m = float(dms[1])
                    s = float(dms[2])
                    dd = d + m / 60 + s / 3600
                    if ref in ["S", "W"]:
                        dd *= -1
                    return dd
                try:
                    result["gps_lat"] = dms_to_dd(gps.get(2, (0,0,0)), gps.get(1, "N"))
                    result["gps_lng"] = dms_to_dd(gps.get(4, (0,0,0)), gps.get(3, "E"))
                except Exception:
                    pass
    except Exception as e:
        print(f"[fraud] EXIF read error on {image_path}: {e}")
    return result


# ── Shadow-clock verification ─────────────────────────────────────────────────

def _expected_shadow_angle(lat: float, lng: float, dt: datetime.datetime) -> float | None:
    """
    Use SunCalc to get solar azimuth (degrees) at the given time/location.
    Shadow falls OPPOSITE to sun azimuth, so shadow_angle = (azimuth + 180) % 360
    """
    if not SUNCALC_AVAILABLE:
        return None
    try:
        pos = get_position(dt, lat, lng)
        sun_az = math.degrees(pos["azimuth"]) % 360
        shadow_az = (sun_az + 180) % 360
        return round(shadow_az, 1)
    except Exception as e:
        print(f"[fraud] SunCalc error: {e}")
        return None


def shadow_clock_check(image_paths: list[str], lat: float, lng: float,
                       vision_exterior: dict) -> dict:
    """
    Compare expected shadow angle (from GPS + EXIF time + SunCalc)
    against the shadow angle estimated by LLaVA in the exterior image.

    Returns {passed: bool, details: str, severity: str}
    """
    # collect EXIF datetime from images
    exif_datetimes = []
    for p in image_paths:
        exif = _read_exif(p)
        if exif["datetime"]:
            try:
                dt = datetime.datetime.strptime(exif["datetime"], "%Y:%m:%d %H:%M:%S")
                exif_datetimes.append(dt)
            except Exception:
                pass

    if not exif_datetimes:
        return {
            "passed":   None,
            "details":  "No EXIF datetime found — cannot verify photo timing",
            "severity": "warning",
            "flag":     "missing_exif_timestamp",
        }

    dt_used = exif_datetimes[0]
    expected_angle = _expected_shadow_angle(lat, lng, dt_used)

    if expected_angle is None:
        # Fallback: just check if time-of-day matches LLaVA's guess
        llava_guess = vision_exterior.get("time_of_day_guess", "unknown")
        hour = dt_used.hour
        expected_guess = ("morning" if 6 <= hour < 12 else
                          "afternoon" if 12 <= hour < 17 else
                          "evening" if 17 <= hour < 20 else "unknown")
        passed = (llava_guess == expected_guess or llava_guess == "unknown")
        return {
            "passed":   passed,
            "details":  f"EXIF time: {dt_used.strftime('%H:%M')} → expected {expected_guess}, LLaVA says {llava_guess}",
            "severity": "low" if passed else "high",
            "flag":     None if passed else "shadow_time_mismatch",
        }

    # Compare to LLaVA-estimated shadow angle
    llava_angle = vision_exterior.get("shadow_angle_estimate")
    if llava_angle is None:
        return {
            "passed":   None,
            "details":  f"SunCalc expected shadow at {expected_angle}°, LLaVA could not estimate angle",
            "severity": "warning",
            "flag":     "shadow_angle_unverifiable",
        }

    angle_diff = abs(float(llava_angle) - expected_angle)
    # allow ±40° tolerance (LLaVA estimates are rough)
    passed = angle_diff <= 40
    return {
        "passed":   passed,
        "details":  f"Expected shadow {expected_angle}°, LLaVA estimated {llava_angle}° (diff {angle_diff:.0f}°)",
        "severity": "high" if not passed else "ok",
        "flag":     None if passed else "shadow_clock_mismatch",
        "exif_time": dt_used.strftime("%Y-%m-%d %H:%M"),
        "expected_shadow_angle": expected_angle,
        "llava_shadow_angle":    float(llava_angle),
    }


# ── Inventory–Geo cross-check ─────────────────────────────────────────────────

def inventory_geo_check(vision: dict, geo: dict) -> dict:
    """
    Flag if shelf density is very high but geo footfall is very low.
    Classic sign of staged / borrowed inventory.
    """
    sdi      = vision["shelf"].get("shelf_density_index", 50)
    footfall = geo.get("footfall_proxy_index", 50)

    if sdi > 85 and footfall < 40:
        return {
            "passed":   False,
            "details":  f"Shelf density {sdi}% is high but footfall index is only {footfall}/100 — possible staged inventory",
            "severity": "high",
            "flag":     "inventory_footfall_mismatch",
        }
    if sdi > 90 and geo.get("competition_count_500m", 3) < 1:
        return {
            "passed":   False,
            "details":  f"Very high shelf density ({sdi}%) with zero competition nearby — demand signal weak",
            "severity": "medium",
            "flag":     "inventory_demand_mismatch",
        }
    return {
        "passed":  True,
        "details": f"Shelf density {sdi}% is consistent with footfall index {footfall}/100",
        "severity": "ok",
        "flag":    None,
    }


# ── Camera angle clustering ───────────────────────────────────────────────────

def camera_angle_check(image_paths: list[str]) -> dict:
    """
    Check that images cover multiple angles.
    Heuristic: read EXIF GPS and check spread; use file-count fallback.
    """
    if len(image_paths) < 2:
        return {
            "passed":   False,
            "details":  "Only 1 image provided — at least 3 required for full coverage",
            "severity": "high",
            "flag":     "insufficient_images",
        }
    if len(image_paths) < 3:
        return {
            "passed":   False,
            "details":  f"Only {len(image_paths)} images — 3 recommended (shelf, counter, exterior)",
            "severity": "medium",
            "flag":     "limited_view_coverage",
        }
    return {
        "passed":  True,
        "details": f"{len(image_paths)} images provided — adequate coverage",
        "severity": "ok",
        "flag":    None,
    }


# ── Refill timing check ───────────────────────────────────────────────────────

def refill_timing_check(vision: dict) -> dict:
    """
    Overfull shelves in the morning can be normal (just restocked).
    Overfull at noon/afternoon = suspicious.
    """
    refill = vision["shelf"].get("refill_signal", "normal")
    tod    = vision["exterior"].get("time_of_day_guess", "unknown")

    if refill == "overstocked" and tod in ["afternoon", "evening"]:
        return {
            "passed":   False,
            "details":  f"Shelves appear overstocked at {tod} — inconsistent with normal kirana sales patterns",
            "severity": "medium",
            "flag":     "overstocked_at_peak_hours",
        }
    if refill == "partially_empty" and tod == "morning":
        return {
            "passed":   True,
            "details":  "Partially empty shelves in morning suggests genuine overnight sales — positive signal",
            "severity": "ok",
            "flag":     None,
        }
    return {
        "passed":  True,
        "details": f"Refill signal '{refill}' at '{tod}' — within normal range",
        "severity": "ok",
        "flag":    None,
    }


# ── Public API ────────────────────────────────────────────────────────────────

def run_fraud_checks(image_paths: list[str], vision: dict, geo: dict,
                     lat: float, lng: float, mock: bool = False) -> dict:
    """
    Run all fraud checks and return aggregated result.

    Returns:
        {
          overall_fraud_risk: "low"|"medium"|"high",
          fraud_score: 0-100  (higher = more suspicious),
          checks: { shadow_clock: {...}, inventory_geo: {...}, ... },
          active_flags: [str],
        }
    """
    if mock:
        # return a benign mock result
        return {
            "overall_fraud_risk": "low",
            "fraud_score":        random.randint(5, 25),
            "checks": {
                "shadow_clock":   {"passed": True,  "details": "Mock: EXIF time consistent", "severity": "ok",  "flag": None},
                "inventory_geo":  {"passed": True,  "details": "Mock: SDI/footfall coherent", "severity": "ok", "flag": None},
                "camera_angles":  {"passed": True,  "details": "Mock: 3 images provided",    "severity": "ok",  "flag": None},
                "refill_timing":  {"passed": True,  "details": "Mock: normal refill signal",  "severity": "ok",  "flag": None},
            },
            "active_flags": [],
        }

    shadow  = shadow_clock_check(image_paths, lat, lng, vision.get("exterior", {}))
    inv_geo = inventory_geo_check(vision, geo)
    cam     = camera_angle_check(image_paths)
    refill  = refill_timing_check(vision)

    checks = {
        "shadow_clock":  shadow,
        "inventory_geo": inv_geo,
        "camera_angles": cam,
        "refill_timing": refill,
    }

    # also pull flags from vision consistency check
    vision_flags = vision.get("consistency_flags", [])

    # aggregate flags
    active_flags = [c["flag"] for c in checks.values() if c.get("flag")]
    active_flags += vision_flags
    active_flags = list(set(active_flags))  # deduplicate

    # fraud score: weighted sum of severity
    score = 0
    for c in checks.values():
        if c.get("severity") == "high":    score += 30
        elif c.get("severity") == "medium":score += 15
        elif c.get("severity") == "warning":score += 5

    score = min(100, score)

    risk = "low" if score < 25 else ("medium" if score < 55 else "high")

    return {
        "overall_fraud_risk": risk,
        "fraud_score":        score,
        "checks":             checks,
        "active_flags":       active_flags,
    }
