"""Metric On-Call demo app.

    make demo      (or: streamlit run app/streamlit_app.py)

Replay mode: runs the offline deterministic pipeline on a planted incident. No API key.
Live mode:   same graph with an LLM provider from .env (ollama | gemini); needs `ollama pull` / GEMINI_API_KEY.
Nothing is published until you click Approve.
"""

from __future__ import annotations

import json

import streamlit as st

from agents.models import get_model
from agents.runner import investigate
from common.config import TRUTH_PATH
from tools.registry import get_warehouse

st.set_page_config(page_title="Metric On-Call", layout="wide")
st.title("Metric On-Call")
st.caption("Multi-agent KPI root-cause analysis. The tools do the maths; the agents route, judge and write.")

if not TRUTH_PATH.exists():
    st.error("No data yet. Run `make data` first.")
    st.stop()

truth = json.loads(TRUTH_PATH.read_text())["incidents"]
with st.sidebar:
    mode = st.radio("Mode", ["Replay (offline)", "Live (LLM)"])
    provider = st.selectbox("Provider", ["ollama", "gemini"]) if mode.startswith("Live") else "scripted"
    pick = st.selectbox("Incident", truth, format_func=lambda t: f"{t['id']:>2}. {t['name']}")
    show_truth = st.checkbox("Show planted ground truth", value=False)
    go = st.button("Investigate", type="primary")

if go:
    st.session_state["run"] = investigate(str(pick["date_from"]), str(pick["date_to"]), model=get_model(provider),
                                          wh=get_warehouse(), approver=None)
    st.session_state["truth"] = pick
    st.session_state.pop("published", None)

run = st.session_state.get("run")
if run:
    f = run["findings"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Status", f["status"].replace("_", " "))
    c2.metric("Cause", f["cause_category"])
    c3.metric("Impact", "n/a" if f["dollar_impact"] is None else f"${f['dollar_impact']:,.0f}")
    c4.metric("Confidence", f["confidence"])
    left, right = st.columns([3, 2])
    with left:
        st.markdown(run["brief"])
    with right:
        st.subheader("Agent trace")
        for t in run["trace"]:
            if "tool" in t:
                st.code(f"{t['agent']} → {t['tool']}({json.dumps(t['arguments'])[:120]})", language=None)
        st.caption(f"{run['budget']['steps']} tool calls")
    if show_truth:
        t = st.session_state["truth"]
        st.info(f"Ground truth: {t['causes']}, planted impact ${t['planted_impact_dollars']:,.0f}" if t.get("planted_impact_dollars") is not None else f"Ground truth: {t['causes']} (data fault, no business impact)")
    a, b = st.columns(2)
    if a.button("Approve and publish") and "published" not in st.session_state:
        from agents import nodes
        from agents.publish import publish
        from agents.schemas import Decision

        st.session_state["published"] = publish(run | nodes.apply_decision(run, Decision(action="approve")))["published"]
    if b.button("Reject"):
        st.warning("Rejected: nothing published. Re-run with a reviewer comment via the graph API.")
    if "published" in st.session_state:
        st.success(st.session_state["published"])
