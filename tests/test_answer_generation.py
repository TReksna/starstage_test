"""Answer-generation tests that do not require any LLM calls."""
import pipeline
import seed_database as seed


# --- classifier parsing -----------------------------------------------------

def test_classifier_fallback_on_bad_json():
    out = pipeline.parse_classifier("this is not json at all")
    assert out["intent"] == "MIXED"
    assert out["life_area"] == "OTHER"
    assert out["answer_style"] == "BLENDED"
    assert out["answer_depth"] == "STANDARD"
    assert out["emotional_risk"] == "LOW"
    assert out["needs_kb"] is True
    assert out["needs_divisional_chart"] is False
    assert out["suggested_divisional_charts"] == []


def test_classifier_parses_valid_json():
    j = ('{"intent":"TIMING","life_area":"CAREER","answer_style":"TECHNICAL",'
         '"answer_depth":"DEEP","emotional_risk":"LOW","needs_kb":true,'
         '"needs_divisional_chart":true,"suggested_divisional_charts":["D10"]}')
    out = pipeline.parse_classifier(j)
    assert out["intent"] == "TIMING"
    assert out["life_area"] == "CAREER"
    assert out["answer_style"] == "TECHNICAL"
    assert out["answer_depth"] == "DEEP"
    assert out["suggested_divisional_charts"] == ["D10"]


def test_classifier_invalid_field_falls_back_but_keeps_valid():
    out = pipeline.parse_classifier('{"intent":"NONSENSE","life_area":"MONEY"}')
    assert out["intent"] == "MIXED"      # invalid -> fallback
    assert out["life_area"] == "MONEY"   # valid -> kept


def test_classifier_handles_code_fence():
    out = pipeline.parse_classifier('```json\n{"intent":"REFLECTIVE"}\n```')
    assert out["intent"] == "REFLECTIVE"


# --- depth targets ----------------------------------------------------------

def test_depth_targets():
    assert pipeline.DEPTH_TARGETS["CONCISE"] == (100, 160)
    assert pipeline.DEPTH_TARGETS["STANDARD"] == (220, 420)
    assert pipeline.DEPTH_TARGETS["DEEP"] == (500, 900)


def test_word_count():
    assert pipeline.word_count("one two three") == 3
    assert pipeline.word_count("") == 0


# --- topic-aware chart summary ---------------------------------------------

SAMPLE_CHART = {
    "vedic_horoscope": {
        "astro_details": {"ascendant": "Pisces", "lagna_lord": "Jupiter",
                          "sun_sign": "Sagittarius", "moon_sign": "Aquarius",
                          "nakshatra": "Dhanishta"},
        "planets_position": {
            "Sun": {"sign": "Sagittarius", "house": 10},
            "Venus": {"sign": "Capricorn", "house": 11},
            "Jupiter": {"sign": "Gemini", "house": 4, "retrograde": True},
            "Mercury": {"sign": "Capricorn", "house": 11},
        },
        "current_vimshottari_dasha": {"dasha": "Saturn", "bhukti": "Saturn",
                                     "paryantardasha": "Rahu"},
    },
    "charts": {"D1": {}, "D9": {}},
}


def test_topic_summary_marriage_is_grounded():
    text, facts = pipeline.build_topic_chart_summary(SAMPLE_CHART, "MARRIAGE", ["D9"])
    assert "Ascendant: Pisces" in text
    assert "Current dasha" in text
    assert "7th house" in text
    assert facts["relevant_dasha_or_timing"]
    assert "D9" in facts["relevant_divisional_chart"]


def test_topic_summary_missing_divisional_does_not_crash():
    chart = {"vedic_horoscope": SAMPLE_CHART["vedic_horoscope"], "charts": {"D1": {}}}
    text, facts = pipeline.build_topic_chart_summary(chart, "MARRIAGE", ["D9"])
    assert text                                   # still produced
    assert "divisional_D9" in facts["missing_data"]


def test_topic_summary_no_chart():
    text, facts = pipeline.build_topic_chart_summary(None, "MARRIAGE", [])
    assert text == ""
    assert facts["main_chart_factors_used"] == []


# --- seed content / defaults ------------------------------------------------

def test_seed_defaults_style_depth_and_critic():
    opts = {o["key"]: o for o in seed.CONFIG_OPTIONS}
    assert opts["default_answer_style"]["value"] == "BLENDED"
    assert opts["default_answer_depth"]["value"] == "STANDARD"
    assert opts["enable_critic_pass"]["value"] == "true"


def test_seed_has_per_step_model_keys():
    keys = {o["key"] for o in seed.CONFIG_OPTIONS}
    for k in ["safety_model", "classifier_model", "evidence_model", "draft_model",
              "critic_model", "formatter_model", "shortener_model"]:
        assert k in keys


def test_seed_has_evidence_planner_module():
    keys = {m["key"] for m in seed.PROMPT_MODULES}
    assert "03a_chart_evidence_planner" in keys


def test_writing_style_has_all_modes():
    mods = {m["key"]: m["content"] for m in seed.PROMPT_MODULES}
    style = mods["06_writing_style"]
    assert "PLAIN" in style and "BLENDED" in style and "TECHNICAL" in style
    assert "Technical basis" in style


def test_shortener_is_conditional_not_fixed_ratio():
    mods = {m["key"]: m["content"] for m in seed.PROMPT_MODULES}
    sh = mods["10_answer_shortener"]
    assert "60-70%" not in sh
    assert "too long for the selected answer_depth" in sh


def test_classifier_module_asks_for_json_only():
    mods = {m["key"]: m["content"] for m in seed.PROMPT_MODULES}
    assert "Return ONLY valid JSON" in mods["02_query_classifier"]
