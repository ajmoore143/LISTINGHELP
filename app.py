from pathlib import Path

import streamlit as st
import pandas as pd

import config
from modules.research import run_research
from modules.pdf_research import parse_competitor_report
from modules.web_research import run_web_research
from modules.keywords import (
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
from modules.flatfile import build_flat_file, build_family_flat_file
from modules import storage


st.set_page_config(page_title="Amazon Listing AI", layout="wide")

# ---------------------------------------------------------------------------
# Login gate. Active only when an [auth] block is configured in secrets
# (Google OIDC). Without it the app runs open, so local/dev use is unaffected.
# ---------------------------------------------------------------------------
AUTH_ENABLED = "auth" in st.secrets
if AUTH_ENABLED and not st.user.is_logged_in:
    st.title("Amazon Listing AI Pipeline")
    st.write("Please sign in to access the app and your saved projects.")
    if st.button("Log in with Google", type="primary"):
        st.login()
    st.stop()

USER_EMAIL = st.user.email if (AUTH_ENABLED and st.user.is_logged_in) else "local"


def autosave():
    """Persist the current project automatically (project name = product name)."""
    name = (st.session_state.get("current_project_name") or "").strip()
    if storage.is_enabled() and name:
        try:
            storage.save_project(USER_EMAIL, name, st.session_state)
        except Exception:
            pass  # never block the pipeline on a transient save error


st.title("Amazon Listing AI Pipeline")
st.caption("Product facts -> research -> keywords -> listing -> flat file")


with st.sidebar:
    if AUTH_ENABLED:
        st.caption(f"Signed in as {USER_EMAIL}")
        if st.button("Log out"):
            st.logout()

    st.header("My Projects")
    current = (st.session_state.get("current_project_name") or "").strip()
    if current:
        st.caption(f"Current project: **{current}** — saved automatically")

    if not storage.is_enabled():
        st.caption("Saving is off (no Supabase secrets configured).")
    else:
        projects = storage.list_projects(USER_EMAIL)
        if projects:
            labels = {p["name"]: p["id"] for p in projects}
            chosen = st.selectbox("Open a saved project", ["—"] + list(labels.keys()))
            cols = st.columns(2)
            with cols[0]:
                if st.button("Load", disabled=(chosen == "—")):
                    rec = storage.load_project(labels[chosen])
                    if rec:
                        storage.apply_state(st.session_state, rec.get("state", {}))
                        st.session_state.current_project_name = chosen
                        st.success(f"Loaded '{chosen}'.")
                        st.rerun()
            with cols[1]:
                if st.button("Delete", disabled=(chosen == "—")):
                    storage.delete_project(labels[chosen])
                    if st.session_state.get("current_project_name") == chosen:
                        st.session_state.current_project_name = ""
                    st.success(f"Deleted '{chosen}'.")
                    st.rerun()
        else:
            st.caption("No saved projects yet. Save Product Input to create one.")

    if not st.secrets.get("ANTHROPIC_API_KEY", ""):
        st.divider()
        st.warning("No ANTHROPIC_API_KEY configured — AI steps will not run.")


# Pipeline settings are locked in config.py and intentionally not shown in the
# UI so they cannot be changed by accident.
weights = config.KEYWORD_WEIGHTS
conversion_floor = config.CONVERSION_FLOOR
extra_brands = list(config.EXTRA_BRAND_EXCLUSIONS)
use_category_fit = config.CATEGORY_FIT_ENABLED
fit_cutoff = config.CATEGORY_FIT_CUTOFF


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
if "flatfile_bytes" not in st.session_state:
    st.session_state.flatfile_bytes = None
if "flatfile_report" not in st.session_state:
    st.session_state.flatfile_report = None
if "current_project_name" not in st.session_state:
    st.session_state.current_project_name = ""


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
        # The project is created/named from the product name and saved
        # automatically, so the worker never manages project names by hand.
        st.session_state.current_project_name = product_name.strip()
        autosave()
        if storage.is_enabled():
            st.success(f"Product input saved. Project '{product_name.strip()}' is now saved automatically.")
        else:
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

    st.caption("Research usually takes about 1-3 minutes (deep reasoning, plus web search if enabled). "
               "The status box below shows progress — it is working even while it looks idle.")

    if st.button("Run Research"):
        with st.status("Running research...", expanded=True) as status:
            report = None
            if report_file is not None:
                status.update(label="Reading competitor report...")
                st.write("Reading competitor report (PDF)...")
                try:
                    report = parse_competitor_report(report_file)
                    st.write("Report parsed.")
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
                status.update(label="Running live web research...")
                st.write("Searching the web for competitors, pricing, and complaints...")
                try:
                    web_notes = run_web_research(st.session_state.product_input)
                    st.write("Web research done.")
                except Exception as exc:  # noqa: BLE001
                    st.warning(f"Web research skipped: {exc}")

            st.session_state.competitor_report = report
            st.session_state.web_notes = web_notes

            status.update(label="Synthesizing research...")
            st.write("Synthesizing everything into listing-ready research...")
            st.session_state.research_result = run_research(
                st.session_state.product_input,
                competitor_report=report,
                manual_brief=brief_text,
                web_notes=web_notes,
            )
            status.update(label="Research complete.", state="complete", expanded=False)

        autosave()

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
            autosave()

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
        with st.spinner("Writing titles, bullets, description, and image prompts..."):
            st.session_state.listing_output = generate_listing(
                st.session_state.product_input,
                st.session_state.research_result,
                keyword_plan,
            )
        autosave()


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


# ---------------------------------------------------------------------------
# Step 5: Build Amazon Flat File
# The app fills every field it can; blanks are left for the worker to finish
# in Excel before upload (product images are still added manually).
# ---------------------------------------------------------------------------
XLSM_MIME = "application/vnd.ms-excel.sheet.macroEnabled.12"
ITEM_FORM_OPTIONS = ["", "Granules", "Powder", "Liquid", "Pellet", "Prill",
                     "Flake", "Paste", "Tablets", "Capsules", "Sticks"]
DG_OPTIONS = ["Not Applicable", "GHS", "Storage", "Transportation", "Waste", "Other", "Unknown"]
PRODUCT_ID_TYPE_OPTIONS = ["", "UPC", "EAN", "GTIN", "ASIN"]
PESTICIDE_OPTIONS = [
    "This product is not a pesticide or pesticide device, as defined under the "
    "U.S. Federal Insecticide, Fungicide, and Rodenticide Act.",
    "This product qualifies for an exemption from registration under the U.S. "
    "Federal Insecticide, Fungicide, and Rodenticide Act.",
    "This product is a pesticide or pesticide device, as defined under the U.S. "
    "Federal Insecticide, Fungicide, and Rodenticide Act",
]
TEMPLATE_FILES = {"fertilizer": "FERTILIZER.xlsm", "soil": "SOIL.xlsm"}


if st.session_state.listing_output:
    st.subheader("Step 5: Build Amazon Flat File")
    st.caption("The app fills everything it can. Leave the rest blank and finish "
               "it in Excel before upload (images are still added manually).")

    # default the template to match the Step 0 product type
    pinput = st.session_state.product_input or {}
    ptype = (pinput.get("product_type") or "").lower()
    default_kind_idx = 1 if any(k in ptype for k in ["soil", "mix", "amend", "mulch"]) else 0
    product_kind = st.selectbox(
        "Template", ["fertilizer", "soil"], index=default_kind_idx, format_func=str.capitalize
    )

    # template source: bundled file in templates/, or upload
    template_bytes = None
    bundled = Path("templates") / TEMPLATE_FILES[product_kind]
    if bundled.exists():
        template_bytes = bundled.read_bytes()
        st.success(f"Using bundled template: {bundled.name}")
    else:
        up = st.file_uploader(
            f"Upload the Amazon {product_kind} flat-file template (.xlsm)", type=["xlsm"]
        )
        if up is not None:
            template_bytes = up.getvalue()

    # generated content (auto-filled, read-only preview)
    listing = st.session_state.listing_output
    ff_backend = ""
    if st.session_state.keyword_plan:
        ff_backend = st.session_state.keyword_plan.get("backend_terms", "")

    with st.expander("Auto-filled by the app (title, bullets, description, keywords)", expanded=False):
        st.write("**Title (item_name):**", (listing.get("titles") or [""])[0])
        st.write("**Bullets:**")
        for b in (listing.get("bullets") or []):
            st.write("•", b)
        st.write("**Description:**", listing.get("description", ""))
        st.write("**Backend search terms:**", ff_backend)

    # business / product fields (all optional; blank = finish in Excel)
    st.markdown("**Product & business fields** (optional — blanks are left for manual completion)")
    c1, c2, c3 = st.columns(3)
    with c1:
        brand = st.text_input("Brand Name", value=pinput.get("brand", ""))
        manufacturer = st.text_input("Manufacturer")
        country = st.text_input("Country of Origin", value="United States")
        item_type_keyword = st.text_input(
            "Item Type Keyword", value="fertilizers" if product_kind == "fertilizer" else "soils"
        )
        product_id_type = st.selectbox("Product Id Type", PRODUCT_ID_TYPE_OPTIONS)
        product_id_value = st.text_input("Product Id (UPC/EAN/GTIN)")
    with c2:
        condition = st.text_input("Condition", value="New")
        item_form = st.selectbox("Item Form", ITEM_FORM_OPTIONS)
        list_price = st.text_input("List Price (USD)")
        item_weight = st.text_input("Item Weight")
        item_weight_unit = st.text_input("Item Weight Unit", value="Pounds")
        dg_regulation = st.selectbox("Dangerous Goods Regulation", DG_OPTIONS)
    with c3:
        pkg_l = st.text_input("Package Length")
        pkg_w = st.text_input("Package Width")
        pkg_h = st.text_input("Package Height")
        pkg_dim_unit = st.text_input("Package Dim Unit", value="Inches")
        pkg_weight = st.text_input("Package Weight")
        pkg_weight_unit = st.text_input("Package Weight Unit", value="Pounds")

    # category-specific extras
    ingredients = intended_use = ""
    item_l = item_w = item_h = item_dim_unit = ""
    if product_kind == "fertilizer":
        ingredients = st.text_area("Ingredients", height=70)
    else:
        intended_use = st.text_input("Intended Use")
        ic1, ic2, ic3, ic4 = st.columns(4)
        with ic1:
            item_l = st.text_input("Item Length")
        with ic2:
            item_w = st.text_input("Item Width")
        with ic3:
            item_h = st.text_input("Item Height")
        with ic4:
            item_dim_unit = st.text_input("Item Dim Unit", value="Inches")

    pesticide_status = st.selectbox("Pesticide Registration Status", PESTICIDE_OPTIONS)
    prop65 = st.text_input("California Prop 65 Chemical Name(s) (if any)")

    # shared business/descriptive fields (used by both single and family mode)
    shared = {
        "brand": brand, "manufacturer": manufacturer, "country_of_origin": country,
        "item_type_keyword": item_type_keyword, "condition_type": condition,
        "dg_regulation": dg_regulation, "pesticide_status": pesticide_status,
        "product_type": product_kind.upper(),
        "product_id_type": product_id_type, "product_id_value": product_id_value,
        "item_form": item_form, "ingredients": ingredients, "intended_use": intended_use,
        "prop65": prop65, "list_price_value": list_price,
        "item_weight_value": item_weight, "item_weight_unit": item_weight_unit,
        "pkg_length": pkg_l, "pkg_width": pkg_w, "pkg_height": pkg_h,
        "pkg_length_unit": pkg_dim_unit, "pkg_width_unit": pkg_dim_unit,
        "pkg_height_unit": pkg_dim_unit,
        "pkg_weight_value": pkg_weight, "pkg_weight_unit": pkg_weight_unit,
        "item_length": item_l, "item_width": item_w, "item_height": item_h,
        "item_length_unit": item_dim_unit, "item_width_unit": item_dim_unit,
        "item_height_unit": item_dim_unit,
    }

    structure = st.radio(
        "Listing structure",
        ["Single product", "Product family (sizes / variations)"],
        horizontal=True,
        help="Use a product family when the same listing comes in several sizes: "
             "one parent plus a child SKU per size, each with FBM and FBA.",
    )

    if structure == "Single product":
        st.markdown("**Offers** — one row per SKU. Same listing, different SKU/channel for FBM vs FBA.")
        default_offers = pd.DataFrame([
            {"sku": "", "fulfillment_channel_code": "DEFAULT", "quantity": None},
            {"sku": "", "fulfillment_channel_code": "AMAZON_NA", "quantity": None},
        ])
        offers_df = st.data_editor(
            default_offers, num_rows="dynamic", hide_index=True, use_container_width=True,
            key="flatfile_offers",
            column_config={
                "sku": st.column_config.TextColumn("SKU"),
                "fulfillment_channel_code": st.column_config.SelectboxColumn(
                    "Fulfillment", options=["DEFAULT", "AMAZON_NA"],
                    help="DEFAULT = FBM (merchant), AMAZON_NA = FBA"),
                "quantity": st.column_config.NumberColumn("Quantity"),
            },
        )
        if st.button("Build Flat File"):
            if not template_bytes:
                st.error("No template available. Upload the .xlsm template first.")
            else:
                offers = [
                    {"sku": r.get("sku"),
                     "fulfillment_channel_code": r.get("fulfillment_channel_code"),
                     "fulfillment_quantity": r.get("quantity")}
                    for r in offers_df.to_dict("records")
                    if str(r.get("sku") or "").strip()
                ]
                xlsm_bytes, report = build_flat_file(
                    template_bytes, listing=listing, backend_search_terms=ff_backend,
                    shared=shared, offers=offers, product_kind=product_kind,
                )
                st.session_state.flatfile_bytes = xlsm_bytes
                st.session_state.flatfile_report = report
                autosave()

    else:
        st.markdown("**Product family** — one parent, then a child SKU per size for both "
                    "FBM and FBA. Each size needs its own UPC. Weight/price come from the "
                    "size row; package dimensions fall back to the fields above.")
        fc1, fc2 = st.columns(2)
        with fc1:
            parent_sku = st.text_input("Parent SKU", help="Family/parent SKU, e.g. GW-1010-PARENT")
        with fc2:
            variation_theme = st.text_input("Variation Theme", value="Size")

        default_sizes = pd.DataFrame([
            {"size": "", "product_id_value": "", "list_price_value": None,
             "item_weight_value": None, "fbm_sku": "", "fbm_qty": None,
             "fba_sku": "", "fba_qty": None},
        ])
        sizes_df = st.data_editor(
            default_sizes, num_rows="dynamic", hide_index=True, use_container_width=True,
            key="flatfile_sizes",
            column_config={
                "size": st.column_config.TextColumn("Size", help="e.g. 1/2 Quart"),
                "product_id_value": st.column_config.TextColumn("UPC (per size)"),
                "list_price_value": st.column_config.NumberColumn("Price", format="%.2f"),
                "item_weight_value": st.column_config.NumberColumn("Weight"),
                "fbm_sku": st.column_config.TextColumn("FBM SKU"),
                "fbm_qty": st.column_config.NumberColumn("FBM Qty"),
                "fba_sku": st.column_config.TextColumn("FBA SKU"),
                "fba_qty": st.column_config.NumberColumn("FBA Qty"),
            },
        )
        if st.button("Build Family Flat File"):
            if not template_bytes:
                st.error("No template available. Upload the .xlsm template first.")
            elif not str(parent_sku or "").strip():
                st.error("Enter a Parent SKU.")
            else:
                size_keys = ("size", "product_id_value", "list_price_value",
                             "item_weight_value", "fbm_sku", "fbm_qty", "fba_sku", "fba_qty")
                sizes = [
                    {k: r.get(k) for k in size_keys}
                    for r in sizes_df.to_dict("records")
                    if str(r.get("size") or "").strip()
                    and (str(r.get("fbm_sku") or "").strip() or str(r.get("fba_sku") or "").strip())
                ]
                xlsm_bytes, report = build_family_flat_file(
                    template_bytes, listing=listing, backend_search_terms=ff_backend,
                    shared=shared, parent_sku=parent_sku.strip(), sizes=sizes,
                    variation_theme=(variation_theme or "Size").strip(),
                    product_kind=product_kind,
                )
                st.session_state.flatfile_bytes = xlsm_bytes
                st.session_state.flatfile_report = report
                autosave()

    if st.session_state.flatfile_bytes:
        rep = st.session_state.flatfile_report
        if "children" in rep:
            st.success(f"Family flat file built: 1 parent + {rep['children']} child row(s) "
                       f"across {rep['sizes']} size(s).")
        else:
            st.success(f"Flat file built: {rep['rows_written']} row(s) written, "
                       f"{len(rep.get('fields_written', []))} fields filled.")
            if rep.get("blank_required"):
                st.warning("Required fields still blank (finish in Excel): "
                           + ", ".join(rep["blank_required"]))
        st.download_button(
            "Download Flat File (.xlsm)",
            data=st.session_state.flatfile_bytes,
            file_name=f"{product_kind}_flat_file.xlsm",
            mime=XLSM_MIME,
        )
