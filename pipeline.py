"""The StarSage answer pipeline.

Stages: safety -> classify -> KB retrieval -> chart context -> draft ->
(optional critic + rewrite) -> format -> shorten. Every LLM call is logged
in full (request, response, tokens, cost) via the run-step recorder.
"""
import json
import logging
import os
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

import knowledge_base as kb
from settings import settings
from db import compute_state_hash, db_insert, get_active_prompt, get_active_value
from llm import cost_for, llm_call, render_messages

logger = logging.getLogger(__name__)

def load_complete_astro_json(user_id: str, name: str) -> Optional[dict]:
    path = os.path.join("user_astro_data", user_id, f"{name}_complete_astro_data.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def extract_path(data: dict, dotpath: str) -> Any:
    parts = dotpath.split(".")
    cur = data
    for p in parts:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def build_chart_context(complete_data: dict, paths: List[str], max_chars: int = 8000) -> str:
    parts = []
    for path in paths:
        val = extract_path(complete_data, path)
        if val is not None:
            chunk = f"### {path}\n{json.dumps(val, indent=2)}"
            parts.append(chunk)
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... [truncated]"
    return text


KB_TRIGGER_PHRASES = [
    "explain", "definition", "what is", "what does", "what are",
    "teach me", "show me the source", "classical", "shastra", "source",
    "why does", "how does", "meaning of", "origin of",
]

KB_CLASSIFICATIONS = {"EDUCATIONAL", "REFLECTIVE", "COACHING", "CONCEPT_EXPLANATION"}


def wants_kb_retrieval(message: str) -> bool:
    msg = message.lower()
    return any(phrase in msg for phrase in KB_TRIGGER_PHRASES)


def _extract_kb_tags(text: str) -> dict:
    """Return {term: '[[kb:term]]'} for every marker in text."""
    import re as _re
    return {m.group(1).strip(): m.group(0) for m in _re.finditer(r"\[\[kb:([^\]]+)\]\]", text)}


def _reinjector(text: str, kb_tags: dict) -> str:
    """Re-inject [[kb:term]] markers stripped by an LLM, by finding the bare term in the text."""
    import re as _re
    if not kb_tags or _re.search(r"\[\[kb:", text):
        return text  # tags still present; nothing to do
    for term, full_tag in kb_tags.items():
        if term in text:
            text = text.replace(term, full_tag, 1)
    return text


def format_answer_html(text: str) -> str:
    """Convert plain-text pipeline output to display HTML.

    - Converts double newlines to paragraph breaks
    - Converts [[kb:term]] markers to clickable <a> links
    """
    import re as _re
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\n\n", "<br><br>")
    text = text.replace("\n", "<br>")

    def replacer(m):
        term = m.group(1).strip()
        safe_attr = term.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
        safe_text = term.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return (
            f'<a class="kb-link" data-term="{safe_attr}" '
            f'onclick="openKnowledgeTerm(this.dataset.term)">{safe_text}</a>'
        )

    return _re.sub(r"\[\[kb:([^\]]+)\]\]", replacer, text)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

REQUIRED_PATHS = [
    "vedic_horoscope.planets_position",
    "vedic_horoscope.astro_details",
    "charts.D1",
]

TIMING_PATHS = REQUIRED_PATHS + ["dashas", "vedic_horoscope.current_vimshottari_dasha"]
THEMATIC_PATHS = REQUIRED_PATHS + ["additional.aspects"]
MIXED_PATHS = TIMING_PATHS + ["additional.aspects"]
FORECAST_PATHS = TIMING_PATHS + ["additional.planets"]


def paths_for_class(classification: str) -> List[str]:
    mapping = {
        "TIMING": TIMING_PATHS,
        "THEMATIC": THEMATIC_PATHS,
        "MIXED": MIXED_PATHS,
        "FORECAST": FORECAST_PATHS,
        "EDUCATIONAL": THEMATIC_PATHS,
        "CONCEPT_EXPLANATION": THEMATIC_PATHS,
        # Reflective/coaching: no chart required; if chart exists, use minimal paths
        "REFLECTIVE": REQUIRED_PATHS,
        "COACHING": REQUIRED_PATHS,
    }
    return mapping.get(classification, THEMATIC_PATHS)


# ---------------------------------------------------------------------------
# Classifier parsing (robust JSON with fallback)
# ---------------------------------------------------------------------------

VALID_INTENTS = {"TIMING", "THEMATIC", "MIXED", "FORECAST", "EDUCATIONAL",
                 "REFLECTIVE", "COACHING", "CONCEPT_EXPLANATION"}
VALID_LIFE_AREAS = {"CAREER", "RELATIONSHIP", "MARRIAGE", "MONEY", "FAMILY", "HOME",
                    "EDUCATION", "HEALTH_GENERAL", "SPIRITUALITY", "IDENTITY", "OTHER"}
VALID_STYLES = {"PLAIN", "BLENDED", "TECHNICAL"}
VALID_DEPTHS = {"CONCISE", "STANDARD", "DEEP"}
VALID_RISK = {"LOW", "MEDIUM", "HIGH"}

FALLBACK_CLASSIFICATION = {
    "intent": "MIXED", "life_area": "OTHER", "answer_style": "BLENDED",
    "answer_depth": "STANDARD", "emotional_risk": "LOW", "needs_kb": True,
    "needs_divisional_chart": False, "suggested_divisional_charts": [],
}

# Word-count targets per depth, and draft token budgets.
DEPTH_TARGETS = {"CONCISE": (100, 160), "STANDARD": (220, 420), "DEEP": (500, 900)}
DEPTH_DRAFT_TOKENS = {"CONCISE": 450, "STANDARD": 850, "DEEP": 1600}


def parse_classifier(text: str) -> dict:
    """Parse the classifier's JSON output, validating each field; fall back safely."""
    import json as _json
    import re as _re
    out = dict(FALLBACK_CLASSIFICATION)
    if not text:
        return out
    m = _re.search(r"\{.*\}", text, _re.DOTALL)
    if not m:
        return out
    try:
        raw = _json.loads(m.group(0))
    except Exception:
        return out
    if not isinstance(raw, dict):
        return out

    def pick(field, valid):
        v = str(raw.get(field, "")).strip().upper()
        if v in valid:
            out[field] = v

    pick("intent", VALID_INTENTS)
    pick("life_area", VALID_LIFE_AREAS)
    pick("answer_style", VALID_STYLES)
    pick("answer_depth", VALID_DEPTHS)
    pick("emotional_risk", VALID_RISK)
    if isinstance(raw.get("needs_kb"), bool):
        out["needs_kb"] = raw["needs_kb"]
    if isinstance(raw.get("needs_divisional_chart"), bool):
        out["needs_divisional_chart"] = raw["needs_divisional_chart"]
    dc = raw.get("suggested_divisional_charts")
    if isinstance(dc, list):
        out["suggested_divisional_charts"] = [str(x).strip().upper() for x in dc if isinstance(x, str)][:6]
    return out


# ---------------------------------------------------------------------------
# Topic-aware chart summary (grounded; nothing invented)
# ---------------------------------------------------------------------------

SIGN_ORDER = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra",
              "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"]
