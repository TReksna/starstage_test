# StarSage

StarSage is a local **Vedic astrology + psychologically-informed self-exploration**
assistant. It computes a real birth chart on your own machine (no external astrology
API), then runs a multi-step LLM pipeline to answer questions about the chart — or to
reflect with the user about feelings, meaning, and direction — in warm, plain language.

It ships with a single-page web workspace (`index_visual.html`) that has five tabs:
**Chat**, **Prompt Modules**, **Config Options**, **Knowledge Base**, and **Logs**.

---

## 1. What it does

- **Computes charts locally** with [`jyotishyamitra`](https://pypi.org/project/jyotishyamitra/)
  + `pyswisseph`. Birth city is geocoded with Google Maps (place + timezone).
- **Answers in a human voice.** Astrology jargon is kept out of the prose and surfaced
  only as clickable references in parentheses at the end of sentences
  (e.g. *"…the next two years look open for new connections ([[kb:Mercury Antardasha]])."*).
- **Routes by intent.** Emotional / meaning / coaching questions get person-centered
  reflection, not a chart dump. Chart questions get a structured Vedic reading.
- **Is fully inspectable.** Every prompt module and config value is versioned in SQLite
  and editable from the UI. Every answer is logged step-by-step with the exact request
  sent to each model, the response, token counts, and the **USD cost** of each call.

---

## 2. Architecture & file layout

```
app.py                  FastAPI HTTP layer: routes, request models, serves the frontend
pipeline.py             The answer pipeline (safety → classify → … → shorten) + cost logging
db.py                   SQLite connection, query helpers, versioned prompt/config accessors
llm.py                  OpenAI call wrapper + per-call cost accounting (pricing table)
geocoding.py            Google Maps place + timezone lookup for birth data
astro.py                jyotishyamitra wrapper + South-Indian SVG chart renderer
settings.py             pydantic-settings loaded from .env

knowledge_base.md       The ENTIRE knowledge base — one clean, numbered markdown document
build_knowledge_base.py One-shot builder that produced knowledge_base.md from source files
knowledge_base.py       Parses knowledge_base.md into navigable sections (tree/resolve/search)
seed_database.py        Creates the schema and seeds the 10 prompt modules + config options
sql/schema.sql          The database schema (read by the seeder)
prompt_modules/         Plain-text backup of every prompt module (kept in sync with the DB)
index_visual.html       Single-page frontend (5 tabs)

starsage.sqlite3        The database (users, conversations, prompts, config, run logs)
user_astro_data/        Per-user computed chart JSON + cached chart SVGs
.env / .env.example     Secrets (never committed) / template
```

### Module dependency graph

```
settings  ─┬─────────────┬──────────────┬──────────────────┐
           ▼             ▼              ▼                   ▼
          db.py        llm.py       geocoding.py     knowledge_base.py
           └─────┬───────┘                                 │
                 ▼                                          │
             pipeline.py ◄─────────────────────────────────┘
                 ▼
               app.py  ◄── astro.py
```

`db`, `llm`, `geocoding`, `knowledge_base`, and `astro` are leaf modules.
`pipeline` orchestrates them. `app` is the web layer on top. No circular imports.

---

## 3. The answer pipeline (`pipeline.py → run_pipeline`)

Each chat message flows through these stages. Steps 1, 2, 6, 7, 8, 9 are LLM calls and
are individually logged with model, tokens, and cost.

| # | Stage | Module / source | Model | Notes |
|---|-------|-----------------|-------|-------|
| 1 | **Safety gate** | `01_safety_and_scope` | fast | Replies `SAFE` or `BLOCKED: …`. Permissive: only an explicit `BLOCKED` stops the run. |
| 2 | **Classify** | `02_query_classifier` | fast | One of TIMING, THEMATIC, MIXED, FORECAST, EDUCATIONAL, REFLECTIVE, COACHING, CONCEPT_EXPLANATION. |
| 3 | **KB retrieval** | `knowledge_base.md` | — | Keyword search over KB sections; injected when the class is knowledge-bearing or the message asks to "explain / what is …". |
| 4 | **Chart context** | `user_astro_data/…json` | — | Pulls only the chart paths relevant to the class (timing vs thematic, etc.). |
| 5 | **Assemble system prompt** | see below | — | Picks which prompt modules go into the draft call. |
| 6 | **Draft** | modules from step 5 | reasoning | Writes the answer. |
| 7 | **Critic + rewrite** | `07_answer_critic`, `08_final_rewriter` | fast / reasoning | **On by default** (`enable_critic_pass`). One rewrite max, only if the critic says `NEEDS_REVISION`. |
| 8 | **Format** | `09_response_formatter` | fast | Cleans markdown, fixes paragraph spacing, preserves `[[kb:…]]` markers. |
| 9 | **Shorten** | `10_answer_shortener` | fast | **Conditional** — only if the answer exceeds the depth word target. |

> **Answer generation v2:** the pipeline also runs a JSON classifier (intent, life area,
> style, depth, risk), a **chart evidence planner** before the draft, topic-aware chart
> context, PLAIN/BLENDED/TECHNICAL style modes with CONCISE/STANDARD/DEEP depth, and returns
> a "Why this answer?" object. See [docs/answer_generation_v2.md](docs/answer_generation_v2.md).
| 10 | **Linkify** | `format_answer_html` | — | Converts `[[kb:term]]` → clickable `<a class="kb-link">` and `\n\n` → paragraph breaks. |

**Step 5 — which modules enter the draft:**

- `REFLECTIVE` / `COACHING` → `05_psychological_modes` (+ `03_vedic_analysis` *only if* the
  user has a chart). Leads with human warmth.
- everything else → `03_vedic_analysis` + `04_answer_planning` (a private reasoning checklist).
- **all** responses → `06_writing_style` (the Hemingway/parenthetical-reference contract,
  which explicitly overrides the analysis modules for wording).

`[[kb:term]]` markers are protected: before the format and shorten calls their terms are
recorded, and if a model strips them they are re-injected afterward.

---

## 4. Prompt modules

There are **11 modules** (including `03a_chart_evidence_planner`), stored versioned in SQLite and mirrored as plain text in
`prompt_modules/<key>.txt`. Editing a module in the **Prompt Modules** tab creates a new
version *and* rewrites its `.txt` backup, so the folder always reflects the live prompts.

| Order | Key | Role |
|------|-----|------|
| 1 | `01_safety_and_scope` | Safety/scope gate (broad allow, narrow refuse) |
| 2 | `02_query_classifier` | Routes the message into one of 8 classes |
| 3 | `03_vedic_analysis` | Core interpretation knowledge (principles, houses, nakshatras, lords, aspects, divisionals, yogas, dasha, karma, validation) |
| 4 | `04_answer_planning` | Internal reasoning checklist per query type |
| 5 | `05_psychological_modes` | Reflection, CBT-style inquiry, soft descriptors, coaching, mode-selection, referral |
| 6 | `06_writing_style` | Hemingway tone; astrology only as end-of-sentence `[[kb:…]]` refs; real-life anchoring |
| 7 | `07_answer_critic` | Reviews a draft (accuracy, vagueness, tone, length) |
| 8 | `08_final_rewriter` | Rewrites per critic notes |
| 9 | `09_response_formatter` | Final markdown cleanup |
| 10 | `10_answer_shortener` | Compress to 60–70% |

---

## 5. Knowledge base

The knowledge base is **one markdown file**, `knowledge_base.md` — it is **not** stored in
the database. It is a single, de-duplicated, consistently numbered document (sections
`1`, `2`, `2.1`, `4.3.1`, …) covering Vedic concepts (graha, bhāva, rāśi, nakshatras,
dignity, avastha, aspects, lordship, divisional charts, yogas, dasha) and the
psychological-translation / safety layer.

`knowledge_base.py` parses it on demand (reloading when the file changes) and exposes:

- `tree()` — the numbered section tree for the Knowledge Base tab.
- `section(number)` — full text of one section.
- `resolve(term)` — maps a `[[kb:term]]` keyword (e.g. *"Mercury Antardasha"*, *"7th House"*,
  *"Raja Yoga"*) to the best section, via an alias map then fuzzy fallback.
- `search(query, n)` — keyword retrieval used inside the pipeline.

Clicking a reference link in a chat answer calls `resolve`, opens the Knowledge Base tab,
expands the tree to that section, and shows it.

> `build_knowledge_base.py` is the one-shot tool that produced `knowledge_base.md` from the
> three original source files (stripping prompt-instruction junk, removing duplicate
> overviews, and renumbering). You normally edit `knowledge_base.md` directly; re-run the
> builder only if you want to regenerate it from the original sources.

---

## 6. Config options

Editable in the **Config Options** tab (versioned, like prompts):

| Key | Default | Meaning |
|-----|---------|---------|
| `fast_model` | `gpt-4o-mini` | Safety, classify, format, shorten, critic |
| `reasoning_model` | `gpt-4o` | Draft + rewrite |
| `enable_critic_pass` | `true` | Run the critic + rewriter loop (one rewrite max) |
| `default_answer_style` | `BLENDED` | PLAIN / BLENDED / TECHNICAL when unset by request/classifier |
| `default_answer_depth` | `STANDARD` | CONCISE / STANDARD / DEEP when unset |
| `safety_model` … `shortener_model` | `""` | Per-step model overrides (empty = fast/reasoning) |
| `enable_kb_retrieval` | `true` | Inject KB sections into the prompt |
| `max_history_messages` | `10` | Prior turns sent as context |
| `chart_context_max_chars` | `8000` | Cap on chart JSON in the prompt |
| `answer_max_tokens` | `1000` | Max tokens for the draft |
| `temperature_fast` | `0.1` | Temperature for fast-model calls |
| `temperature_reasoning` | `0.7` | Temperature for draft/rewrite |
| `provider` | `openai` | LLM provider |

---

## 7. Logging & cost

Every run is recorded in `pipeline_runs`; every step in `pipeline_steps`. The **Logs** tab
shows, for a run:

- the user message + timestamp, the classification, and whether KB was used;
- each step **in order**: step name, the prompt module(s) used, the model, the **full
  request sent to the model**, the **full response**, tokens in/out, the **USD cost**, and
  the duration;
- the **final message** returned to the user;
- the **total cost** and total tokens for the whole answer.

Costs are computed in `llm.py` from a per-model pricing table (USD per 1M tokens, longest
prefix match). Update `MODEL_PRICING` there if prices change.

---

## 8. Database

SQLite (WAL mode). Schema in `sql/schema.sql`. Main tables:

- `users`, `conversations`, `messages` — chat data.
- `prompt_modules` / `prompt_versions`, `config_options` / `config_versions` — versioned,
  with an `active_version_id` pointer.
- `pipeline_runs`, `pipeline_steps`, `answer_versions` — detailed run logs (incl. tokens & cost).
- `snapshots` — named captures of the active prompt/config state.

The seeder is **non-destructive to user data**: it replaces the prompt-module set and adds
any missing config options, but leaves users/conversations/logs intact. It also runs small
idempotent migrations so an existing database gains newer columns.

---

## 9. Setup & run

### Prerequisites
- Python 3.11+
- Keys in `.env` (see `.env.example`): `OPENAI_API_KEY`, `GOOGLE_MAPS_API_KEY`.
  `.env` is gitignored — never commit real keys.

### Install
```bash
pip install -r requirements.txt
```

### Seed the database
```bash
python seed_database.py --db starsage.sqlite3
```

### Run
```bash
uvicorn app:app --reload --port 8120
```
Open <http://127.0.0.1:8120/>. (The same config is in `.claude/launch.json` as **FastAPI**.)

### (Optional) rebuild the knowledge base from source
```bash
python build_knowledge_base.py
```

---

## 10. HTTP API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Serves the frontend |
| GET | `/health`, `/healthz` | Health check |
| POST | `/api/users/init` | Geocode + compute a chart, store the user |
| POST | `/api/get_horoscope` | Render a divisional chart SVG |
| POST | `/api/chat` | Run the pipeline; returns `response`, `answer_html`, `kb_used` |
| GET | `/api/conversations/{id}/messages` | Conversation history |
| GET | `/api/admin/prompt-modules` (+ `/{id}/active`, `POST /{id}/versions`) | Manage prompts |
| GET | `/api/admin/config-options` (+ `/{id}/active`, `POST /{id}/versions`) | Manage config |
| GET | `/api/kb/tree` | KB section tree |
| GET | `/api/kb/section/{number}` | One KB section |
| GET | `/api/kb/resolve?term=…` | Resolve a `[[kb:term]]` to a section |
| GET | `/api/admin/pipeline-runs` (+ `/{id}`) | Run logs (list + detail) |
| POST | `/api/snapshots` | Snapshot the active prompt/config state |

---

## 11. Data flow (end to end)

1. **Onboard** — `POST /api/users/init` geocodes the birth city (`geocoding.py`), computes
   the chart locally (`astro.py`), writes `user_astro_data/<id>/…json`, and stores the user.
2. **Ask** — `POST /api/chat` saves the message, builds history, loads the chart JSON, and
   calls `run_pipeline` (`pipeline.py`).
3. **Pipeline** — safety → classify → KB → chart → draft → (critic) → format → shorten,
   logging every step and cost into the database (`db.py`).
4. **Reply** — the final text plus `answer_html` (with clickable `[[kb:…]]` links) is
   returned and rendered in the Chat tab.
5. **Inspect** — the Logs tab shows the full per-step breakdown and total cost; the Prompt
   Modules / Config tabs let you edit and re-version the behavior live.

---

## 12. Security notes

- Secrets live only in `.env` (gitignored). `.env.example` lists variable names with no values.
- The safety module refuses system-prompt/key exfiltration, medical/legal/financial
  directives, death prediction, active-crisis handling, and manipulation — while
  deliberately allowing emotional, reflective, and meaning questions.
