import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st

# Target schema the rest of the pipeline relies on.
TARGET_SCHEMA = {
    "keyword": "keyword",
    "clicks": "clicks",
    "sales": "sales",
    "conversion": "conversion",
    "market_availability": "market_availability",
    "cpc": "cpc",
    "relevance": "relevance",
}

# Default scoring weights.
# NOTE: cpc defaults to 0.0 because Sellerise Keyword Hunter Pro exports do NOT
# include a CPC column. The weight is kept only for backward compatibility with
# the existing sidebar slider; it stays inert unless a CPC column is supplied.
DEFAULT_WEIGHTS = {
    "clicks": 0.20,
    "sales": 0.25,
    "conversion": 0.18,
    "market_availability": 0.12,
    "cpc": 0.00,
    "relevance": 0.25,
}

# Metrics that are right-skewed and benefit from a log transform before scaling.
HEAVY_TAILED_METRICS = ["clicks", "sales", "relevance"]

# Number of keywords pre-selected after scoring.
TOP_N_PRESELECTED = 20

# Default keyword placement plan sizes.
DEFAULT_N_TITLE = 3
DEFAULT_N_BULLETS = 12
MAX_TITLE_WORDS = 6

# Keyword hygiene thresholds.
MIN_KEYWORD_CHARS = 2
MAX_KEYWORD_WORDS = 12

# Amazon backend search terms field byte budget.
BACKEND_BYTE_LIMIT = 249

# Words skipped when assembling backend search terms (Amazon ignores them, so
# they only waste the byte budget).
_BACKEND_STOPWORDS = {"a", "an", "and", "for", "in", "of", "the", "to", "with"}

# Competitor / third-party brand names to exclude from keyword targeting.
# Amazon prohibits using other brands' names in listing copy, so these are
# removed before scoring. The list is intentionally conservative (distinctive
# brand tokens only) to avoid false positives on generic words. Extend it via
# the `extra_brands` argument to filter_brand_keywords.
DEFAULT_BRAND_BLOCKLIST = [
    "scotts",
    "scott's",
    "miracle-gro",
    "miracle gro",
    "miraclegro",
    "osmocote",
    "espoma",
    "jobe",
    "jobes",
    "jobe's",
    "vigoro",
    "pennington",
    "schultz",
    "foxfarm",
    "fox farm",
    "milorganite",
    "ironite",
    "bonide",
    "lilly miller",
    "southern ag",
    "sta-green",
    "lesco",
    "neptune's harvest",
    "neptunes harvest",
    "happy frog",
    "dr earth",
    "dr. earth",
    "jacks classic",
    "jack's classic",
    "ortho",
    "roundup",
    "burpee",
    "advanced nutrients",
    "general hydroponics",
    "botanicare",
    "simple lawn solutions",
    "milorganite",
    "easy peasy",
    "the andersons",
]


# ----------------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------------
def load_csv_file(uploaded_file) -> pd.DataFrame:
    """
    Format-aware loader for keyword exports.

    Handles both Excel (.xlsx/.xlsm/.xls) and delimited text (.csv) files.
    Sellerise Keyword Hunter Pro exports are multi-sheet Excel workbooks; the
    sheet holding the per-keyword metrics (e.g. "Keyword Semantic Core") is
    selected automatically. CSV exports fall back to a multi-attempt parser
    that tries several encodings and delimiters.
    """
    name = (getattr(uploaded_file, "name", "") or "").lower()

    if name.endswith((".xlsx", ".xlsm", ".xls")):
        return _read_excel_keyword_sheet(uploaded_file)

    return _read_csv_robust(uploaded_file)


