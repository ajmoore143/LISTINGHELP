"""
Project persistence backed by Supabase (Postgres).

Lets a logged-in worker save the full pipeline state under a named project and
resume it later from any device. Storage is optional: if Supabase secrets are
not set, is_enabled() returns False and the app runs without persistence.

Required Streamlit secrets:
    SUPABASE_URL = "https://xxxx.supabase.co"
    SUPABASE_KEY = "service_role or anon key"

Table (run once in Supabase SQL editor):
    create extension if not exists "pgcrypto";
    create table if not exists projects (
        id uuid primary key default gen_random_uuid(),
        user_email text not null,
        name text not null,
        state jsonb not null,
        updated_at timestamptz not null default now(),
        unique (user_email, name)
    );
"""

from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

# Session keys that make up a saved project.
_STATE_KEYS = [
    "product_input",
    "competitor_report",
    "web_notes",
    "research_result",
    "selected_keywords",
    "listing_output",
    "keyword_plan",
    "keyword_master_df",
    "keyword_review_df",
]
_DF_MARKER = "__dataframe__"


def is_enabled() -> bool:
    return bool(st.secrets.get("SUPABASE_URL", "") and st.secrets.get("SUPABASE_KEY", ""))


@st.cache_resource(show_spinner=False)
def _client():
    """Lazily build the Supabase client (cached across reruns)."""
    if not is_enabled():
        return None
    from supabase import create_client  # imported lazily so the app loads without the package
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


# --- (de)serialization: DataFrames <-> JSON-safe dicts --------------------

def serialize_state(session_state) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in _STATE_KEYS:
        val = session_state.get(key)
        if isinstance(val, pd.DataFrame):
            out[key] = {_DF_MARKER: val.to_json(orient="split")}
        else:
            out[key] = val
    return out


def apply_state(session_state, state: Dict[str, Any]) -> None:
    for key in _STATE_KEYS:
        if key not in state:
            continue
        val = state[key]
        if isinstance(val, dict) and _DF_MARKER in val:
            try:
                session_state[key] = pd.read_json(val[_DF_MARKER], orient="split")
            except Exception:
                session_state[key] = None
        else:
            session_state[key] = val


# --- CRUD ------------------------------------------------------------------

def list_projects(user_email: str) -> List[Dict[str, Any]]:
    client = _client()
    if client is None:
        return []
    res = (
        client.table("projects")
        .select("id,name,updated_at")
        .eq("user_email", user_email)
        .order("updated_at", desc=True)
        .execute()
    )
    return res.data or []


def save_project(user_email: str, name: str, session_state) -> Optional[Dict[str, Any]]:
    client = _client()
    if client is None:
        return None
    payload = {
        "user_email": user_email,
        "name": name,
        "state": serialize_state(session_state),
    }
    # upsert on (user_email, name) so re-saving the same project updates it
    res = (
        client.table("projects")
        .upsert(payload, on_conflict="user_email,name")
        .execute()
    )
    return (res.data or [None])[0]


def load_project(project_id: str) -> Optional[Dict[str, Any]]:
    client = _client()
    if client is None:
        return None
    res = client.table("projects").select("*").eq("id", project_id).single().execute()
    return res.data


def delete_project(project_id: str) -> None:
    client = _client()
    if client is None:
        return
    client.table("projects").delete().eq("id", project_id).execute()
