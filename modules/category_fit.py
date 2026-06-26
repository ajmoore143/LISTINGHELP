from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

import config
from modules import llm

DEFAULT_CANDIDATE_LIMIT = 200

CATEGORY_FIT_SYSTEM_PROMPT = """
ROLE
You are an Amazon catalog relevance analyst.

OBJECTIVE
Given one product and a list of candidate search keywords, rate how well each
keyword fits THIS SPECIFIC product. The question is: would a shopper typing that
keyword be looking for this exact product type?

SCORING (0-100)
- 90-100: directly describes this product or its core use.
- 60-89 : clearly relevant; a natural search for this product.
- 40-59 : loosely related but broad or partial fit.
- 1-39  : wrong product TYPE, wrong use, or contradictory specs. Examples for a
          fertilizer: herbicides, pesticides, insecticides, fungicides, weed
          killers, weed-and-feed, grass seed, soil, mulch, tools, or a different
          nutrient ratio than the product.
- 0     : unrelated.

RULES
- Be STRICT about product type and use. Be LENIENT about phrasing, word order,
  and singular/plural.
- Judge fit to the actual product described, not to the general category.
- If a keyword names a specific specification (e.g. an NPK ratio) that conflicts
  with the product, score it low.

OUTPUT
Return STRICT JSON only:
{ "results": [ { "keyword": "...", "fit": 0-100 } ] }
Return one entry per input keyword, using the exact keyword strings provided.
Do not include any text outside the JSON.
""".strip()


def _fit_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string"},
                        "fit": {"type": "number"},
                    },
                    "required": ["keyword", "fit"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["results"],
        "additionalProperties": False,
    }


def _score_fit(
    keywords: List[str],
    product_input: Dict[str, Any],
    research_result: Optional[Dict[str, Any]],
) -> Dict[str, float]:
    """Send one batched request and return a {keyword: fit} map."""
    import json

    research_context = {}
    if research_result:
        for key in ("use_cases", "strengths"):
            if key in research_result:
                research_context[key] = research_result[key]

    user_prompt = f"""
Product:
{json.dumps(product_input, indent=2)}

Research context:
{json.dumps(research_context, indent=2)}

Candidate keywords to rate:
{json.dumps(keywords, indent=2)}

Rate every keyword's fit to this exact product. Return JSON only.
""".strip()

    parsed = llm.complete_json(
        config.MODEL_CATEGORY_FIT,
        CATEGORY_FIT_SYSTEM_PROMPT,
        user_prompt,
        _fit_schema(),
        max_tokens=config.MAX_TOKENS_CATEGORY_FIT,
    )

    fit_map: Dict[str, float] = {}
    for item in parsed.get("results", []):
        keyword = str(item.get("keyword", "")).strip().lower()
        if keyword:
            try:
                fit_map[keyword] = float(item.get("fit", 0.0))
            except (TypeError, ValueError):
                fit_map[keyword] = 0.0
    return fit_map


def apply_category_fit(
    df: pd.DataFrame,
    product_input: Dict[str, Any],
    research_result: Optional[Dict[str, Any]] = None,
    fit_cutoff: float = config.CATEGORY_FIT_CUTOFF,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> Tuple[pd.DataFrame, int]:
    """
    Score the top candidates for fit to the product, drop off-category keywords,
    and blend fit into the score so better-fitting keywords rank higher.

    Only the top `candidate_limit` keywords by score are sent to the model
    (the final listing needs far fewer than that), which keeps the call to a
    single efficient request. Keywords outside that window keep their base
    score and a missing fit value. Returns (filtered_df, dropped_count).
    """
    if df.empty or "keyword" not in df.columns:
        return df.copy(), 0

    ranked = df.sort_values("score", ascending=False) if "score" in df.columns else df
    candidates = ranked.head(candidate_limit)["keyword"].astype(str).tolist()
    if not candidates:
        return df.copy(), 0

    fit_map = _score_fit(candidates, product_input, research_result)

    out = df.copy()
    out["category_fit"] = out["keyword"].map(fit_map)

    drop_mask = out["category_fit"].notna() & (out["category_fit"] < fit_cutoff)
    dropped_count = int(drop_mask.sum())
    out = out[~drop_mask].copy()

    if "score" in out.columns:
        has_fit = out["category_fit"].notna()
        out.loc[has_fit, "score"] = out.loc[has_fit, "score"] * (out.loc[has_fit, "category_fit"] / 100.0)
        out = out.sort_values("score", ascending=False).reset_index(drop=True)

    return out, dropped_count
