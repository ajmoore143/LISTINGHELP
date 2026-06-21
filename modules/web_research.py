import json
import os
from typing import Any, Dict, Optional

import streamlit as st
from openai import OpenAI

WEB_RESEARCH_MODEL = "gpt-5.4"
WEB_RESEARCH_REASONING_EFFORT = "medium"
# Depending on the account/model the tool type may be "web_search" or
# "web_search_preview"; adjust here if the API rejects it.
WEB_SEARCH_TOOL_TYPE = "web_search"
OPENAI_API_KEY = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))

WEB_RESEARCH_SYSTEM_PROMPT = """
ROLE
You are an Amazon market research analyst with live web access.

OBJECTIVE
Use web search to gather REAL, current market intelligence for the given
product's Amazon category, so a listing can be built on facts rather than
guesses.

WHAT TO FIND
- Competitors: actual competing products and brands on Amazon in this category,
  with how they position themselves and their approximate price.
- Pricing: the typical price range and common pack/size tiers.
- Complaints: frustrations buyers repeatedly raise about products in this
  category (from reviews discussed online, blogs, forums, Reddit).
- What buyers value: features and outcomes shoppers praise or look for.
- Feature expectations and category trends.

RULES
- Search the web; do not rely on memory for current products or prices.
- Report only what sources support. If something can't be found, say so plainly.
- Do NOT fabricate statistics, brands, or prices.
- Prefer Amazon, manufacturer sites, and reputable publications.
- Cite a source (site or URL) for each concrete finding.

OUTPUT
Return concise markdown notes grouped under these headings:
Competitors, Pricing, Common complaints, What buyers value, Category trends.
Keep it factual and tight. Include sources inline.
""".strip()


def get_openai_client() -> Optional[OpenAI]:
    if not OPENAI_API_KEY:
        return None
    return OpenAI(api_key=OPENAI_API_KEY)


def run_web_research(product_input: Dict[str, Any]) -> str:
    """
    Run a live web-research pass for the product and return markdown notes.

    Returns an empty string if no API key is configured so the caller can
    continue without web data. Tool/model errors propagate to the caller.
    """
    client = get_openai_client()
    if client is None:
        return ""

    user_prompt = f"""
Research the Amazon market for this product. Use web search.

PRODUCT:
{json.dumps(product_input, indent=2)}

Return concise, sourced markdown notes under the required headings.
""".strip()

    response = client.responses.create(
        model=WEB_RESEARCH_MODEL,
        reasoning={"effort": WEB_RESEARCH_REASONING_EFFORT},
        tools=[{"type": WEB_SEARCH_TOOL_TYPE}],
        input=[
            {"role": "system", "content": WEB_RESEARCH_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )

    return (response.output_text or "").strip()
