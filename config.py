"""
Central configuration. These settings are intentionally kept out of the UI so
hired workers cannot change them by accident. The owner edits this file to tune
models or scoring; nothing here is exposed in the app.
"""

# --- Models (Anthropic) -----------------------------------------------------
# Tiered on purpose: Opus for the steps where reasoning/document quality decides
# the outcome, Sonnet for writing and web research, Haiku for cheap bulk scoring.
MODEL_PDF_RESEARCH = "claude-opus-4-8"            # parse competitor report (vision + JSON)
MODEL_WEB_RESEARCH = "claude-sonnet-4-6"          # live web research (web search tool)
MODEL_RESEARCH = "claude-opus-4-8"                # research synthesis (deep reasoning)
MODEL_CATEGORY_FIT = "claude-haiku-4-5-20251001"  # batched keyword fit scoring
MODEL_LISTING = "claude-sonnet-4-6"               # listing copywriting

# Per-stage output budgets.
MAX_TOKENS_PDF_RESEARCH = 8000
MAX_TOKENS_WEB_RESEARCH = 6000
MAX_TOKENS_RESEARCH = 8000
MAX_TOKENS_CATEGORY_FIT = 8000
MAX_TOKENS_LISTING = 8000

# --- Keyword scoring weights (locked) --------------------------------------
# Derived from analysis of the real Sellerise export. cpc stays 0 because that
# column is not present in the export; conversion is handled through smoothed
# scoring, so no hard floor is applied.
KEYWORD_WEIGHTS = {
    "clicks": 0.20,
    "sales": 0.25,
    "conversion": 0.18,
    "market_availability": 0.12,
    "cpc": 0.00,
    "relevance": 0.25,
}
CONVERSION_FLOOR = 0.0          # 0 = disabled (recommended)
CATEGORY_FIT_ENABLED = True     # AI off-category keyword filter
CATEGORY_FIT_CUTOFF = 40.0      # drop candidates below this fit score

# Extra competitor brand names to always exclude from keyword targeting, on top
# of the built-in blocklist in modules/keywords.py.
EXTRA_BRAND_EXCLUSIONS = []