def _read_excel_keyword_sheet(uploaded_file) -> pd.DataFrame:
    """Read the most likely keyword sheet from a multi-sheet workbook."""
    uploaded_file.seek(0)
    sheets = pd.read_excel(uploaded_file, sheet_name=None)

    if not sheets:
        raise ValueError("The Excel file contains no readable sheets.")

    def has_keyword_column(frame: pd.DataFrame) -> bool:
        return any(str(c).strip().lower() == "keyword" for c in frame.columns)

    def sheet_rank(name_df: Tuple[str, pd.DataFrame]) -> Tuple[int, int, int]:
        sheet_name, frame = name_df
        lowered = sheet_name.lower()
        name_hint = 2 if ("semantic core" in lowered or "keyword" in lowered) else 0
        return (int(has_keyword_column(frame)), name_hint, len(frame))

    best_name, best_frame = max(sheets.items(), key=sheet_rank)

    if not has_keyword_column(best_frame):
        raise ValueError(
            "Could not find a sheet with a 'Keyword' column. "
            f"Sheets available: {', '.join(sheets.keys())}"
        )

    return best_frame


def _read_csv_robust(uploaded_file) -> pd.DataFrame:
    """Try several common CSV formats before failing."""
    attempts = [
        {"encoding": "utf-8-sig"},
        {"encoding": "utf-8-sig", "sep": ";"},
        {"encoding": "latin1"},
        {"encoding": "latin1", "sep": ";"},
        {"engine": "python", "sep": None},
    ]

    last_error = None
    for kwargs in attempts:
        try:
            uploaded_file.seek(0)
            return pd.read_csv(uploaded_file, **kwargs)
        except Exception as exc:  # noqa: BLE001 - we record and continue
            last_error = exc

    raise ValueError(f"Could not parse the file. Last parser error: {last_error}")


def preview_dataframe(df: pd.DataFrame, title: str) -> None:
    st.markdown(f"**{title}**")
    st.dataframe(df.head(10), use_container_width=True)


# ----------------------------------------------------------------------------
# Normalization helpers
# ----------------------------------------------------------------------------
def normalize_keyword_text(value: Any) -> str:
    return str(value).strip().lower()


def _to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0.0)


def robust_normalize(series: pd.Series, log_transform: bool = False) -> pd.Series:
    """
    Scale a metric to [0, 1] in a way that is resistant to outliers.

    Steps:
      1. Coerce to numeric, fill missing with 0.
      2. Winsorize at the 1st / 99th percentile to cap extreme outliers.
      3. Optionally apply log1p for heavy-tailed metrics.
      4. Min-max scale to [0, 1].

    A flat metric (all equal values) returns all zeros, contributing nothing.
    """
    numeric = _to_numeric(series).astype(float)

    if len(numeric) == 0:
        return pd.Series([], dtype=float)

    lower = numeric.quantile(0.01)
    upper = numeric.quantile(0.99)
    if upper > lower:
        numeric = numeric.clip(lower=lower, upper=upper)

    if log_transform:
        # Shift so the minimum is non-negative before log1p.
        shift = min(0.0, numeric.min())
        numeric = np.log1p(numeric - shift)

    min_val = numeric.min()
    max_val = numeric.max()
    if max_val == min_val:
        return pd.Series([0.0] * len(numeric), index=numeric.index)

    return (numeric - min_val) / (max_val - min_val)


def smooth_conversion(conversion: pd.Series, clicks: pd.Series) -> pd.Series:
    """
    Bayesian shrinkage of conversion toward a volume-weighted prior.

    Low-volume keywords (e.g. 1 click, 1 sale = 100% conversion) are noisy.
    Each keyword's conversion is pulled toward the dataset's overall conversion
    in proportion to how little click volume backs it up:

        smoothed = (clicks * conversion + K * prior) / (clicks + K)

    where the prior is the click-weighted mean conversion and K (the pseudo-
    count, in click units) is the median positive click volume. A keyword with
    far more clicks than K keeps its observed conversion; a keyword with almost
    no clicks collapses to the prior.
    """
    conv = _to_numeric(conversion).astype(float)
    clk = _to_numeric(clicks).astype(float).clip(lower=0.0)

    total_clicks = clk.sum()
    if total_clicks > 0:
        prior = float((clk * conv).sum() / total_clicks)
    else:
        prior = float(conv.mean()) if len(conv) else 0.0

    positive_clicks = clk[clk > 0]
    pseudo_count = float(positive_clicks.median()) if len(positive_clicks) else 1.0
    pseudo_count = max(pseudo_count, 1.0)

    return (clk * conv + pseudo_count * prior) / (clk + pseudo_count)


