"""StarSage FastAPI application — HTTP layer.

Routes, request models, and the static frontend. Business logic lives in
pipeline.py (the LLM pipeline), db.py (database), llm.py (model calls/cost),
geocoding.py (maps), knowledge_base.py (the markdown KB), and astro.py (charts).
"""
import json
import logging
import os
import uuid
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

load_dotenv(".env")

from settings import settings
from astro import extract_and_store_user_data, render_chart_svg_from_complete_data
import knowledge_base as kb
from db import DB_PATH, get_db, db_row, db_rows, db_insert, compute_state_hash, write_prompt_backup
from geocoding import GOOGLE_MAPS_API_KEY, get_geo_data_for_user
from pipeline import run_pipeline, format_answer_html

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("app.log", encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


APP_VERSION = "2.1.0"
app = FastAPI(title="StarSage", version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# answer_feedback table — created on startup if it does not already exist, so
# feedback works even against a database seeded before this feature was added.
FEEDBACK_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS answer_feedback (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    user_id               TEXT,
    username              TEXT,
    session_id            TEXT,
    conversation_id       TEXT,
    user_message_id       INTEGER,
    bot_message_id        INTEGER,
    user_input            TEXT NOT NULL,
    bot_answer            TEXT NOT NULL,
    user_feedback         TEXT NOT NULL,
    answer_style          TEXT,
    answer_depth          TEXT,
    classifier_intent     TEXT,
    classifier_life_area  TEXT,
    emotional_risk        TEXT,
    model_used            TEXT,
    app_version           TEXT,
    metadata_json         TEXT
);
"""


def ensure_feedback_table() -> None:
    with get_db() as conn:
        conn.execute(FEEDBACK_TABLE_SQL)


@app.on_event("startup")
async def _on_startup():
    try:
        ensure_feedback_table()
    except Exception as e:
        logger.warning(f"Could not ensure answer_feedback table: {e}")

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class UserInitRequest(BaseModel):
    name: str
    birth_date: int = Field(ge=1, le=31)
    birth_month: int = Field(ge=1, le=12)
    birth_year: int = Field(ge=1900, le=2100)
    birth_hour: int = Field(ge=0, le=23)
    birth_min: int = Field(ge=0, le=59)
    birth_city: str
    user_id: Optional[str] = None
    gender: Optional[str] = "others"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    timezone: Optional[float] = None


class ChatRequest(BaseModel):
    user_id: str
    message: str
    conversation_id: Optional[str] = None
    # Optional answer controls. Old clients that omit these still work.
    answer_style: Optional[str] = None   # PLAIN | BLENDED | TECHNICAL
    answer_depth: Optional[str] = None   # CONCISE | STANDARD | DEEP


class FeedbackRequest(BaseModel):
    user_input: str
    bot_answer: str
    user_feedback: str
    user_id: Optional[str] = None
    username: Optional[str] = None
    session_id: Optional[str] = None
    conversation_id: Optional[str] = None
    user_message_id: Optional[int] = None
    bot_message_id: Optional[int] = None
    answer_style: Optional[str] = None
    answer_depth: Optional[str] = None
    classifier_intent: Optional[str] = None
    classifier_life_area: Optional[str] = None
    emotional_risk: Optional[str] = None
    model_used: Optional[str] = None
    metadata_json: Optional[str] = None


class PromptVersionRequest(BaseModel):
    content: str
    notes: Optional[str] = ""


class ConfigVersionRequest(BaseModel):
    value: str
    notes: Optional[str] = ""


class SnapshotRequest(BaseModel):
    comment: Optional[str] = ""


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

FRONTEND_PATH = Path(__file__).parent / "index_visual.html"


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    if FRONTEND_PATH.exists():
        return HTMLResponse(FRONTEND_PATH.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>StarSage</h1><p>Frontend not found. Place index_visual.html in the project root.</p>")


@app.get("/health")
@app.get("/healthz")
async def health():
    db_ok = os.path.exists(DB_PATH)
    return {"status": "ok" if db_ok else "degraded", "db": DB_PATH, "db_exists": db_ok}


# ---------------------------------------------------------------------------
# User endpoints
# ---------------------------------------------------------------------------

@app.post("/api/users/init")
async def init_user(req: UserInitRequest):
    user_dict = req.dict()
    user_id = user_dict.get("user_id") or f"user_{uuid.uuid4().hex[:8]}"
    user_dict["user_id"] = user_id

    # Geocode if coordinates are missing
    if (user_dict.get("latitude") is None or user_dict.get("longitude") is None
            or user_dict.get("timezone") is None):
        if GOOGLE_MAPS_API_KEY:
            try:
                user_dict = await get_geo_data_for_user(user_dict)
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Geo lookup failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        else:
            raise HTTPException(status_code=400, detail="latitude/longitude/timezone required (no Google Maps key configured)")

    # Extract local astrology
    try:
        user_id = extract_and_store_user_data(user_dict)
    except Exception as e:
        logger.error(f"Astrology extraction failed: {e}")
        raise HTTPException(status_code=500, detail=f"Extraction failed: {e}")

    # Persist to SQLite
    astro_path = os.path.join("user_astro_data", user_id, f"{user_dict['name']}_complete_astro_data.json")
    with get_db() as conn:
        existing = db_row(conn, "SELECT user_id FROM users WHERE user_id=?", (user_id,))
        if not existing:
            db_insert(
                conn,
                "INSERT INTO users (user_id, name, birth_data, astro_path) VALUES (?,?,?,?)",
                (user_id, user_dict["name"], json.dumps(user_dict), astro_path),
            )
        else:
            conn.execute(
                "UPDATE users SET name=?, birth_data=?, astro_path=? WHERE user_id=?",
                (user_dict["name"], json.dumps(user_dict), astro_path, user_id),
            )

    return {
        "user_id": user_id,
        "name": user_dict["name"],
        "birth_city": user_dict.get("birth_city"),
        "latitude": user_dict.get("latitude"),
        "longitude": user_dict.get("longitude"),
        "timezone": user_dict.get("timezone"),
    }


# ---------------------------------------------------------------------------
# Horoscope chart
# ---------------------------------------------------------------------------

CHART_MAP = {
    "birth_chart": "D1", "moon_chart": "MOON", "sun_chart": "SUN",
    "navamsa_chart": "D9", "hora_chart": "D2", "dreshkan_chart": "D3",
    "chaturthamsha_chart": "D4", "panchmansha_chart": "D5",
    "saptamansha_chart": "D7", "ashtamansha_chart": "D8",
    "dashamansha_chart": "D10", "dwadashamsha_chart": "D12",
    "shodashamsha_chart": "D16", "vishamansha_chart": "D20",
    "chaturvimshamsha_chart": "D24", "bhamsha_chart": "D27",
    "trishamansha_chart": "D30", "khavedamsha_chart": "D40",
    "akshvedansha_chart": "D45", "shashtymsha_chart": "D60",
    "chalit_chart": "chalit",
}


@app.post("/api/get_horoscope")
async def get_horoscope(user_id: str, chart_type: str = "birth_chart"):
    if chart_type not in CHART_MAP:
        raise HTTPException(status_code=400, detail=f"Invalid chart_type. Options: {', '.join(CHART_MAP)}")

    with get_db() as conn:
        user_row = db_row(conn, "SELECT name, astro_path FROM users WHERE user_id=?", (user_id,))

    if not user_row:
        raise HTTPException(status_code=404, detail="User not found. Run /api/users/init first.")

    complete_data = None
    if user_row["astro_path"] and os.path.exists(user_row["astro_path"]):
        with open(user_row["astro_path"], encoding="utf-8") as f:
            complete_data = json.load(f)

    if not complete_data:
        raise HTTPException(status_code=404, detail="Astro data not found. Run /api/users/init first.")

    cache_dir = os.path.join("user_astro_data", user_id, "horoscope_cache")
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"{chart_type}.svg")

    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            return {"status": "success", "cached": True, "chart_type": chart_type, "svg": f.read()}

    try:
        svg = render_chart_svg_from_complete_data(complete_data, CHART_MAP[chart_type])
    except Exception as e:
        logger.error(f"SVG render failed: {e}")
        raise HTTPException(status_code=500, detail=f"Chart rendering failed: {e}")

    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(svg)

    return {"status": "success", "cached": False, "chart_type": chart_type, "svg": svg}


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

@app.post("/api/chat")
async def chat(req: ChatRequest):
    with get_db() as conn:
        user_row = db_row(conn, "SELECT name, astro_path FROM users WHERE user_id=?", (req.user_id,))
        if not user_row:
            raise HTTPException(status_code=404, detail="User not found. Run /api/users/init first.")

        conv_id = req.conversation_id
        if not conv_id:
            conv_id = str(uuid.uuid4())
            db_insert(
                conn,
                "INSERT INTO conversations (id, user_id, title) VALUES (?,?,?)",
                (conv_id, req.user_id, req.message[:60]),
            )
        else:
            existing_conv = db_row(conn, "SELECT id FROM conversations WHERE id=?", (conv_id,))
            if not existing_conv:
                db_insert(
                    conn,
                    "INSERT INTO conversations (id, user_id, title) VALUES (?,?,?)",
                    (conv_id, req.user_id, req.message[:60]),
                )

        # Save user message
        user_message_id = db_insert(
            conn,
            "INSERT INTO messages (conversation_id, role, content) VALUES (?,?,?)",
            (conv_id, "user", req.message),
        )

        # Build history
        history_rows = db_rows(
            conn,
            "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id",
            (conv_id,),
        )
        history = [{"role": r["role"], "content": r["content"]} for r in history_rows[:-1]]

        # Load complete astro data
        complete_data = None
        if user_row["astro_path"] and os.path.exists(user_row["astro_path"]):
            with open(user_row["astro_path"], encoding="utf-8") as f:
                complete_data = json.load(f)

        result = await run_pipeline(
            conn, req.user_id, conv_id, req.message, history, complete_data,
            req_answer_style=req.answer_style, req_answer_depth=req.answer_depth,
        )

        # Save assistant message
        bot_message_id = db_insert(
            conn,
            "INSERT INTO messages (conversation_id, role, content) VALUES (?,?,?)",
            (conv_id, "assistant", result["response"]),
        )

        conn.execute("UPDATE conversations SET updated_at=datetime('now') WHERE id=?", (conv_id,))

    # Backward compatible: `response` and `answer_html` remain; everything else is additive.
    return {
        "conversation_id": conv_id,
        "run_id": result["run_id"],
        "response": result["response"],
        "answer": result["response"],
        "answer_html": result.get("answer_html", format_answer_html(result["response"])),
        "kb_used": result["kb_used"],
        "user_message_id": user_message_id,
        "bot_message_id": bot_message_id,
        "answer_style": result.get("answer_style"),
        "answer_depth": result.get("answer_depth"),
        "classifier": result.get("classification"),
        "why_this_answer": result.get("why_this_answer"),
        "model_used": result.get("model_used"),
    }


@app.get("/api/conversations/{conversation_id}/messages")
async def get_messages(conversation_id: str):
    with get_db() as conn:
        rows = db_rows(
            conn,
            "SELECT id, role, content, created_at FROM messages WHERE conversation_id=? ORDER BY id",
            (conversation_id,),
        )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Answer feedback (test-version collection)
# ---------------------------------------------------------------------------

@app.post("/api/feedback")
async def submit_feedback(req: FeedbackRequest):
    # Feedback text is required and must not be only whitespace.
    if not (req.user_feedback and req.user_feedback.strip()):
        raise HTTPException(status_code=400, detail="Feedback text is required.")

    # Reasonable length limits (reject oversized payloads).
    if len(req.user_input or "") > 20_000:
        raise HTTPException(status_code=400, detail="user_input too long (max 20000 chars).")
    if len(req.bot_answer or "") > 50_000:
        raise HTTPException(status_code=400, detail="bot_answer too long (max 50000 chars).")
    if len(req.user_feedback) > 10_000:
        raise HTTPException(status_code=400, detail="user_feedback too long (max 10000 chars).")

    try:
        ensure_feedback_table()
        with get_db() as conn:
            feedback_id = db_insert(
                conn,
                "INSERT INTO answer_feedback (user_id, username, session_id, conversation_id, "
                "user_message_id, bot_message_id, user_input, bot_answer, user_feedback, "
                "answer_style, answer_depth, classifier_intent, classifier_life_area, "
                "emotional_risk, model_used, app_version, metadata_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    req.user_id, req.username, req.session_id, req.conversation_id,
                    req.user_message_id, req.bot_message_id,
                    req.user_input or "", req.bot_answer or "", req.user_feedback,
                    req.answer_style, req.answer_depth, req.classifier_intent,
                    req.classifier_life_area, req.emotional_risk, req.model_used,
                    APP_VERSION, req.metadata_json,
                ),
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Feedback save failed: {e}")
        raise HTTPException(status_code=500, detail="Could not save feedback.")

    return {"ok": True, "feedback_id": feedback_id}


# ---------------------------------------------------------------------------
# Admin: Prompt Modules
# ---------------------------------------------------------------------------

@app.get("/api/admin/prompt-modules")
async def list_prompt_modules():
    with get_db() as conn:
        rows = db_rows(
            conn,
            """SELECT pm.id, pm.module_key, pm.name, pm.description, pm.category,
                      pm.active_version_id, pm.created_at,
                      pv.content as active_content, pv.version as active_version
               FROM prompt_modules pm
               LEFT JOIN prompt_versions pv ON pm.active_version_id = pv.id
               ORDER BY pm.module_key""",
        )
    return [dict(r) for r in rows]


@app.get("/api/admin/prompt-modules/{module_id}/active")
async def get_prompt_module_active(module_id: int):
    with get_db() as conn:
        row = db_row(
            conn,
            """SELECT pm.id, pm.module_key, pm.name, pm.description, pm.category,
                      pv.id as version_id, pv.version, pv.content, pv.notes, pv.created_at
               FROM prompt_modules pm
               LEFT JOIN prompt_versions pv ON pm.active_version_id = pv.id
               WHERE pm.id = ?""",
            (module_id,),
        )
    if not row:
        raise HTTPException(status_code=404, detail="Module not found")
    return dict(row)


@app.post("/api/admin/prompt-modules/{module_id}/versions")
async def create_prompt_version(module_id: int, req: PromptVersionRequest):
    with get_db() as conn:
        mod = db_row(conn, "SELECT id, module_key FROM prompt_modules WHERE id=?", (module_id,))
        if not mod:
            raise HTTPException(status_code=404, detail="Module not found")

        last = db_row(
            conn,
            "SELECT MAX(version) as v FROM prompt_versions WHERE module_id=?",
            (module_id,),
        )
        next_v = (last["v"] or 0) + 1

        new_id = db_insert(
            conn,
            "INSERT INTO prompt_versions (module_id, version, content, notes) VALUES (?,?,?,?)",
            (module_id, next_v, req.content, req.notes or ""),
        )
        conn.execute("UPDATE prompt_modules SET active_version_id=? WHERE id=?", (new_id, module_id))

    # Keep the plain-text backup in sync with the active version.
    write_prompt_backup(mod["module_key"], req.content)

    return {"version_id": new_id, "version": next_v, "active": True}


# ---------------------------------------------------------------------------
# Admin: Config Options
# ---------------------------------------------------------------------------

@app.get("/api/admin/config-options")
async def list_config_options():
    with get_db() as conn:
        rows = db_rows(
            conn,
            """SELECT co.id, co.option_key, co.name, co.description, co.value_type,
                      co.active_version_id, co.created_at,
                      cv.value as active_value, cv.version as active_version
               FROM config_options co
               LEFT JOIN config_versions cv ON co.active_version_id = cv.id
               ORDER BY co.option_key""",
        )
    return [dict(r) for r in rows]


@app.get("/api/admin/config-options/{option_id}/active")
async def get_config_option_active(option_id: int):
    with get_db() as conn:
        row = db_row(
            conn,
            """SELECT co.id, co.option_key, co.name, co.description, co.value_type,
                      cv.id as version_id, cv.version, cv.value, cv.notes, cv.created_at
               FROM config_options co
               LEFT JOIN config_versions cv ON co.active_version_id = cv.id
               WHERE co.id = ?""",
            (option_id,),
        )
    if not row:
        raise HTTPException(status_code=404, detail="Config option not found")
    return dict(row)


@app.post("/api/admin/config-options/{option_id}/versions")
async def create_config_version(option_id: int, req: ConfigVersionRequest):
    with get_db() as conn:
        opt = db_row(conn, "SELECT id FROM config_options WHERE id=?", (option_id,))
        if not opt:
            raise HTTPException(status_code=404, detail="Config option not found")

        last = db_row(
            conn,
            "SELECT MAX(version) as v FROM config_versions WHERE option_id=?",
            (option_id,),
        )
        next_v = (last["v"] or 0) + 1

        new_id = db_insert(
            conn,
            "INSERT INTO config_versions (option_id, version, value, notes) VALUES (?,?,?,?)",
            (option_id, next_v, req.value, req.notes or ""),
        )
        conn.execute("UPDATE config_options SET active_version_id=? WHERE id=?", (new_id, option_id))

    return {"version_id": new_id, "version": next_v, "active": True}


# ---------------------------------------------------------------------------
# Knowledge Base  (sourced from knowledge_base.md, not the database)
# ---------------------------------------------------------------------------

@app.get("/api/kb/tree")
async def kb_tree():
    """Return the numbered section tree for the Knowledge Base tab."""
    return {"sections": kb.tree()}


@app.get("/api/kb/section/{number}")
async def kb_section(number: str):
    """Return the full content of one KB section by its number (e.g. '3.1')."""
    sec = kb.section(number)
    if not sec:
        raise HTTPException(status_code=404, detail=f"Section not found: {number}")
    return sec


@app.get("/api/kb/resolve")
async def kb_resolve(term: str):
    """Resolve a [[kb:term]] keyword to its best-matching section number."""
    res = kb.resolve(term)
    if not res:
        raise HTTPException(status_code=404, detail=f"Term not found: {term}")
    return res


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

@app.post("/api/snapshots")
async def create_snapshot(req: SnapshotRequest):
    with get_db() as conn:
        phash, chash = compute_state_hash(conn)
        active_modules = [
            {"module_key": r["module_key"], "version_id": r["active_version_id"]}
            for r in db_rows(conn, "SELECT module_key, active_version_id FROM prompt_modules ORDER BY module_key")
        ]
        active_configs = [
            {"option_key": r["option_key"], "version_id": r["active_version_id"]}
            for r in db_rows(conn, "SELECT option_key, active_version_id FROM config_options ORDER BY option_key")
        ]
        snap_id = db_insert(
            conn,
            "INSERT INTO snapshots (comment, prompt_hash, config_hash, active_modules, active_configs) VALUES (?,?,?,?,?)",
            (req.comment or "", phash, chash, json.dumps(active_modules), json.dumps(active_configs)),
        )
    return {"snapshot_id": snap_id, "prompt_hash": phash, "config_hash": chash}


# ---------------------------------------------------------------------------
# Pipeline run logs
# ---------------------------------------------------------------------------

@app.get("/api/admin/pipeline-runs")
async def list_pipeline_runs(limit: int = 50):
    with get_db() as conn:
        rows = db_rows(
            conn,
            "SELECT id, user_id, conversation_id, user_message, classification, status, kb_used, "
            "fast_model, reason_model, total_tokens_in, total_tokens_out, total_cost_usd, "
            "started_at, finished_at FROM pipeline_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
    return [dict(r) for r in rows]


@app.get("/api/admin/pipeline-runs/{run_id}")
async def get_pipeline_run(run_id: str):
    with get_db() as conn:
        run = db_row(conn, "SELECT * FROM pipeline_runs WHERE id=?", (run_id,))
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        steps = db_rows(
            conn,
            "SELECT step_index, step_name, prompt_module, model, request_text, input_text, "
            "output_text, tokens_in, tokens_out, cost_usd, duration_ms, created_at "
            "FROM pipeline_steps WHERE run_id=? ORDER BY step_index, id",
            (run_id,),
        )
        versions = db_rows(
            conn,
            "SELECT version, stage, content, created_at FROM answer_versions WHERE run_id=? ORDER BY version",
            (run_id,),
        )
    return {
        "run": dict(run),
        "steps": [dict(s) for s in steps],
        "answer_versions": [dict(v) for v in versions],
    }