SIGN_LORD = {"Aries": "Mars", "Taurus": "Venus", "Gemini": "Mercury", "Cancer": "Moon",
             "Leo": "Sun", "Virgo": "Mercury", "Libra": "Venus", "Scorpio": "Mars",
             "Sagittarius": "Jupiter", "Capricorn": "Saturn", "Aquarius": "Saturn",
             "Pisces": "Jupiter"}

LIFE_AREA_HOUSES = {
    "MARRIAGE": [7, 2, 11, 5], "RELATIONSHIP": [7, 5, 11], "CAREER": [10, 6, 2, 11],
    "MONEY": [2, 11, 5, 9], "EDUCATION": [4, 5, 2], "HOME": [4, 2], "FAMILY": [2, 4],
    "HEALTH_GENERAL": [1, 6], "SPIRITUALITY": [9, 12, 5], "IDENTITY": [1, 5, 9],
    "OTHER": [1, 10, 7, 4],
}
LIFE_AREA_PLANETS = {
    "MARRIAGE": ["Venus", "Jupiter"], "RELATIONSHIP": ["Venus", "Moon"],
    "CAREER": ["Sun", "Saturn", "Mercury"], "MONEY": ["Jupiter", "Venus", "Mercury"],
    "EDUCATION": ["Mercury", "Jupiter"], "HOME": ["Moon", "Mars"], "FAMILY": ["Moon", "Jupiter"],
    "HEALTH_GENERAL": ["Sun", "Moon", "Mars", "Saturn"], "SPIRITUALITY": ["Jupiter", "Ketu", "Saturn"],
    "IDENTITY": ["Sun", "Moon"], "OTHER": [],
}
LIFE_AREA_DIVISIONALS = {
    "MARRIAGE": ["D9"], "RELATIONSHIP": ["D9"], "CAREER": ["D10"], "MONEY": ["D2"],
    "EDUCATION": ["D24"], "HOME": ["D4"], "FAMILY": ["D12"], "HEALTH_GENERAL": ["D30"],
    "SPIRITUALITY": ["D20"], "IDENTITY": ["D9"], "OTHER": [],
}


