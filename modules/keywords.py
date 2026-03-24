from typing import Any, Dict, List

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
    return pd.read_csv(uploaded_file)


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
    options = ["ignore"] + list(TARGET_SCHEMA.keys())
    mapping: Dict[str, str] = {}
    cols = st.columns(2)

    for idx, column_name in enumerate(df.columns):
        with cols[idx % 2]:
            guessed = "ignore"
            lowered = column_name.strip().lower()
            if "keyword" in lowered or lowered in {"search term", "query", "term"}:
                guessed = "keyword"
            elif "click" in lowered:
                guessed = "clicks"
            elif "sale" in lowered or "order" in lowered:
                guessed = "sales"
            elif "conversion" in lowered or "cvr" in lowered:
                guessed = "conversion"
            elif "availability" in lowered or "competition" in lowered:
                guessed = "market_availability"
            elif lowered in {"cpc", "bid", "suggested bid", "cost per click"}:
                guessed = "cpc"

            mapping[column_name] = st.selectbox(
                f"{source_label}: {column_name}",
                options,
                index=options.index(guessed),
                key=f"map_{source_label}_{column_name}",
            )

    return mapping
