import json
import os
from typing import Any, Dict, Optional

import streamlit as st
from openai import OpenAI

from prompts.research_prompt import RESEARCH_SYSTEM_PROMPT

RESEARCH_MODEL = "gpt-5.4"
RESEARCH_REASONING_EFFORT = "high"
OPENAI_API_KEY = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))


def get_openai_client() -> Optional[OpenAI]:
    if not OPENAI_API_KEY:
        return None
    return OpenAI(api_key=OPENAI_API_KEY)


def run_research(product_input: Dict[str, Any]) -> Dict[str, Any]:
    client = get_openai_client()
    if client is None:
        raise ValueError("OpenAI API key is missing. Set OPENAI_API_KEY in Streamlit secrets or your environment.")

    schema = {
        "name": "research_result",
        "schema": {
            "type": "object",
            "properties": {
                "use_cases": {"type": "array", "items": {"type": "string"}},
                "strengths": {"type": "array", "items": {"type": "string"}},
                "complaints": {"type": "array", "items": {"type": "string"}},
                "buyer_pains": {"type": "array", "items": {"type": "string"}},
                "messaging_angles": {"type": "array", "items": {"type": "string"}},
                "suggested_keywords": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "use_cases",
                "strengths",
                "complaints",
                "buyer_pains",
                "messaging_angles",
                "suggested_keywords",
            ],
            "additionalProperties": False,
        },
    }

    user_prompt = f"""
Analyze this product idea for Amazon listing preparation.

Product input:
{json.dumps(product_input, indent=2)}

Return exactly these fields:
- use_cases
- strengths
- complaints
- buyer_pains
- messaging_angles
- suggested_keywords (10 keyword phrases, realistic Amazon-style search terms, may be multi-word)

Return JSON only.
""".strip()

    response = client.responses.create(
        model=RESEARCH_MODEL,
        reasoning={"effort": RESEARCH_REASONING_EFFORT},
        input=[
            {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
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
