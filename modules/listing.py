import json
import os
from typing import Any, Dict, List, Optional

import streamlit as st
from openai import OpenAI

from prompts.listing_prompt import LISTING_SYSTEM_PROMPT

LISTING_MODEL = "gpt-5.4"
LISTING_REASONING_EFFORT = "medium"
OPENAI_API_KEY = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))


def get_openai_client() -> Optional[OpenAI]:
    if not OPENAI_API_KEY:
        return None
    return OpenAI(api_key=OPENAI_API_KEY)


def generate_listing(
    product_input: Dict[str, Any],
    research_result: Dict[str, Any],
    selected_keywords: List[str],
) -> Dict[str, Any]:
    client = get_openai_client()
    if client is None:
        raise ValueError("OpenAI API key is missing. Set OPENAI_API_KEY in Streamlit secrets or your environment.")

    schema = {
        "name": "listing_output",
        "schema": {
            "type": "object",
            "properties": {
                "titles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 3,
                    "maxItems": 3,
                },
                "bullets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 5,
                    "maxItems": 5,
                },
                "description": {"type": "string"},
                "image_prompts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 5,
                    "maxItems": 5,
                },
            },
            "required": ["titles", "bullets", "description", "image_prompts"],
            "additionalProperties": False,
        },
    }

    user_prompt = f"""
Create Amazon listing draft content.

Product input:
{json.dumps(product_input, indent=2)}

Research summary:
{json.dumps(research_result, indent=2)}

Selected keywords:
{json.dumps(selected_keywords, indent=2)}

Requirements:
- Generate exactly 3 title variants.
- Generate exactly 5 bullet points.
- Generate 1 SEO-friendly description.
- Generate exactly 5 image prompts.
- Keep the writing commercially useful and readable.
- Avoid unsupported claims.
- Return JSON only.
""".strip()

    response = client.responses.create(
        model=LISTING_MODEL,
        reasoning={"effort": LISTING_REASONING_EFFORT},
        input=[
            {"role": "system", "content": LISTING_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
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


def export_listing_text(product_input: Dict[str, Any], listing_output: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("AMAZON LISTING EXPORT")
    lines.append("")
    lines.append(f"Product Name: {product_input['product_name']}")
    lines.append(f"Category: {product_input['category']}")
    lines.append(f"Target Customer: {product_input['target_customer']}")
    lines.append("")
    lines.append("TITLE VARIANTS")
    for i, title in enumerate(listing_output["titles"], start=1):
        lines.append(f"{i}. {title}")
    lines.append("")
    lines.append("BULLET POINTS")
    for i, bullet in enumerate(listing_output["bullets"], start=1):
        lines.append(f"{i}. {bullet}")
    lines.append("")
    lines.append("SEO DESCRIPTION")
    lines.append(listing_output["description"])
    lines.append("")
    lines.append("IMAGE PROMPTS")
    for i, prompt in enumerate(listing_output["image_prompts"], start=1):
        lines.append(f"{i}. {prompt}")
    return "\n".join(lines)
