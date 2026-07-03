# Product Research — Implementation Notes

Implementation-relevant findings that shaped Answer Generation v2. These are the reasons
behind the pipeline and UX choices; the broader product ideas they imply are parked in
[product_backlog.md](product_backlog.md).

- **Users trust transparent chart logic more than opaque AI prediction.** → We added the
  evidence planner and the "Why this answer?" panel so the reasoning is visible and grounded.
- **Users dislike generic, copy-paste, overly vague AI astrology answers.** → The critic
  checks specificity; the topic-aware chart summary and evidence plan force chart-specific
  grounding rather than horoscope-style filler.
- **Users prefer layered explanations: plain summary first, optional technical detail.** →
  Style modes (PLAIN / BLENDED / TECHNICAL); TECHNICAL leads with a plain summary then a
  "Technical basis" section.
- **Beginners need plain language; serious Jyotish users need visible placements, dashas,
  vargas, and methodology.** → PLAIN vs TECHNICAL modes, plus divisional-chart awareness in
  the chart summary and classifier (`suggested_divisional_charts`).
- **Users strongly dislike fear-based, doom-heavy, fatalistic, or relationship-anxiety
  predictions.** → Safety module + per-request emotional-risk handling + critic tone/safety
  checks; timing windows instead of deterministic fate.
- **Users distrust aggressive monetization, hidden subscriptions, ads, and remedy upsells.**
  → No monetization added; safety rules forbid manipulative remedy upsells and dependency
  language. (Monetization is explicitly backlogged, not built.)
- **Users value privacy clarity because birth data is sensitive.** → Feedback text stored as
  plain text and never rendered as HTML; secrets stay in `.env`. A privacy/methodology
  surface is backlogged.
- **Users like emotionally safe self-reflection, weekly themes, and clear methodology.** →
  Reflective/coaching modes and calmer language for sensitive questions; methodology is
  partly surfaced via "Why this answer?".
- **For this test version, answer-level feedback is high-leverage.** → We shipped a
  lightweight per-answer feedback box and an `answer_feedback` table to gather targeted data
  before investing in larger product features.
