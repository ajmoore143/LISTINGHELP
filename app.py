import streamlit as st
import pandas as pd

from modules.research import run_research
from modules.pdf_research import parse_competitor_report
from modules.web_research import run_web_research
from modules.keywords import (
    DEFAULT_WEIGHTS,
    load_csv_file,
    preview_dataframe,
    render_mapping_editor,
    standardize_keyword_df,
    merge_keyword_sources,
    filter_brand_keywords,
    clean_keywords,
    deduplicate_keywords,
    score_keywords,
    assign_keyword_roles,
    build_keyword_plan,
    apply_conversion_threshold,
)
from modules.category_fit import apply_category_fit
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
        value=0.0,
        step=1.0,
        help="Optional hard floor on conversion. 0 disables it (recommended) so conversion is handled through smoothed scoring instead of cutting strong head terms at the threshold.",
    )
    extra_brands_raw = st.text_area(
        "Additional brand names to exclude",
        value="",
        height=80,
        help="One brand per line or comma-separated. Competitor brand names are removed from keyword targeting because Amazon prohibits using them in listing copy.",
    )

    st.divider()
    st.header("Category fit (AI)")
    use_category_fit = st.checkbox(
        "Filter off-category keywords with AI",
        value=True,
        help="Uses the model to drop keywords that belong to a different product type (herbicides, seeds, wrong NPK, etc.) and rank the rest by how well they fit this product.",
    )
    fit_cutoff = st.slider(
        "Minimum fit score",
        0.0,
        100.0,
        40.0,
        1.0,
        help="Candidate keywords scoring below this fit value are removed.",
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
if "competitor_report" not in st.session_state:
    st.session_state.competitor_report = None
if "web_notes" not in st.session_state:
    st.session_state.web_notes = ""
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
if "keyword_plan" not in st.session_state:
    st.session_state.keyword_plan = None


st.subheader("Step 0: Product Input")
st.caption("Enter the facts about the product. The app derives the buyer, angles, and positioning for you.")

col1, col2 = st.columns(2)
with col1:
    product_name = st.text_input("Product name", help="e.g. 10-10-10 All Purpose Granular Fertilizer")
    product_type = st.selectbox(
        "Product type",
        ["Fertilizer", "Soil mix", "Potting mix", "Soil amendment", "Mulch", "Other"],
    )
    brand = st.text_input("Brand (optional)")

with col2:
    form_size = st.text_input("Form & size", help="e.g. Granular, 1/2 quart bag")
    short_description = st.text_area("One-line description (optional)", height=110)

key_specs = st.text_area(
    "Key specs / attributes",
    height=120,
    help="Factual selling points, one per line: NPK ratio, made in USA, application areas, coverage, organic status, etc.",
)
optional_notes = st.text_area("Optional notes", height=90)

if st.button("Save Product Input"):
    missing_fields = []
    if not product_name.strip():
        missing_fields.append("Product name")
    if not product_type.strip():
        missing_fields.append("Product type")
    if not form_size.strip():
        missing_fields.append("Form & size")

    if missing_fields:
        st.error(f"Please fill in: {', '.join(missing_fields)}")
    else:
        st.session_state.product_input = {
            "product_name": product_name.strip(),
            "product_type": product_type.strip(),
            "brand": brand.strip(),
            "form_size": form_size.strip(),
            "short_description": short_description.strip(),
            "key_specs": key_specs.strip(),
            "notes": optional_notes.strip(),
        }
        st.success("Product input saved.")


if st.session_state.product_input:
    st.subheader("Step 1: Research")
    st.caption("The app pulls from any data you give it. A Listing Optimization AI report is best; if you don't have one, use the manual brief and/or live web research.")

    report_file = st.file_uploader(
        "Competitor research report (PDF) — best source if available",
        type=["pdf"],
    )

    manual_brief = st.text_area(
        "Manual brief (backup) — competitors you see + product context",
        height=140,
        help="Use this when no report exists. Describe likely competitors, how they position, and anything you know about the product and buyers.",
    )
    brief_file = st.file_uploader("...or upload a brief (.txt / .md)", type=["txt", "md"])

    run_web = st.checkbox(
        "Run live web research",
        value=True,
        help="Searches the web for real competitors, pricing, complaints, and trends. Works with or without a report.",
    )

    if st.button("Run Research"):
        report = None
        if report_file is not None:
            with st.spinner("Reading competitor report..."):
                try:
                    report = parse_competitor_report(report_file)
                except Exception as exc:  # noqa: BLE001
                    st.warning(f"Could not parse the report, continuing without it: {exc}")

        brief_text = manual_brief.strip()
        if brief_file is not None:
            try:
                brief_text = (brief_text + "\n\n" + brief_file.read().decode("utf-8", errors="ignore")).strip()
            except Exception as exc:  # noqa: BLE001
                st.warning(f"Could not read the brief file: {exc}")

        web_notes = ""
        if run_web:
            with st.spinner("Running live web research..."):
                try:
                    web_notes = run_web_research(st.session_state.product_input)
                except Exception as exc:  # noqa: BLE001
                    st.warning(f"Web research skipped: {exc}")

        st.session_state.competitor_report = report
        st.session_state.web_notes = web_notes

        with st.spinner("Synthesizing research..."):
            st.session_state.research_result = run_research(
                st.session_state.product_input,
                competitor_report=report,
                manual_brief=brief_text,
                web_notes=web_notes,
            )

        if report is None and not brief_text and not web_notes:
            st.info("Research generated from product facts only (no report, brief, or web data).")


if st.session_state.competitor_report:
    with st.expander("Parsed competitor report", expanded=False):
        st.json(st.session_state.competitor_report)

if st.session_state.web_notes:
    with st.expander("Live web research notes", expanded=False):
        st.markdown(st.session_state.web_notes)

if st.session_state.research_result:
    st.markdown("**Research result**")
    st.json(st.session_state.research_result)


if st.session_state.research_result:
    st.subheader("Step 2: Upload Keyword File")
    file = st.file_uploader("Upload Sellerise export (CSV or Excel)", type=["csv", "xlsx", "xlsm"])

    if file is not None:
        df = load_csv_file(file)
        preview_dataframe(df, "Preview")
        mapping = render_mapping_editor(df, "sellerise")

        if st.button("Run Keyword Pipeline"):
            extra_brands = [
                b.strip()
                for chunk in extra_brands_raw.replace(",", "\n").splitlines()
                for b in [chunk]
                if b.strip()
            ]
            standardized = standardize_keyword_df(df, "sellerise", mapping)
            merged = merge_keyword_sources([standardized])
            merged, removed_junk = clean_keywords(merged)
            merged, removed_brands = filter_brand_keywords(merged, extra_brands)
            merged = deduplicate_keywords(merged)
            filtered, applied_threshold = apply_conversion_threshold(merged, conversion_floor)
            scored = score_keywords(filtered, weights)

            dropped_fit = 0
            if use_category_fit:
                try:
                    scored, dropped_fit = apply_category_fit(
                        scored,
                        st.session_state.product_input,
                        st.session_state.research_result,
                        fit_cutoff=fit_cutoff,
                    )
                except Exception as exc:  # noqa: BLE001 - surface to user, keep pipeline alive
                    st.warning(f"Category-fit step skipped: {exc}")

            scored = assign_keyword_roles(scored)
            st.session_state.keyword_master_df = scored
            st.session_state.keyword_review_df = scored.head(40).copy()

            messages = []
            if len(removed_junk) > 0:
                messages.append(f"{len(removed_junk)} junk keyword(s) cleaned")
            if len(removed_brands) > 0:
                messages.append(f"{len(removed_brands)} brand keyword(s) removed")
            if dropped_fit > 0:
                messages.append(f"{dropped_fit} off-category keyword(s) dropped")
            if messages:
                st.info("Pre-scoring filters: " + "; ".join(messages) + ".")
            if conversion_floor > 0 and applied_threshold != conversion_floor:
                st.info(f"No keywords met {conversion_floor:.0f}% conversion, so the pipeline automatically retried at {applied_threshold:.0f}%.")


if st.session_state.keyword_master_df is not None:
    st.subheader("Step 3: Final Keyword Table")
    st.caption("Showing top 40 keywords only. Select the final keywords you want to use for listing generation.")

    display_columns = [
        col for col in [
            "selected",
            "keyword",
            "role",
            "clicks",
            "sales",
            "conversion",
            "market_availability",
            "relevance",
            "category_fit",
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
            "role": st.column_config.TextColumn("Placement", disabled=True),
            "clicks": st.column_config.NumberColumn("Clicks", disabled=True),
            "sales": st.column_config.NumberColumn("Sales", disabled=True),
            "conversion": st.column_config.NumberColumn("Conversion %", disabled=True, format="%.2f"),
            "market_availability": st.column_config.NumberColumn("Market Availability", disabled=True, format="%.2f"),
            "relevance": st.column_config.NumberColumn("Relevance", disabled=True, format="%.0f"),
            "category_fit": st.column_config.NumberColumn("Fit", disabled=True, format="%.0f"),
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
        keyword_plan = build_keyword_plan(
            st.session_state.keyword_master_df,
            st.session_state.selected_keywords,
        )
        st.session_state.keyword_plan = keyword_plan
        st.session_state.listing_output = generate_listing(
            st.session_state.product_input,
            st.session_state.research_result,
            keyword_plan,
        )


if st.session_state.listing_output:
    st.json(st.session_state.listing_output)

    backend_terms = ""
    if st.session_state.keyword_plan:
        backend_terms = st.session_state.keyword_plan.get("backend_terms", "")
        if backend_terms:
            st.subheader("Backend Search Terms")
            st.caption(f"Ready for Seller Central ({len(backend_terms.encode('utf-8'))} / 249 bytes).")
            st.code(backend_terms, language=None)

    export_text = export_listing_text(
        st.session_state.product_input,
        st.session_state.listing_output,
        backend_terms,
    )

    st.download_button("Download", export_text)
