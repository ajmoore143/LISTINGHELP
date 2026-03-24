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


st.subheader("Step 0: Product Input")
product_name = st.text_input("Product name")
category = st.text_input("Category")
target_customer = st.text_input("Target customer")
short_description = st.text_area("Short description")

if st.button("Save Product Input"):
    st.session_state.product_input = {
        "product_name": product_name,
        "category": category,
        "target_customer": target_customer,
        "short_description": short_description,
    }


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
            scored = score_keywords(merged, weights)
            st.session_state.keyword_master_df = scored


if st.session_state.keyword_master_df is not None:
    st.dataframe(st.session_state.keyword_master_df)

    selected = st.session_state.keyword_master_df.head(20)["keyword"].tolist()
    st.session_state.selected_keywords = selected


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
