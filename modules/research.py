import json
from typing import Any, Dict, Optional

import config
from modules import llm
from prompts.research_prompt import RESEARCH_SYSTEM_PROMPT


def _research_schema() -> Dict[str, Any]:
    return {
        "name": "research_result",
        "schema": {
            "type": "object",
            "properties": {
                "product_summary": {"type": "string"},
                "target_segments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "priorities": {"type": "string"},
                        },
                        "required": ["name", "priorities"],
                        "additionalProperties": False,
                    },
                },
                "buying_decision_factors": {"type": "array", "items": {"type": "string"}},
                "buyer_pains": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "pain": {"type": "string"},
                            "evidence": {"type": "string"},
                        },
                        "required": ["pain", "evidence"],
                        "additionalProperties": False,
                    },
                },
                "objections_to_preempt": {"type": "array", "items": {"type": "string"}},
                "differentiators": {"type": "array", "items": {"type": "string"}},
                "messaging_angles": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "pain": {"type": "string"},
                            "angle": {"type": "string"},
                        },
                        "required": ["pain", "angle"],
                        "additionalProperties": False,
                    },
                },
                "proof_points": {"type": "array", "items": {"type": "string"}},
                "compliance_flags": {"type": "array", "items": {"type": "string"}},
                "price_positioning": {"type": "string"},
                "suggested_keywords": {"type": "array", "items": {"type": "string"}},
                "research_basis": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "product_summary",
                "target_segments",
                "buying_decision_factors",
                "buyer_pains",
                "objections_to_preempt",
                "differentiators",
                "messaging_angles",
                "proof_points",
                "compliance_flags",
                "price_positioning",
                "suggested_keywords",
                "research_basis",
            ],
            "additionalProperties": False,
        },
    }


def run_research(
    product_input: Dict[str, Any],
    competitor_report: Optional[Dict[str, Any]] = None,
    manual_brief: str = "",
    web_notes: str = "",
) -> Dict[str, Any]:
    schema = _research_schema()

    report_block = json.dumps(competitor_report, indent=2) if competitor_report else "null"
    manual_block = manual_brief.strip() if manual_brief and manual_brief.strip() else "null"
    web_block = web_notes.strip() if web_notes and web_notes.strip() else "null"

    available = []
    if competitor_report:
        available.append("a competitor report (review-mined, strongest evidence)")
    if manual_block != "null":
        available.append("a manual operator brief")
    if web_block != "null":
        available.append("live web research notes")
    if available:
        grounding_note = ("Sources available: " + "; ".join(available) +
                          ". Ground per the priority rules and record what you used in research_basis.")
    else:
        grounding_note = (
            "No external sources are available. Work from product facts and category logic, "
            "mark every buyer pain's evidence as 'category inference', and set research_basis to ['category inference']."
        )

    user_prompt = f"""
Produce listing-ready research for this product.

PRODUCT INPUT:
{json.dumps(product_input, indent=2)}

COMPETITOR REPORT (review-mined; strongest evidence when present):
{report_block}

MANUAL OPERATOR BRIEF (competitors we see + product context):
{manual_block}

LIVE WEB RESEARCH NOTES:
{web_block}

{grounding_note}

Return JSON only.
""".strip()

    return llm.complete_json(
        config.MODEL_RESEARCH,
        RESEARCH_SYSTEM_PROMPT,
        user_prompt,
        schema["schema"],
        max_tokens=config.MAX_TOKENS_RESEARCH,
    )
