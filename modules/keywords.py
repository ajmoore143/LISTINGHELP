from typing import Any, Dict, List, Tuple

import pandas as pd
import streamlit as st

TARGET_SCHEMA = {
    "keyword": "keyword",
    "clicks": "clicks",
    "sales": "sales",
    "conversion": "conversion",
    "market_availability": "market_availability",
    "cpc": "cpc",
    "relevance": "relevance",
}

DEFAULT_WEIGHTS = {
    "clicks": 0.15,
    "sales": 0.30,
    "conversion": 0.20,
    "market_availability": 0.10,
    "cpc": 0.10,
    "relevance": 0.15,
}


def load_csv_file(uploaded_file) -> pd.DataFrame:
    """
    More robust CSV loader for exports from Sellerise / Amazon tools.
    Tries multiple common formats before failing.
    """
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
        except Exception as e:
            last_error = e

    raise ValueError(f"Could not parse CSV file. Last parser error: {last_error}")


def preview_dataframe(df: pd.DataFrame, title: str) -> None:
    st.markdown(f"**{title}**")
    st.dataframe(df.head(10), use_container_width=True)


def normalize_keyword_text(value: Any) -> str:
    return str(value).strip().lower()


def min_max_normalize(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce").fillna(0)
    min_val = numeric.min()
    max_val = numeric.max()
    if max_val == min_val:
        return pd.Series([0.0] * len(numeric), index=numeric.index)
    return (numeric - min_val) / (max_val - min_val)


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

    for metric in ["clicks", "sales", "conversion", "market_availability", "cpc"]:
        if metric not in standardized.columns:
            standardized[metric] = 0.0
        standardized[metric] = pd.to_numeric(standardized[metric], errors="coerce").fillna(0.0)

    standardized["source"] = source_name
    return standardized[["keyword", "clicks", "sales", "conversion", "market_availability", "cpc", "source"]]


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
                "source": lambda x: ", ".join(sorted(set(x))),
            }
        )
        .copy()
    )
    return grouped

def apply_conversion_threshold(df: pd.DataFrame, preferred_threshold: float = 20.0) -> Tuple[pd.DataFrame, float]:
    filtered_20 = df[df["conversion"] >= preferred_threshold].copy()
    if not filtered_20.empty:
        return filtered_20, preferred_threshold

    fallback_threshold = 15.0
    filtered_15 = df[df["conversion"] >= fallback_threshold].copy()
    if not filtered_15.empty:
        return filtered_15, fallback_threshold

    return df.copy(), 0.0

def score_keywords(df: pd.DataFrame, weights: Dict[str, float]) -> pd.DataFrame:
    scored = df.copy()
    scored["clicks_norm"] = min_max_normalize(scored["clicks"])
    scored["sales_norm"] = min_max_normalize(scored["sales"])
    scored["conversion_norm"] = min_max_normalize(scored["conversion"])
    scored["market_availability_norm"] = min_max_normalize(scored["market_availability"])
    scored["cpc_norm"] = min_max_normalize(scored["cpc"])

    scored["word_count"] = scored["keyword"].apply(lambda x: len(str(x).split()))
    scored["relevance_norm"] = min_max_normalize(scored["word_count"])

    scored["score"] = (
        weights["clicks"] * scored["clicks_norm"]
        + weights["sales"] * scored["sales_norm"]
        + weights["conversion"] * scored["conversion_norm"]
        + weights["market_availability"] * (1 - scored["market_availability_norm"])
        + weights["cpc"] * (1 - scored["cpc_norm"])
        + weights["relevance"] * scored["relevance_norm"]
    )

    scored = scored.sort_values("score", ascending=False).reset_index(drop=True)
    scored.insert(0, "selected", False)
    scored.loc[: min(19, len(scored) - 1), "selected"] = True
    return scored


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
            if target == "market_availability" and ("market availability" in lowered or "availability" in lowered):
                return col
            if target == "cpc" and lowered in {"cpc", "bid", "suggested bid", "cost per click"}:
                return col

        for col in columns:
            lowered = col.strip().lower()
            if target == "clicks" and "click" in lowered and "trend" not in lowered and "pos" not in lowered and "other" not in lowered:
                return col
            if target == "sales" and "sale" in lowered and "trend" not in lowered and "pos" not in lowered and "daily" not in lowered and "other" not in lowered:
                return col

        return "ignore"

    options = ["ignore"] + columns
    target_fields = ["keyword", "clicks", "sales", "conversion", "market_availability", "cpc"]

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
