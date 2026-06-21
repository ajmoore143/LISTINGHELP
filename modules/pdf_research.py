import base64
import json
import os
from typing import Any, Dict, Optional

import streamlit as st
from openai import OpenAI

REPORT_MODEL = "gpt-5.4"
REPORT_REASONING_EFFORT = "medium"
OPENAI_API_KEY = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))

REPORT_EXTRACTION_PROMPT = """
ROLE
You extract structured competitor and market intelligence from an Amazon
listing research report (e.g. a Listing Optimization AI export). The report may
contain text, charts, tables, and dashboards.

OBJECTIVE
Read the entire report and capture the facts that matter for writing a winning
Amazon listing: what buyers value, what frustrates them, what drives the
purchase decision, how competitors are positioned, and where the gaps are.

RULES
- Extract only what the report actually states. Do not invent figures.
- Preserve any quantitative signal the report gives (mention counts, impact
  percentages, market share, price ranges).
- Keep buying decision factors in the report's stated priority order.
- For unknown numeric fields use 0; for unknown text use an empty string; for
  unknown lists use an empty array.

OUTPUT
Return STRICT JSON only, matching the provided schema. No text outside the JSON.
""".strip()


def get_openai_client() -> Optional[OpenAI]:
    if not OPENAI_API_KEY:
        return None
    return OpenAI(api_key=OPENAI_API_KEY)


def _report_schema() -> Dict[str, Any]:
    return {
        "name": "competitor_report",
        "schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "review_count": {"type": "integer"},
                "competitor_count": {"type": "integer"},
                "positive_themes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "theme": {"type": "string"},
                            "mentions": {"type": "integer"},
                        },
                        "required": ["theme", "mentions"],
                        "additionalProperties": False,
                    },
                },
                "pain_points": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "pain": {"type": "string"},
                            "impact": {"type": "string"},
                        },
                        "required": ["pain", "impact"],
                        "additionalProperties": False,
                    },
                },
                "feature_requests": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "request": {"type": "string"},
                            "mentions": {"type": "integer"},
                        },
                        "required": ["request", "mentions"],
                        "additionalProperties": False,
                    },
                },
                "buying_decision_factors": {"type": "array", "items": {"type": "string"}},
                "customer_segments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["name", "description"],
                        "additionalProperties": False,
                    },
                },
                "competitors": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "brand": {"type": "string"},
                            "category": {"type": "string"},
                            "key_features": {"type": "array", "items": {"type": "string"}},
                            "market_share": {"type": "string"},
                        },
                        "required": ["brand", "category", "key_features", "market_share"],
                        "additionalProperties": False,
                    },
                },
                "price_range": {"type": "string"},
                "differentiation_opportunities": {"type": "array", "items": {"type": "string"}},
                "strategic_recommendations": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "summary",
                "review_count",
                "competitor_count",
                "positive_themes",
                "pain_points",
                "feature_requests",
                "buying_decision_factors",
                "customer_segments",
                "competitors",
                "price_range",
                "differentiation_opportunities",
                "strategic_recommendations",
            ],
            "additionalProperties": False,
        },
    }


def parse_competitor_report(uploaded_file) -> Dict[str, Any]:
    """
    Parse a competitor research PDF into a structured competitor_report dict.

    The PDF is sent to the model natively, so both text-based and image-based
    (scanned / dashboard-rendered) reports are handled without local OCR.
    """
    client = get_openai_client()
    if client is None:
        raise ValueError("OpenAI API key is missing. Set OPENAI_API_KEY in Streamlit secrets or your environment.")

    uploaded_file.seek(0)
    data = uploaded_file.read()
    if not data[:5] == b"%PDF-":
        raise ValueError("The uploaded file is not a valid PDF. Export the report as a standard PDF and try again.")

    encoded = base64.b64encode(data).decode("ascii")
    filename = getattr(uploaded_file, "name", "report.pdf") or "report.pdf"
    schema = _report_schema()

    response = client.responses.create(
        model=REPORT_MODEL,
        reasoning={"effort": REPORT_REASONING_EFFORT},
        input=[
            {"role": "system", "content": REPORT_EXTRACTION_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Extract the structured competitor report from this file."},
                    {
                        "type": "input_file",
                        "filename": filename,
                        "file_data": f"data:application/pdf;base64,{encoded}",
                    },
                ],
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": schema["name"],
                "schema": schema["schema"],
                "strict": True,
            }
        },
    )

    return json.loads(response.output_text)