def build_topic_chart_summary(complete_data: Optional[dict], life_area: str,
                              suggested_divisionals: List[str], max_chars: int = 8000):
    """Build a compact, topic-aware chart summary + an evidence-facts dict.

    Everything is read from the chart JSON — nothing is invented. Missing data is
    recorded rather than fabricated. Returns (summary_text, facts_dict).
    """
    facts = {"main_chart_factors_used": [], "relevant_house_or_lord": [],
             "relevant_dasha_or_timing": None, "relevant_divisional_chart": [], "missing_data": []}
    if not complete_data:
        return "", facts

    vh = complete_data.get("vedic_horoscope", {}) or {}
    ad = vh.get("astro_details", {}) or {}
    pp = vh.get("planets_position", {}) or {}
    charts = complete_data.get("charts", {}) or {}

    lines = [f"=== CHART SUMMARY (topic: {life_area}) ==="]
    asc_sign = ad.get("ascendant")
    if asc_sign:
        lines.append(f"Ascendant: {asc_sign} | Lagna lord: {ad.get('lagna_lord', '?')}")
    else:
        facts["missing_data"].append("ascendant")
    lines.append(
        f"Sun sign: {ad.get('sun_sign', '?')} | Moon sign: {ad.get('moon_sign', '?')} "
        f"| Birth nakshatra: {ad.get('nakshatra', '?')}"
    )

    cvd = vh.get("current_vimshottari_dasha") or {}
    if cvd.get("dasha"):
        dstr = (f"{cvd.get('dasha')} Mahadasha / {cvd.get('bhukti')} Antardasha "
                f"/ {cvd.get('paryantardasha')} Pratyantardasha")
        lines.append(f"Current dasha: {dstr}")
        facts["relevant_dasha_or_timing"] = dstr
    else:
        facts["missing_data"].append("current_dasha")

    houses = LIFE_AREA_HOUSES.get(life_area, LIFE_AREA_HOUSES["OTHER"])
    if asc_sign in SIGN_ORDER:
        asc_idx = SIGN_ORDER.index(asc_sign)
        lines.append("Key houses for this topic:")
        for h in houses:
            hsign = SIGN_ORDER[(asc_idx + h - 1) % 12]
            hlord = SIGN_LORD[hsign]
            lp = pp.get(hlord)
            if lp:
                retro = " [retrograde]" if lp.get("retrograde") else ""
                lines.append(f"- {h}th house: sign {hsign}, lord {hlord} is in house "
                             f"{lp.get('house')} ({lp.get('sign')}){retro}")
                facts["relevant_house_or_lord"].append(
                    f"{h}th lord {hlord} in house {lp.get('house')}")
            else:
                lines.append(f"- {h}th house: sign {hsign}, lord {hlord} (position missing)")

    planets = LIFE_AREA_PLANETS.get(life_area, [])
    if planets:
        lines.append("Key planets for this topic:")
        for pl in planets:
            info = pp.get(pl)
            if info:
                lines.append(f"- {pl}: {info.get('sign')}, house {info.get('house')}")
                facts["main_chart_factors_used"].append(
                    f"{pl} in {info.get('sign')} (house {info.get('house')})")
            else:
                facts["missing_data"].append(f"{pl}_position")

    wanted_div = suggested_divisionals or LIFE_AREA_DIVISIONALS.get(life_area, [])
    avail = [d for d in wanted_div if d in charts]
    if avail:
        lines.append(f"Divisional charts available for this topic: {', '.join(avail)}")
        facts["relevant_divisional_chart"] = avail
    for d in wanted_div:
        if d not in charts:
            facts["missing_data"].append(f"divisional_{d}")

    if pp:
        lines.append("Full planet positions:")
        for pl, info in pp.items():
            retro = " [R]" if info.get("retrograde") else ""
            lines.append(f"- {pl}: {info.get('sign')}, house {info.get('house')}, "
                         f"{info.get('nakshatra')}{retro}")

    if life_area == "HEALTH_GENERAL":
        lines.append("NOTE: general wellbeing framing only. Do NOT produce medical "
                     "predictions, diagnosis, or treatment advice.")

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... [truncated]"
    return text, facts