# ----------------------------------------------------------------------------
# Standardization and merging
# ----------------------------------------------------------------------------
def standardize_keyword_df(df: pd.DataFrame, source_name: str, mapping: Dict[str, str]) -> pd.DataFrame:
    mapped = {}
    for original_col, target_col in mapping.items():
        if target_col != "ignore" and original_col in df.columns:
            mapped[target_col] = df[original_col]

    standardized = pd.DataFrame(mapped)
    if "keyword" not in standardized.columns:
        raise ValueError(f"{source_name}: keyword column is required.")

    standardized["keyword"] = standardized["keyword"].map(normalize_keyword_text)
    standardized = standardized[standardized["keyword"] != ""].copy()

    for metric in ["clicks", "sales", "conversion", "market_availability", "cpc", "relevance"]:
        if metric not in standardized.columns:
            standardized[metric] = 0.0
        standardized[metric] = _to_numeric(standardized[metric])

    standardized["source"] = source_name
    return standardized[
        ["keyword", "clicks", "sales", "conversion", "market_availability", "cpc", "relevance", "source"]
    ]


def merge_keyword_sources(dataframes: List[pd.DataFrame]) -> pd.DataFrame:
    if not dataframes:
        raise ValueError("No standardized keyword dataframes were provided.")

    merged = pd.concat(dataframes, ignore_index=True)
    grouped = (
        merged.groupby("keyword", as_index=False)
        .agg(
            {
                "clicks": "max",
                "sales": "max",
                "conversion": "max",
                "market_availability": "max",
                "cpc": "max",
                "relevance": "max",
                "source": lambda x: ", ".join(sorted(set(x))),
            }
        )
        .copy()
    )
    return grouped


def apply_conversion_threshold(df: pd.DataFrame, preferred_threshold: float = 20.0) -> Tuple[pd.DataFrame, float]:
    """
    Optional hard floor on conversion, kept as an explicit user control.

    Setting the threshold to 0 disables the floor entirely (recommended), in
    which case conversion is handled purely through smoothed scoring. When a
    floor is set and nothing passes it, the pipeline falls back to 15%, then to
    no filter, so the pipeline never returns an empty table.
    """
    if preferred_threshold <= 0:
        return df.copy(), 0.0

    filtered = df[df["conversion"] >= preferred_threshold].copy()
    if not filtered.empty:
        return filtered, preferred_threshold

    fallback_threshold = 15.0
    filtered_fallback = df[df["conversion"] >= fallback_threshold].copy()
    if not filtered_fallback.empty:
        return filtered_fallback, fallback_threshold

    return df.copy(), 0.0


