"""
tests/smoke_test.py
Runs a full mock assessment end-to-end and validates the output shape.
Called by GitHub Actions CI — must exit 0.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from vision  import analyze_images
from geo     import get_geo_signals
from fraud   import run_fraud_checks
from scoring import compute_kcs_score

REQUIRED_OUTPUT_KEYS = [
    "daily_sales_range", "monthly_revenue_range", "monthly_income_range",
    "kcs_score", "kcs_band", "confidence_score", "confidence_90d",
    "confidence_180d", "recommendation", "max_loan_eligibility",
    "risk_flags", "fraud_risk", "sub_scores", "confidence_decay",
]

def run():
    print("Running KiranaLens smoke test (mock mode)...")
    vision = analyze_images([], mock=True)
    geo    = get_geo_signals(12.9716, 77.5946, mock=True)
    fraud  = run_fraud_checks([], vision, geo, 12.9716, 77.5946, mock=True)
    result = compute_kcs_score(vision, geo, fraud, {"rent": "8000", "shop_age": "5"})

    # validate keys
    for key in REQUIRED_OUTPUT_KEYS:
        assert key in result, f"FAIL: missing key '{key}'"

    # validate ranges
    assert 0   <= result["kcs_score"]        <= 850,  "FAIL: KCS out of range"
    assert 0.0 <= result["confidence_score"] <= 1.0,  "FAIL: confidence out of range"
    assert result["daily_sales_range"][0]    >  0,    "FAIL: daily lo must be > 0"
    assert result["daily_sales_range"][1]    > result["daily_sales_range"][0], "FAIL: range reversed"
    assert result["max_loan_eligibility"]    >  0,    "FAIL: loan eligibility must be > 0"

    print(f"  KCS Score         : {result['kcs_score']} ({result['kcs_band']})")
    print(f"  Daily Sales       : ₹{result['daily_sales_range'][0]:,} – ₹{result['daily_sales_range'][1]:,}")
    print(f"  Monthly Income    : ₹{result['monthly_income_range'][0]:,} – ₹{result['monthly_income_range'][1]:,}")
    print(f"  Confidence Now    : {result['confidence_score']}")
    print(f"  Confidence 90d    : {result['confidence_90d']}")
    print(f"  Recommendation    : {result['recommendation']}")
    print(f"  Max Loan          : ₹{result['max_loan_eligibility']:,}")
    print(f"  Risk Flags        : {result['risk_flags'] or 'None'}")
    print("\n✅ Smoke test passed.")

if __name__ == "__main__":
    run()
    sys.exit(0)
