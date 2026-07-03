# Answer Generation v2

This document describes the upgraded answer pipeline, the style/depth system, the
evidence planner, the critic pass, conditional shortening, the "Why this answer?"
object, safety rules, per-step model config, and feedback collection.

## Pipeline flow

For each chat message (`pipeline.py → run_pipeline`):

1. **Safety gate** (`01_safety_and_scope`, `safety_model`) — permissive; only an explicit
   `BLOCKED` reply stops the run.
2. **Classify** (`02_query_classifier`, `classifier_model`) — returns **JSON only**. Parsed
   by `parse_classifier()` into `intent, life_area, answer_style, answer_depth,
   emotional_risk, needs_kb, needs_divisional_chart, suggested_divisional_charts`. Invalid
   JSON falls back to a safe default object.
3. **Effective style/depth** — resolved as: explicit request field → classifier value →
   config default (`default_answer_style` = BLENDED, `default_answer_depth` = STANDARD).
4. **KB retrieval** (`knowledge_base.md`) — runs for most astrology answers (classifier
   `needs_kb`, astrology intents, KB classes, or trigger phrases). TECHNICAL retrieves more
   (limit 5) than other styles (limit 3).
5. **Topic-aware chart context** (`build_topic_chart_summary`) — a compact, grounded summary
   built from the chart JSON for the classified `life_area` (relevant houses + their lords +
   where the lords sit, relevant planets, current dasha, available divisional charts, full
   planet positions). Nothing is invented; missing data is recorded, not fabricated.
6. **Chart evidence planner** (`03a_chart_evidence_planner`, `evidence_model`) — internal
   step producing a structured evidence plan (facts, KB concepts, supported claims with
   confidence, risky claims to avoid, style guidance). It does **not** write the answer.
7. **Draft** (`draft_model`) — assembled system prompt = identity + style/depth directive +
   a per-request **hard style rule** + analysis/psychological modules + `06_writing_style` +
   the evidence plan + KB + chart summary. Draft token budget scales with depth.
8. **Critic** (`07_answer_critic`, `critic_model`) — checks grounding, specificity, style
   match, depth, tone, safety, and Vedic transparency. Returns `VERDICT: PASS | NEEDS_REVISION`.
9. **Rewrite** (`08_final_rewriter`, `draft_model`) — **at most one** pass, only when the
   critic returns `NEEDS_REVISION`.
10. **Format** (`09_response_formatter`, `formatter_model`) — cleans markdown, **preserves
    section headings** (e.g. `**Technical basis**`) and `[[kb:term]]` markers.
11. **Conditional shorten** (`10_answer_shortener`, `shortener_model`) — runs **only** when
    the answer exceeds the depth word target. No automatic 60–70% trimming anymore.
12. **Linkify** — `[[kb:term]]` → clickable links; blank lines → paragraphs (`answer_html`).

## Style modes (`06_writing_style` + per-request hard rule)

- **PLAIN** — no astrology terms in the body; everything translated into ordinary life
  language; no KB markers.
- **BLENDED** (default) — lead with life meaning; 2–4 terms max, each immediately translated.
- **TECHNICAL** — short plain summary, then a section headed exactly `**Technical basis**`
  with placements/houses/dashas/aspects explained.

## Depth modes and conditional shortening

| Depth | Target words | Draft token budget |
|-------|--------------|--------------------|
| CONCISE | 100–160 | 450 |
| STANDARD | 220–420 | 850 |
| DEEP | 500–900 | 1600 |

The shortener runs only if `word_count(answer) > target_max`; otherwise the answer is
returned unchanged. It preserves the main insight, chart-specific evidence, uncertainty,
safety wording, TECHNICAL detail, and `[[kb:term]]` markers.

## "Why this answer?" object

Returned on `/api/chat` as `why_this_answer` (a brief evidence summary, **not**
chain-of-thought):

```json
{
  "main_chart_factors_used": ["Saturn in Sagittarius (house 10)"],
  "relevant_dasha_or_timing": "Saturn Mahadasha / Saturn Antardasha / Rahu Pratyantardasha",
  "relevant_house_or_lord": ["10th lord Saturn in house 10"],
  "relevant_divisional_chart": ["D10"],
  "confidence": "HIGH",
  "missing_data": []
}
```

Confidence: LOW if no chart or ≥4 missing facts; HIGH if factors + dasha + house-lord all
present; MEDIUM otherwise. The frontend shows this in a collapsible "Why this answer?" panel.

## Safety rules (all paths)

No death/lifespan prediction, medical diagnosis, deterministic doom, legal/financial
instructions, fear-based framing, manipulative remedy upsells, dependency language, or
claims of certainty. For MEDIUM/HIGH `emotional_risk`, the draft directive enforces calmer
language, more uncertainty, practical reflection, and user agency. HEALTH_GENERAL adds a
"general wellbeing only — no medical predictions" note to the chart summary.

## Model config keys (Config tab / `config_options`)

`safety_model`, `classifier_model`, `evidence_model`, `draft_model`, `critic_model`,
`formatter_model`, `shortener_model`. Each is empty by default, meaning: fall back to
`fast_model` (safety/classifier/critic/formatter/shortener) or `reasoning_model`
(evidence/draft). Also: `default_answer_style`, `default_answer_depth`, `enable_critic_pass`
(now **true**).

## Chat API response (backward compatible)

`response` and `answer_html` are unchanged. Additive fields: `answer` (alias),
`user_message_id`, `bot_message_id`, `conversation_id`, `answer_style`, `answer_depth`,
`classifier` (`{intent, life_area, emotional_risk}`), `why_this_answer`, `model_used`.
Requests may include optional `answer_style` / `answer_depth`; omitting them still works.

## Feedback collection flow

1. After each completed bot answer the frontend renders an independent feedback box
   (textarea + Submit) below the answer. It never blocks the conversation.
2. Empty box + continue → nothing happens. Empty box + Submit → gentle "nothing to submit".
3. Non-empty + Submit → `POST /api/feedback`; on success shows "Feedback saved." and disables
   the box (one submit per interaction).
4. Feedback text is treated as untrusted, stored as plain text, never rendered as HTML.

## Feedback table (`answer_feedback`)

Created from `sql/schema.sql` by the seeder, and ensured on app startup
(`ensure_feedback_table`) so it exists even on older databases. Columns: `id`, `created_at`
(UTC ISO), `user_id`, `username`, `session_id`, `conversation_id`, `user_message_id`,
`bot_message_id`, `user_input`, `bot_answer`, `user_feedback`, `answer_style`,
`answer_depth`, `classifier_intent`, `classifier_life_area`, `emotional_risk`, `model_used`,
`app_version`, `metadata_json`.

`POST /api/feedback` validates non-empty feedback and length limits (user_input ≤ 20k,
bot_answer ≤ 50k, user_feedback ≤ 10k) and returns `{ "ok": true, "feedback_id": N }`.
