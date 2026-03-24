import streamlit as st
import pandas as pd

from modules.research import run_research
from modules.keywords import (
    DEFAULT_WEIGHTS,
    load_csv_file,
    preview_dataframe,
    render_mapping_editor,
    standardize_keyword_df,
    merge_keyword_sources,
    score_keywords,
    apply_conversion_threshold,
)
from modules.listing import generate_listing, export_listing_text


st.set_page_config(page_title="Amazon Listing AI", layout="wide")
st.title("Amazon Listing AI Pipeline")
st.caption("Research -> CSV standardization -> keyword scoring -> listing generation")


with st.sidebar:
    st.header("API status")
    if st.secrets.get("OPENAI_API_KEY", ""):
        st.success("OpenAI key detected in Streamlit secrets")
    else:
        st.warning("No key in Streamlit secrets. Local env var may still work.")

    st.divider()
    st.header("Keyword score weights")
    w_clicks = st.slider("Clicks", 0.0, 1.0, float(DEFAULT_WEIGHTS["clicks"]), 0.01)
    w_sales = st.slider("Sales", 0.0, 1.0, float(DEFAULT_WEIGHTS["sales"]), 0.01)
    w_conversion = st.slider("Conversion", 0.0, 1.0, float(DEFAULT_WEIGHTS["conversion"]), 0.01)
    w_market = st.slider("Market availability", 0.0, 1.0, float(DEFAULT_WEIGHTS["market_availability"]), 0.01)
    w_cpc = st.slider("CPC efficiency", 0.0, 1.0, float(DEFAULT_WEIGHTS["cpc"]), 0.01)
    w_relevance = st.slider("Relevance / specificity", 0.0, 1.0, float(DEFAULT_WEIGHTS["relevance"]), 0.01)

    st.divider()
    st.header("Keyword filtering")
    conversion_floor = st.number_input(
        "Minimum conversion %",
        min_value=0.0,
        max_value=100.0,
        value=20.0,
        step=1.0,
        help="Keywords below this conversion threshold will be removed before scoring. If none remain, the pipeline will automatically retry at 15%.",
    )

weights = {
    "clicks": w_clicks,
    "sales": w_sales,
    "conversion": w_conversion,
    "market_availability": w_market,
    "cpc": w_cpc,
    "relevance": w_relevance,
}


if "product_input" not in st.session_state:
    st.session_state.product_input = None
if "research_result" not in st.session_state:
    st.session_state.research_result = None
if "keyword_master_df" not in st.session_state:
    st.session_state.keyword_master_df = None
if "selected_keywords" not in st.session_state:
    st.session_state.selected_keywords = []
if "listing_output" not in st.session_state:
    st.session_state.listing_output = None
if "keyword_review_df" not in st.session_state:
    st.session_state.keyword_review_df = None


st.subheader("Step 0: Product Input")

col1, col2 = st.columns(2)
with col1:
    product_name = st.text_input("Product name")
    category = st.text_input("Category")
    target_customer = st.text_input("Target customer")

with col2:
    short_description = st.text_area("Short description", height=140)
    competitor_info = st.text_area("Competitor info (optional)", height=140)

optional_notes = st.text_area("Optional notes", height=120)

if st.button("Save Product Input"):
    missing_fields = []
    if not product_name.strip():
        missing_fields.append("Product name")
    if not category.strip():
        missing_fields.append("Category")
    if not target_customer.strip():
        missing_fields.append("Target customer")
    if not short_description.strip():
        missing_fields.append("Short description")

    if missing_fields:
        st.error(f"Please fill in: {', '.join(missing_fields)}")
    else:
        st.session_state.product_input = {
            "product_name": product_name.strip(),
            "category": category.strip(),
            "target_customer": target_customer.strip(),
            "short_description": short_description.strip(),
            "competitor_info": competitor_info.strip(),
            "optional_notes": optional_notes.strip(),
        }
        st.success("Product input saved.")


if st.session_state.product_input:
    st.subheader("Step 1: Research")
    if st.button("Run Research"):
        st.session_state.research_result = run_research(st.session_state.product_input)


if st.session_state.research_result:
    st.json(st.session_state.research_result)


if st.session_state.research_result:
    st.subheader("Step 2: Upload CSV")
    file = st.file_uploader("Upload Sellerise CSV", type=["csv"])

    if file is not None:
        df = load_csv_file(file)
        preview_dataframe(df, "Preview")
        mapping = render_mapping_editor(df, "sellerise")

        if st.button("Run Keyword Pipeline"):
            standardized = standardize_keyword_df(df, "sellerise", mapping)
            merged = merge_keyword_sources([standardized])
            filtered, applied_threshold = apply_conversion_threshold(merged, conversion_floor)
            scored = score_keywords(filtered, weights)
            st.session_state.keyword_master_df = scored
            st.session_state.keyword_review_df = scored.head(40).copy()
            if applied_threshold != conversion_floor:
                st.info(f"No keywords met {conversion_floor:.0f}% conversion, so the pipeline automatically retried at {applied_threshold:.0f}%.")


if st.session_state.keyword_master_df is not None:
    st.subheader("Step 3: Final Keyword Table")
    st.caption("Showing top 40 keywords only. Select the final keywords you want to use for listing generation.")

    display_columns = [
        col for col in [
            "selected",
            "keyword",
            "clicks",
            "sales",
            "conversion",
            "market_availability",
            "cpc",
            "score",
            "source",
        ]
        if col in st.session_state.keyword_review_df.columns
    ]

    edited_df = st.data_editor(
        st.session_state.keyword_review_df[display_columns],
        use_container_width=True,
        num_rows="fixed",
        hide_index=True,
        key="keyword_review_editor",
        column_config={
            "selected": st.column_config.CheckboxColumn("Selected"),
            "keyword": st.column_config.TextColumn("Keyword", disabled=True),
            "clicks": st.column_config.NumberColumn("Clicks", disabled=True),
            "sales": st.column_config.NumberColumn("Sales", disabled=True),
            "conversion": st.column_config.NumberColumn("Conversion %", disabled=True, format="%.2f"),
            "market_availability": st.column_config.NumberColumn("Market Availability", disabled=True, format="%.2f"),
            "cpc": st.column_config.NumberColumn("CPC", disabled=True, format="%.2f"),
            "score": st.column_config.NumberColumn("Score", disabled=True, format="%.3f"),
            "source": st.column_config.TextColumn("Source", disabled=True),
        },
    )

    st.session_state.keyword_review_df = edited_df
    st.session_state.selected_keywords = edited_df.loc[edited_df["selected"] == True, "keyword"].tolist()

    c1, c2 = st.columns(2)
    with c1:
        st.metric("Visible keywords", len(edited_df))
    with c2:
        st.metric("Selected keywords", len(st.session_state.selected_keywords))

    with st.expander("View selected keywords", expanded=False):
        st.write(st.session_state.selected_keywords)


if st.session_state.selected_keywords:
    st.subheader("Step 4: Generate Listing")
    if st.button("Generate Listing"):
        st.session_state.listing_output = generate_listing(
            st.session_state.product_input,
            st.session_state.research_result,
            st.session_state.selected_keywords,
        )


if st.session_state.listing_output:
    st.json(st.session_state.listing_output)

    export_text = export_listing_text(
        st.session_state.product_input,
        st.session_state.listing_output,
    )

    st.download_button("Download", export_text)