def word_count(text: str) -> int:
    return len((text or "").split())


async def run_pipeline(
    conn: sqlite3.Connection,
    user_id: str,
    conversation_id: str,
    message: str,
    history: List[Dict],
    complete_data: Optional[dict],
    req_answer_style: Optional[str] = None,
    req_answer_depth: Optional[str] = None,
) -> dict:
    run_id = str(uuid.uuid4())
    fast_model = get_active_value(conn, "fast_model", settings.DEFAULT_FAST_MODEL)
    reason_model = get_active_value(conn, "reasoning_model", settings.DEFAULT_REASONING_MODEL)
    enable_critic = get_active_value(conn, "enable_critic_pass", "true").lower() == "true"
    enable_kb = get_active_value(conn, "enable_kb_retrieval", "true").lower() == "true"
    max_chars = int(get_active_value(conn, "chart_context_max_chars", "8000"))
    temp_fast = float(get_active_value(conn, "temperature_fast", "0.1"))
    temp_reason = float(get_active_value(conn, "temperature_reasoning", "0.7"))

    # Per-step model overrides (empty config value falls back to fast/reasoning model).
    def model_for(key: str, default: str) -> str:
        return get_active_value(conn, key, "").strip() or default

    safety_model = model_for("safety_model", fast_model)
    classifier_model = model_for("classifier_model", fast_model)
    evidence_model = model_for("evidence_model", reason_model)
    draft_model = model_for("draft_model", reason_model)
    critic_model = model_for("critic_model", fast_model)
    formatter_model = model_for("formatter_model", fast_model)
    shortener_model = model_for("shortener_model", fast_model)
    default_style = get_active_value(conn, "default_answer_style", "BLENDED").upper()
    default_depth = get_active_value(conn, "default_answer_depth", "STANDARD").upper()

    phash, chash = compute_state_hash(conn)
    db_insert(
        conn,
        "INSERT INTO pipeline_runs (id, conversation_id, user_id, user_message, fast_model, "
        "reason_model, prompt_hash, config_hash) VALUES (?,?,?,?,?,?,?,?)",
        (run_id, conversation_id, user_id, message, fast_model, reason_model, phash, chash),
    )

    # Per-run accounting for the detailed logs.
    totals = {"tokens_in": 0, "tokens_out": 0, "cost": 0.0, "idx": 0}

    def log_event(name, *, module="", inp="", out="", model="", request="",
                  tin=0, tout=0, cost=0.0, dur_ms=0):
        """Record one pipeline step (LLM call or notable event) in full detail."""
        totals["idx"] += 1
        db_insert(
            conn,
            "INSERT INTO pipeline_steps (run_id, step_index, step_name, prompt_module, model, "
            "request_text, input_text, output_text, tokens_in, tokens_out, cost_usd, duration_ms) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, totals["idx"], name, module, model,
             request[:12000], inp[:6000], out[:12000], tin, tout, cost, dur_ms),
        )

    async def run_step(name, messages, model, max_tokens, temperature, *, module=""):
        """Make an LLM call, record it (request, response, tokens, cost), return text."""
        t0 = time.time()
        content, tin, tout = await llm_call(messages, model, max_tokens, temperature)
        dur_ms = int((time.time() - t0) * 1000)
        cost = cost_for(model, tin, tout)
        totals["tokens_in"] += tin
        totals["tokens_out"] += tout
        totals["cost"] += cost
        user_msg = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        log_event(name, module=module, inp=user_msg, out=content, model=model,
                  request=render_messages(messages), tin=tin, tout=tout, cost=cost, dur_ms=dur_ms)
        return content

    # -------------------------
    # Step 1: Safety check
    # -------------------------
    safety_prompt = get_active_prompt(conn, "01_safety_and_scope")
    if safety_prompt and settings.OPENAI_API_KEY:
        try:
            safety_out = await run_step(
                "safety_check",
                [{"role": "system", "content": safety_prompt}, {"role": "user", "content": message}],
                safety_model, 30, temp_fast, module="01_safety_and_scope",
            )
            # Permissive gate: only block when the guard explicitly says BLOCKED.
            # Anything else (including the model rambling) is treated as SAFE.
            if safety_out.strip().upper().startswith("BLOCKED"):
                out_stripped = safety_out.strip()
                if ":" in out_stripped:
                    final = out_stripped.split(":", 1)[1].strip()
                else:
                    final = out_stripped
                if not final:
                    final = "I'm not able to help with that request."
                conn.execute(
                    "UPDATE pipeline_runs SET final_response=?, status='blocked', finished_at=datetime('now') WHERE id=?",
                    (final, run_id),
                )
                return {"run_id": run_id, "response": final, "answer_html": final, "kb_used": False}
        except Exception as e:
            logger.warning(f"Safety check skipped: {e}")

    # -------------------------
    # Step 2: Classification
    # -------------------------
    cls = dict(FALLBACK_CLASSIFICATION)
    classifier_prompt = get_active_prompt(conn, "02_query_classifier")
    if classifier_prompt and settings.OPENAI_API_KEY:
        try:
            cls_out = await run_step(
                "classification",
                [{"role": "system", "content": classifier_prompt}, {"role": "user", "content": message}],
                classifier_model, 200, temp_fast, module="02_query_classifier",
            )
            cls = parse_classifier(cls_out)
        except Exception as e:
            logger.warning(f"Classification failed, using fallback: {e}")

    classification = cls["intent"]
    life_area = cls["life_area"]
    emotional_risk = cls["emotional_risk"]

    # Effective style/depth: explicit request overrides classifier, which overrides config default.
    effective_style = (req_answer_style or "").strip().upper()
    if effective_style not in VALID_STYLES:
        effective_style = cls["answer_style"] if cls["answer_style"] in VALID_STYLES else default_style
    if effective_style not in VALID_STYLES:
        effective_style = "BLENDED"
    effective_depth = (req_answer_depth or "").strip().upper()
    if effective_depth not in VALID_DEPTHS:
        effective_depth = cls["answer_depth"] if cls["answer_depth"] in VALID_DEPTHS else default_depth
    if effective_depth not in VALID_DEPTHS:
        effective_depth = "STANDARD"

    # -------------------------
    # Step 3: KB retrieval
    # -------------------------
    kb_text = ""
    kb_used = False
    astrology_intent = classification in {
        "TIMING", "THEMATIC", "MIXED", "FORECAST", "EDUCATIONAL", "CONCEPT_EXPLANATION"}
    want_kb = (cls["needs_kb"] or astrology_intent
               or classification in KB_CLASSIFICATIONS or wants_kb_retrieval(message))
    if enable_kb and want_kb:
        kb_limit = 5 if effective_style == "TECHNICAL" else 3
        kb_text = kb.search(message, kb_limit)
        kb_used = bool(kb_text)
        log_event("kb_retrieval", module="knowledge_base.md",
                  inp=f"[style={effective_style} limit={kb_limit}] {message}",
                  out=kb_text or "(no matching sections)")

    # -------------------------
    # Step 4: Topic-aware chart context (compact, grounded summary)
    # -------------------------
    chart_context = ""
    chart_facts: dict = {}
    if complete_data:
        chart_context, chart_facts = build_topic_chart_summary(
            complete_data, life_area, cls["suggested_divisional_charts"], max_chars)
        log_event("chart_context", module="(topic chart summary)",
                  inp=f"life_area={life_area} divisionals={cls['suggested_divisional_charts']}",
                  out=chart_context)

    # -------------------------
    # Step 5: Chart evidence planner (internal; grounds the draft in real chart facts)
    # -------------------------
    evidence_plan = ""
    if complete_data and settings.OPENAI_API_KEY:
        planner_prompt = get_active_prompt(conn, "03a_chart_evidence_planner")
        if planner_prompt:
            try:
                evidence_plan = await run_step(
                    "evidence_planner",
                    [
                        {"role": "system", "content": planner_prompt},
                        {"role": "user", "content":
                            f"QUESTION:\n{message}\n\nCHART DATA:\n{chart_context or '(none)'}\n\n"
                            f"KNOWLEDGE BASE EXCERPTS:\n{kb_text or '(none)'}"},
                    ],
                    evidence_model, 700, temp_fast, module="03a_chart_evidence_planner",
                )
            except Exception as e:
                logger.warning(f"Evidence planner skipped: {e}")

    # -------------------------
    # Step 6: Build the draft system prompt from the consolidated modules
    # -------------------------
    tmin, tmax = DEPTH_TARGETS.get(effective_depth, DEPTH_TARGETS["STANDARD"])
    system_parts: List[str] = []
    modules_used: List[str] = []

    system_parts.append(
        "You are StarSage, a warm and knowledgeable guide combining Vedic astrology "
        "with psychologically-informed self-exploration.\n"
        f"The user's query has been classified as: {classification} (life area: {life_area}).\n"
        f"ANSWER STYLE: {effective_style}. ANSWER DEPTH: {effective_depth} "
        f"(aim for roughly {tmin}-{tmax} words).\n"
        f"Emotional risk: {emotional_risk}. If MEDIUM or HIGH, use calmer language, more "
        "uncertainty, practical reflection, and user agency; avoid catastrophic framing."
    )

    # Per-request hard style rule (kept prominent so the draft actually obeys the mode).
    STYLE_RULES = {
        "PLAIN": ("HARD RULE (PLAIN STYLE): Do NOT use any astrology terms in the answer — no planet "
                  "names (Sun, Moon, Mars, Mercury, Jupiter, Venus, Saturn, Rahu, Ketu), no signs, no "
                  "house numbers, no dashas, no nakshatras, no Sanskrit words, and do not use the word "
                  "'retrograde'. Translate every chart idea into ordinary life language. Do NOT include "
                  "any kb link markers in this mode. Write pure plain English."),
        "TECHNICAL": ("HARD RULE (TECHNICAL STYLE): First write a short plain-language summary paragraph. "
                      "Then a heading on its own line reading EXACTLY: **Technical basis**. Then list the "
                      "relevant placements, houses, dashas, aspects, or divisional charts, explaining in "
                      "plain words what each one means. The heading must literally read 'Technical basis' "
                      "— do not rename it."),
        "BLENDED": ("HARD RULE (BLENDED STYLE): Lead with the real-life meaning. Use at most 2-4 astrology "
                    "terms total, and immediately translate each into everyday life. No long chains of "
                    "placements."),
    }
    style_rule = STYLE_RULES.get(effective_style, STYLE_RULES["BLENDED"])
    system_parts.append(style_rule)

    def add_module(key: str):
        p = get_active_prompt(conn, key)
        if p:
            system_parts.append(p)
            modules_used.append(key)

    if classification in ("REFLECTIVE", "COACHING"):
        # Psychological mode — lead with human connection.
        add_module("05_psychological_modes")
        if complete_data:
            add_module("03_vedic_analysis")
    else:
        add_module("03_vedic_analysis")
        add_module("04_answer_planning")

    # Writing style governs wording for the selected mode; applies to every response.
    add_module("06_writing_style")

    if evidence_plan:
        system_parts.append(
            "\n\n## Internal Evidence Plan\nUse this as the factual basis for your answer. "
            "Do not repeat it verbatim and do not expose it as chain-of-thought.\n" + evidence_plan)
    if kb_text:
        system_parts.append(f"\n\n## Knowledge Base Reference\n{kb_text}")
    if chart_context:
        system_parts.append(f"\n\n## Chart Data\n{chart_context}")

    system_content = "\n\n".join(p for p in system_parts if p)

    llm_messages = [{"role": "system", "content": system_content}]
    for h in history[-int(get_active_value(conn, "max_history_messages", "10")):]:
        llm_messages.append(h)
    llm_messages.append({"role": "user", "content": message})

    # -------------------------
    # Step 7: Draft answer
    # -------------------------
    draft_tokens = DEPTH_DRAFT_TOKENS.get(effective_depth, 850)
    draft = "I'm unable to generate a response right now — please ensure your OpenAI API key is configured."
    if settings.OPENAI_API_KEY:
        try:
            draft = await run_step(
                "draft_answer", llm_messages, draft_model, draft_tokens, temp_reason,
                module=" + ".join(modules_used) if modules_used else "(none)",
            )
            db_insert(
                conn,
                "INSERT INTO answer_versions (run_id, version, stage, content) VALUES (?,?,?,?)",
                (run_id, 1, "draft", draft),
            )
        except Exception as e:
            logger.error(f"Draft generation failed: {e}")

    # -------------------------
    # Step 8: Critic + rewrite (optional)
    # -------------------------
    final_answer = draft
    if enable_critic and settings.OPENAI_API_KEY and draft:
        critic_prompt = get_active_prompt(conn, "07_answer_critic")
        rewriter_prompt = get_active_prompt(conn, "08_final_rewriter")
        if critic_prompt:
            try:
                critic_out = await run_step(
                    "critic",
                    [
                        {"role": "system", "content": critic_prompt},
                        {"role": "user", "content":
                            f"QUESTION: {message}\n"
                            f"STYLE: {effective_style} | DEPTH: {effective_depth} (target {tmin}-{tmax} words)\n"
                            f"STYLE RULE: {style_rule}\n\n"
                            f"EVIDENCE PLAN:\n{evidence_plan or '(none)'}\n\nDRAFT:\n{draft}"},
                    ],
                    critic_model, 400, temp_fast, module="07_answer_critic",
                )

                # One rewrite pass max, only if the critic asks for revision.
                if "NEEDS_REVISION" in critic_out.upper() and rewriter_prompt:
                    revised = await run_step(
                        "rewrite",
                        [
                            {"role": "system", "content": rewriter_prompt},
                            {"role": "user", "content":
                                f"ORIGINAL DRAFT:\n{draft}\n\nCRITIC:\n{critic_out}\n\n"
                                f"Rewrite in {effective_style} style at {effective_depth} depth "
                                f"(target {tmin}-{tmax} words). Preserve safety wording and uncertainty."},
                        ],
                        draft_model, draft_tokens, temp_reason, module="08_final_rewriter",
                    )
                    final_answer = revised
                    db_insert(
                        conn,
                        "INSERT INTO answer_versions (run_id, version, stage, content) VALUES (?,?,?,?)",
                        (run_id, 2, "rewrite", revised),
                    )
            except Exception as e:
                logger.warning(f"Critic pass failed: {e}")

    # -------------------------
    # Step 9: Format
    # -------------------------
    # Save kb tags first — LLMs sometimes strip [[kb:]] markers during formatting
    kb_tags_pre_format = _extract_kb_tags(final_answer)
    formatter_prompt = get_active_prompt(conn, "09_response_formatter")
    if formatter_prompt and settings.OPENAI_API_KEY and final_answer:
        try:
            formatted = await run_step(
                "formatter",
                [
                    {"role": "system", "content": formatter_prompt},
                    {"role": "user", "content": final_answer},
                ],
                formatter_model, min(draft_tokens + 300, 2000), 0.0,  # extra headroom vs truncation
                module="09_response_formatter",
            )
            final_answer = _reinjector(formatted, kb_tags_pre_format)
        except Exception as e:
            logger.warning(f"Formatter skipped: {e}")

    # -------------------------
    # Step 10: Conditional shorten (only if longer than the depth target)
    # -------------------------
    shortener_prompt = get_active_prompt(conn, "10_answer_shortener")
    wc = word_count(final_answer)
    if shortener_prompt and settings.OPENAI_API_KEY and final_answer and wc > tmax:
        kb_tags_pre_shorten = _extract_kb_tags(final_answer)
        try:
            shortened = await run_step(
                "shortener",
                [
                    {"role": "system", "content": shortener_prompt},
                    {"role": "user", "content":
                        f"ANSWER DEPTH: {effective_depth} (target {tmin}-{tmax} words). "
                        f"Current length: {wc} words. STYLE: {effective_style}.\n\n{final_answer}"},
                ],
                shortener_model, draft_tokens, 0.0, module="10_answer_shortener",
            )
            final_answer = _reinjector(shortened, kb_tags_pre_shorten)
        except Exception as e:
            logger.warning(f"Shortener skipped: {e}")

    # Convert [[kb:term]] markers to clickable HTML links + handle paragraph breaks
    answer_html = format_answer_html(final_answer)

    # Build the compact "Why this answer?" evidence summary (not chain-of-thought).
    used = chart_facts.get("main_chart_factors_used", [])
    hol = chart_facts.get("relevant_house_or_lord", [])
    missing = chart_facts.get("missing_data", [])
    if not complete_data:
        confidence = "LOW"
    elif len(missing) >= 4:
        confidence = "LOW"
    elif used and chart_facts.get("relevant_dasha_or_timing") and hol:
        confidence = "HIGH"
    elif used or hol:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    why_this_answer = {
        "main_chart_factors_used": used[:6],
        "relevant_dasha_or_timing": chart_facts.get("relevant_dasha_or_timing"),
        "relevant_house_or_lord": hol[:6],
        "relevant_divisional_chart": chart_facts.get("relevant_divisional_chart", []),
        "confidence": confidence,
        "missing_data": missing[:8],
    }

    # Persist final answer
    db_insert(
        conn,
        "INSERT INTO answer_versions (run_id, version, stage, content) VALUES (?,?,?,?)",
        (run_id, 3, "final", final_answer),
    )
    conn.execute(
        "UPDATE pipeline_runs SET final_response=?, classification=?, kb_used=?, "
        "total_tokens_in=?, total_tokens_out=?, total_cost_usd=?, "
        "status='done', finished_at=datetime('now') WHERE id=?",
        (final_answer, classification, int(kb_used),
         totals["tokens_in"], totals["tokens_out"], round(totals["cost"], 6), run_id),
    )

    return {
        "run_id": run_id,
        "response": final_answer,
        "answer_html": answer_html,
        "kb_used": kb_used,
        "classification": {
            "intent": classification, "life_area": life_area, "emotional_risk": emotional_risk,
        },
        "answer_style": effective_style,
        "answer_depth": effective_depth,
        "why_this_answer": why_this_answer,
        "model_used": draft_model,
    }