# ----------------------------------------------------------------------------
# Brand filtering
# ----------------------------------------------------------------------------
def _normalize_for_match(value: Any) -> str:
    """Lowercase and strip punctuation to plain words for brand matching."""
    text = str(value).lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def filter_brand_keywords(
    df: pd.DataFrame, extra_brands: Optional[List[str]] = None
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Remove keywords that contain a competitor / third-party brand name.

    Single-word brands match on a whole-word (token) basis to avoid partial
    hits; multi-word brand phrases match as a substring. Returns a tuple of
    (kept_df, removed_df) so the caller can report what was dropped.
    """
    brands = {b.strip().lower() for b in DEFAULT_BRAND_BLOCKLIST if b.strip()}
    if extra_brands:
        brands |= {b.strip().lower() for b in extra_brands if b and b.strip()}

    single_word = {b for b in brands if " " not in b}
    multi_word = {b for b in brands if " " in b}

    def is_brand(keyword: Any) -> bool:
        normalized = _normalize_for_match(keyword)
        if not normalized:
            return False
        tokens = set(normalized.split())
        if tokens & single_word:
            return True
        return any(phrase in normalized for phrase in multi_word)

    if "keyword" not in df.columns or df.empty:
        return df.copy(), df.iloc[0:0].copy()

    mask = df["keyword"].map(is_brand)
    removed = df[mask].copy()
    kept = df[~mask].copy()
    return kept, removed


# ----------------------------------------------------------------------------
# Keyword role assignment
# ----------------------------------------------------------------------------
def assign_keyword_roles(
    df: pd.DataFrame,
    n_title: int = DEFAULT_N_TITLE,
    n_bullets: int = DEFAULT_N_BULLETS,
    max_title_words: int = MAX_TITLE_WORDS,
) -> pd.DataFrame:
    """
    Tag each keyword with a listing placement role, ranked by score.

    Roles:
      - title   : the strongest, concise head terms (<= max_title_words words).
      - bullets : the next tier of strong keywords, integrated into bullets.
      - backend : everything else (long-tail / supporting search terms).

    The description is intentionally NOT a role: the listing step uses the full
    keyword set for the description, so every keyword still contributes to
    description coverage regardless of its role here.
    """
    work = df.copy().reset_index(drop=True)
    if "score" in work.columns:
        work = work.sort_values("score", ascending=False).reset_index(drop=True)

    word_counts = work["keyword"].astype(str).str.split().apply(len)

    roles: List[str] = []
    title_used = 0
    bullets_used = 0
    for i in range(len(work)):
        if title_used < n_title and word_counts.iloc[i] <= max_title_words:
            roles.append("title")
            title_used += 1
        elif bullets_used < n_bullets:
            roles.append("bullets")
            bullets_used += 1
        else:
            roles.append("backend")

    work["role"] = roles
    return work


def build_keyword_plan(scored_df: pd.DataFrame, selected_keywords: List[str]) -> Dict[str, List[str]]:
    """
    Group the user's selected keywords into a placement plan for listing copy.

    Returns:
      - title   : keywords to use in the titles.
      - bullets : keywords to integrate into the bullet points.
      - backend : long-tail keywords for backend search terms.
      - all     : every selected keyword, in score order. The description must
                  incorporate this complete set, not just the backend remainder.

    Empty title/bullet buckets are backfilled from the top of `all` so the
    listing step always has keywords to place.
    """
    normalized_selected = {normalize_keyword_text(k) for k in selected_keywords}
    subset = scored_df[scored_df["keyword"].isin(normalized_selected)].copy()
    if "score" in subset.columns:
        subset = subset.sort_values("score", ascending=False)

    if "role" not in subset.columns:
        subset = assign_keyword_roles(subset)

    all_keywords = subset["keyword"].tolist()
    title = subset.loc[subset["role"] == "title", "keyword"].tolist()
    bullets = subset.loc[subset["role"] == "bullets", "keyword"].tolist()
    backend = subset.loc[subset["role"] == "backend", "keyword"].tolist()

    if not title:
        title = all_keywords[:DEFAULT_N_TITLE]
    if not bullets:
        bullets = [k for k in all_keywords if k not in title][:DEFAULT_N_BULLETS]

    plan = {
        "title": title,
        "bullets": bullets,
        "backend": backend,
        "all": all_keywords,
    }
    plan["backend_terms"] = build_backend_search_terms(plan)
    return plan


# ----------------------------------------------------------------------------
# Keyword hygiene and deduplication
# ----------------------------------------------------------------------------
def clean_keywords(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Drop low-quality / junk keywords.

    Removes empties, keywords shorter than MIN_KEYWORD_CHARS, purely numeric or
    purely punctuation keywords, and abnormally long phrases (likely export
    noise). Returns (kept_df, removed_df).
    """
    if "keyword" not in df.columns or df.empty:
        return df.copy(), df.iloc[0:0].copy()

    def is_junk(keyword: Any) -> bool:
        text = str(keyword).strip()
        if len(text) < MIN_KEYWORD_CHARS:
            return True
        alnum = re.sub(r"[^a-z0-9]", "", text.lower())
        if not alnum:
            return True
        # Drop long pure-digit strings (barcodes / ASINs) but keep short numeric
        # patterns like NPK ratios (e.g. "10-10-10", "10 10 10").
        if alnum.isdigit() and len(alnum) >= 8:
            return True
        if len(text.split()) > MAX_KEYWORD_WORDS:
            return True
        return False

    mask = df["keyword"].map(is_junk)
    return df[~mask].copy(), df[mask].copy()


def _dedup_signature(keyword: Any) -> frozenset:
    """Order-independent token set, ignoring punctuation, for near-dup merging."""
    normalized = re.sub(r"[^a-z0-9]", " ", str(keyword).lower())
    return frozenset(t for t in normalized.split() if t)


def deduplicate_keywords(df: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse keywords that are punctuation / word-order variants of each other.

    Keywords with the same set of tokens (e.g. "10-10-10 fertilizer",
    "10 10 10 fertilizer", "fertilizer 10-10-10") are treated as one. The
    highest-scoring member is kept as the representative; sources are merged.
    Distinct keywords with different token sets (e.g. "plant fertilizer" vs
    "plant fertilizer outdoor") are left untouched.
    """
    if "keyword" not in df.columns or df.empty:
        return df.copy()

    work = df.copy()
    work["_sig"] = work["keyword"].map(_dedup_signature)
    sort_col = "score" if "score" in work.columns else "keyword"
    work = work.sort_values(sort_col, ascending=False)

    representatives = work.drop_duplicates(subset="_sig", keep="first").copy()

    if "source" in work.columns:
        merged_sources = (
            work.groupby("_sig")["source"]
            .apply(lambda s: ", ".join(sorted({part.strip() for val in s for part in str(val).split(",") if part.strip()})))
        )
        representatives["source"] = representatives["_sig"].map(merged_sources)

    representatives = representatives.drop(columns="_sig").reset_index(drop=True)
    return representatives


def build_backend_search_terms(keyword_plan: Dict[str, List[str]], byte_limit: int = BACKEND_BYTE_LIMIT) -> str:
    """
    Assemble an Amazon backend search-terms string from the keyword plan.

    Amazon indexes each unique word once, so the string holds de-duplicated
    individual words (not whole phrases), skips words already used in the title
    keywords, drops stopwords, and stays under the byte budget. The result is
    ready to paste into the Seller Central search-terms field.
    """
    title_words = set()
    for phrase in keyword_plan.get("title", []):
        title_words.update(re.sub(r"[^a-z0-9 ]", " ", str(phrase).lower()).split())

    ordered_sources = keyword_plan.get("backend", []) + keyword_plan.get("all", [])

    selected_words: List[str] = []
    seen = set()
    used_bytes = 0
    for phrase in ordered_sources:
        for word in re.sub(r"[^a-z0-9 ]", " ", str(phrase).lower()).split():
            if word in seen or word in title_words or word in _BACKEND_STOPWORDS:
                continue
            addition = (1 if selected_words else 0) + len(word.encode("utf-8"))
            if used_bytes + addition > byte_limit:
                continue
            selected_words.append(word)
            seen.add(word)
            used_bytes += addition

    return " ".join(selected_words)


# ----------------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------------
def score_keywords(df: pd.DataFrame, weights: Dict[str, float]) -> pd.DataFrame:
    """
    Compute a [0, 1] composite score per keyword and pre-select the top N.

    Component handling:
      - clicks, sales, relevance : log1p + winsorized min-max (heavy-tailed).
      - conversion               : Bayesian shrinkage by click volume, then scaled.
      - market_availability      : scaled directly (higher availability is better).
      - cpc                      : inverted (lower is better) only if data exists.
      - relevance                : uses the native Relevance column when present,
                                    otherwise falls back to keyword word count.
    """
    scored = df.copy()

    # Volume components.
    scored["clicks_norm"] = robust_normalize(scored["clicks"], log_transform=True)
    scored["sales_norm"] = robust_normalize(scored["sales"], log_transform=True)

    # Conversion: smooth first, then scale.
    scored["conversion_smoothed"] = smooth_conversion(scored["conversion"], scored["clicks"])
    scored["conversion_norm"] = robust_normalize(scored["conversion_smoothed"])

    # Market availability: higher is better (NOT inverted).
    scored["market_availability_norm"] = robust_normalize(scored["market_availability"])

    # CPC: only meaningful if a non-zero CPC column was supplied; lower is better.
    cpc_values = _to_numeric(scored["cpc"])
    if cpc_values.abs().sum() > 0:
        scored["cpc_norm"] = robust_normalize(cpc_values)
        cpc_component = 1.0 - scored["cpc_norm"]
    else:
        scored["cpc_norm"] = 0.0
        cpc_component = pd.Series([0.0] * len(scored), index=scored.index)

    # Relevance: prefer the native column; fall back to word count.
    relevance_values = _to_numeric(scored["relevance"])
    if relevance_values.abs().sum() > 0:
        scored["relevance_norm"] = robust_normalize(relevance_values, log_transform=True)
        scored["relevance_source"] = "native"
    else:
        scored["word_count"] = scored["keyword"].apply(lambda x: len(str(x).split()))
        scored["relevance_norm"] = robust_normalize(scored["word_count"])
        scored["relevance_source"] = "word_count"

    scored["score"] = (
        weights.get("clicks", 0.0) * scored["clicks_norm"]
        + weights.get("sales", 0.0) * scored["sales_norm"]
        + weights.get("conversion", 0.0) * scored["conversion_norm"]
        + weights.get("market_availability", 0.0) * scored["market_availability_norm"]
        + weights.get("cpc", 0.0) * cpc_component
        + weights.get("relevance", 0.0) * scored["relevance_norm"]
    )

    scored = scored.sort_values("score", ascending=False).reset_index(drop=True)
    scored.insert(0, "selected", False)
    if len(scored) > 0:
        scored.loc[: min(TOP_N_PRESELECTED - 1, len(scored) - 1), "selected"] = True
    return scored


# ----------------------------------------------------------------------------
# Column mapping UI
# ----------------------------------------------------------------------------
def render_mapping_editor(df: pd.DataFrame, source_label: str) -> Dict[str, str]:
    st.markdown(f"**Column mapping: {source_label}**")
    st.caption("Choose only the source columns you actually want to use in scoring. Everything else stays ignored.")

    columns = list(df.columns)

    def guess_column(target: str) -> str:
        for col in columns:
            lowered = col.strip().lower()

            if target == "keyword" and ("keyword" in lowered or lowered in {"search term", "query", "term"}):
                return col
            if target == "clicks" and lowered == "clicks":
                return col
            if target == "sales" and lowered == "sales":
                return col
            if target == "conversion" and ("conversion" in lowered or lowered == "cvr"):
                return col
            if target == "relevance" and lowered == "relevance":
                return col
            if target == "market_availability" and ("market availability" in lowered or "availability" in lowered):
                return col
            if target == "cpc" and lowered in {"cpc", "bid", "suggested bid", "cost per click"}:
                return col

        # Looser fallbacks, guarding against trend/position/derived columns.
        for col in columns:
            lowered = col.strip().lower()
            if target == "clicks" and "click" in lowered and not any(
                token in lowered for token in ["trend", "pos", "other"]
            ):
                return col
            if target == "sales" and "sale" in lowered and not any(
                token in lowered for token in ["trend", "pos", "daily", "other"]
            ):
                return col

        return "ignore"

    options = ["ignore"] + columns
    target_fields = ["keyword", "clicks", "sales", "conversion", "relevance", "market_availability", "cpc"]

    mapping: Dict[str, str] = {col: "ignore" for col in columns}

    left, right = st.columns(2)
    for idx, target in enumerate(target_fields):
        guessed = guess_column(target)
        with (left if idx % 2 == 0 else right):
            selected_source = st.selectbox(
                f"Map to {target}",
                options,
                index=options.index(guessed) if guessed in options else 0,
                key=f"map_{source_label}_{target}",
            )
            if selected_source != "ignore":
                mapping[selected_source] = target

    return mapping
