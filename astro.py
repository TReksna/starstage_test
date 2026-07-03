"""
Local Vedic astrology extraction using jyotishyamitra.
Replaces the old AstrologyAPI-based astro.py.
"""

import json
import os
import tempfile
from datetime import datetime
from typing import Dict, Any

import jyotishyamitra as jy

# jyotishyamitra spells "Sagittarius" as "Saggitarius" — normalize on read
_SIGN_NORMALIZE = {"Saggitarius": "Sagittarius"}

SIGN_NAMES = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces",
]
SIGN_NUM = {name: i + 1 for i, name in enumerate(SIGN_NAMES)}

# South Indian chart: sign -> (row, col) in 4x4 grid (inner 4 cells are chart center)
SOUTH_INDIAN_POS = {
    "Pisces":       (0, 0), "Aries":        (0, 1),
    "Taurus":       (0, 2), "Gemini":       (0, 3),
    "Aquarius":     (1, 0), "Cancer":       (1, 3),
    "Capricorn":    (2, 0), "Leo":          (2, 3),
    "Sagittarius":  (3, 0), "Scorpio":      (3, 1),
    "Libra":        (3, 2), "Virgo":        (3, 3),
}

SIGN_ABBREV = {
    "Aries": "Ar", "Taurus": "Ta", "Gemini": "Ge", "Cancer": "Ca",
    "Leo": "Le", "Virgo": "Vi", "Libra": "Li", "Scorpio": "Sc",
    "Sagittarius": "Sg", "Capricorn": "Cp", "Aquarius": "Aq", "Pisces": "Pi",
}

PLANET_ABBREV = {
    "Sun": "Su", "Moon": "Mo", "Mars": "Ma", "Mercury": "Me",
    "Jupiter": "Ju", "Venus": "Ve", "Saturn": "Sa", "Rahu": "Ra", "Ketu": "Ke",
}


def _norm_sign(sign: str) -> str:
    return _SIGN_NORMALIZE.get(sign, sign)


class AstrologyDataExtractor:
    """Extracts Vedic astrology data using jyotishyamitra (local, no API calls)."""

    def __init__(self, user_data: Dict[str, Any]):
        self.user_data = user_data

    def _run_jyotishyamitra(self) -> Dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmp:
            jy.clear_birthdata()
            jy.set_output(tmp, "astro_out")

            jy.input_birthdata(
                name=str(self.user_data.get("name", "")),
                gender=str(self.user_data.get("gender", "male")),
                place=str(self.user_data.get("birth_city", "")),
                longitude=str(self.user_data.get("longitude", 0)),
                lattitude=str(self.user_data.get("latitude", 0)),
                timezone=str(self.user_data.get("timezone", 0)),
                year=str(self.user_data.get("birth_year", 1990)),
                month=str(self.user_data.get("birth_month", 1)),
                day=str(self.user_data.get("birth_date", 1)),
                hour=str(self.user_data.get("birth_hour", 0)),
                min=str(self.user_data.get("birth_min", 0)),
                sec="0",
            )

            result = jy.validate_birthdata()
            if result != "SUCCESS":
                raise RuntimeError(f"jyotishyamitra birth-data validation failed: {result}")

            result_path = jy.generate_astrologicalData(jy.birthdata)
            if result_path in ("INPUT_ERROR", "OUTPUTPATH_ERROR"):
                raise RuntimeError(f"jyotishyamitra generation error: {result_path}")

            with open(result_path, encoding="utf-8") as f:
                raw = json.load(f)

        return raw

    @staticmethod
    def _build_manglik_dosha(d1_planets: Dict) -> Dict:
        manglik_houses = {1, 2, 4, 7, 8, 12}
        mars_house = None
        for planet, info in d1_planets.items():
            if planet == "Mars":
                mars_house = info.get("house-num")
                break
        is_manglik = bool(mars_house and mars_house in manglik_houses)
        return {
            "is_manglik": is_manglik,
            "mars_house": mars_house,
            "description": (
                f"Mars is in house {mars_house} (Manglik position)."
                if is_manglik
                else f"Mars is in house {mars_house} (not Manglik)."
            ),
        }

    @staticmethod
    def _build_vedic_horoscope(raw: Dict, user_details: Dict) -> Dict:
        d1 = raw.get("D1", {})
        asc = d1.get("ascendant", {})
        planets = d1.get("planets", {})

        astro_details = {
            "ascendant": _norm_sign(asc.get("sign", "")),
            "ascendant_deg": asc.get("pos", {}).get("dec_deg", 0),
            "moon_sign": _norm_sign(planets.get("Moon", {}).get("sign", "")),
            "sun_sign": _norm_sign(planets.get("Sun", {}).get("sign", "")),
            "nakshatra": user_details.get("nakshatra", ""),
            "tithi": user_details.get("tithi", ""),
            "yoga": user_details.get("yoga", ""),
            "karana": user_details.get("karana", ""),
            "vaara": user_details.get("vaara", ""),
            "maasa": user_details.get("maasa", ""),
            "rashi": user_details.get("rashi", ""),
            "lagna_lord": asc.get("lagna-lord", ""),
        }

        planets_position = {}
        for pname, pinfo in planets.items():
            planets_position[pname] = {
                "sign": _norm_sign(pinfo.get("sign", "")),
                "house": pinfo.get("house-num"),
                "nakshatra": pinfo.get("nakshatra", ""),
                "pada": pinfo.get("pada"),
                "degree": pinfo.get("pos", {}).get("dec_deg", 0),
                "retrograde": pinfo.get("retro", False),
                "dispositor": pinfo.get("dispositor", ""),
                "house_rel": pinfo.get("house-rel", ""),
            }

        current_dasha = (
            raw.get("Dashas", {}).get("Vimshottari", {}).get("current", {})
        )

        return {
            "astro_details": astro_details,
            "planets_position": planets_position,
            "manglik_dosha": AstrologyDataExtractor._build_manglik_dosha(planets),
            "current_vimshottari_dasha": current_dasha,
        }

    @staticmethod
    def _build_aspects(d1_planets: Dict) -> Dict:
        aspects = {}
        for pname, pinfo in d1_planets.items():
            asp = pinfo.get("Aspects", {})
            aspects[pname] = {
                "aspects_planets": asp.get("planets", []),
                "aspects_houses": asp.get("houses", []),
                "aspected_by": pinfo.get("Aspected-by", []),
                "conjuncts": pinfo.get("conjuncts", []),
            }
        return aspects

    def extract_all_data(self) -> Dict[str, Any]:
        print(f"[astro] Starting local extraction for {self.user_data['name']}...")
        raw = self._run_jyotishyamitra()
        user_details = raw.get("user_details", {})
        d1 = raw.get("D1", {})
        d1_planets = d1.get("planets", {})

        # Build charts section — D5 and D8 are not computed by jyotishyamitra 1.4.0
        charts = {}
        for cid in ["D1", "D2", "D3", "D4", "D7", "D9", "D10", "D12",
                    "D16", "D20", "D24", "D27", "D30", "D40", "D45", "D60"]:
            charts[cid] = raw.get(cid, {})
        charts["D5"] = {}
        charts["D8"] = {}

        comprehensive = {
            "user_info": self.user_data,
            "extraction_timestamp": datetime.now().isoformat(),
            "charts": charts,
            "vedic_horoscope": self._build_vedic_horoscope(raw, user_details),
            "dashas": raw.get("Dashas", {}).get("Vimshottari", {}),
            "ashtakvarga": raw.get("AshtakaVarga", {}),
            "additional": {
                "planets": d1_planets,
                "aspects": self._build_aspects(d1_planets),
                "special_points": raw.get("special_points", {}),
                "balas": raw.get("Balas", {}),
                "classifications": d1.get("classifications", {}),
            },
            "_raw": raw,
        }

        print(f"[astro] Extraction complete for {self.user_data['name']}")
        return comprehensive


def extract_and_store_user_data(user_data: Dict[str, Any]) -> str:
    """Extract astrology data locally and save to disk. Returns user_id."""
    extractor = AstrologyDataExtractor(user_data)
    comprehensive_data = extractor.extract_all_data()

    user_id = user_data["user_id"]
    user_dir = os.path.join("user_astro_data", user_id)
    os.makedirs(user_dir, exist_ok=True)

    backup_file = os.path.join(user_dir, f"{user_data['name']}_complete_astro_data.json")
    with open(backup_file, "w", encoding="utf-8") as f:
        json.dump(comprehensive_data, f, indent=2)
    print(f"[astro] Saved: {backup_file}")

    return user_id


# ---------------------------------------------------------------------------
# SVG chart rendering
# ---------------------------------------------------------------------------

def _planet_labels_for_chart(chart_data: dict, chart_id: str) -> Dict[str, list]:
    """
    Return {sign_name: [planet_abbrev, ...]} for the given chart data.
    Handles both D1 structure (planets dict) and Dx structure (houses list).
    """
    sign_planets: Dict[str, list] = {s: [] for s in SOUTH_INDIAN_POS}

    if chart_id == "D1" and isinstance(chart_data.get("planets"), dict):
        for pname, pinfo in chart_data["planets"].items():
            sign = _norm_sign(pinfo.get("sign", ""))
            if sign in sign_planets:
                label = PLANET_ABBREV.get(pname, pname[:2])
                if pinfo.get("retro"):
                    label += "®"
                sign_planets[sign].append(label)
    elif isinstance(chart_data.get("houses"), list):
        for house in chart_data["houses"]:
            sign = _norm_sign(house.get("sign", ""))
            if sign in sign_planets:
                for pname in house.get("planets", []):
                    sign_planets[sign].append(PLANET_ABBREV.get(pname, pname[:2]))

    return sign_planets


def render_chart_svg_from_complete_data(complete_data: dict, chart_id: str) -> str:
    """
    Generate a South Indian style Vedic chart as an SVG string.
    chart_id: D1, D2, ... D60, MOON, SUN, chalit
    """
    raw = complete_data.get("_raw", {})
    charts = complete_data.get("charts", {})

    # Resolve lagna sign and planet map
    if chart_id == "MOON":
        moon_sign = (
            complete_data.get("vedic_horoscope", {})
            .get("astro_details", {})
            .get("moon_sign", "Aries")
        )
        chart_data = raw.get("D1", charts.get("D1", {}))
        lagna_sign = moon_sign
    elif chart_id == "SUN":
        sun_sign = (
            complete_data.get("vedic_horoscope", {})
            .get("astro_details", {})
            .get("sun_sign", "Aries")
        )
        chart_data = raw.get("D1", charts.get("D1", {}))
        lagna_sign = sun_sign
    elif chart_id == "chalit":
        chart_data = raw.get("D1", charts.get("D1", {}))
        lagna_sign = _norm_sign(
            chart_data.get("ascendant", {}).get("sign", "Aries")
        )
    else:
        chart_data = raw.get(chart_id) or charts.get(chart_id, {})
        # For divisional charts the lagna is house 1's sign
        if isinstance(chart_data.get("houses"), list) and chart_data["houses"]:
            lagna_sign = _norm_sign(chart_data["houses"][0].get("sign", "Aries"))
        else:
            lagna_sign = _norm_sign(
                chart_data.get("ascendant", {}).get("sign", "Aries")
            )

    sign_planets = _planet_labels_for_chart(chart_data, chart_id)
    return _render_south_indian_svg(sign_planets, lagna_sign, chart_id)


def _render_south_indian_svg(
    sign_planets: Dict[str, list],
    lagna_sign: str,
    chart_id: str,
    size: int = 400,
) -> str:
    cell = size // 4
    parts = [
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
        f'xmlns="http://www.w3.org/2000/svg">',
        f'<rect width="{size}" height="{size}" fill="#ffffff"/>',
    ]

    for sign, (row, col) in SOUTH_INDIAN_POS.items():
        x, y = col * cell, row * cell
        is_lagna = sign == lagna_sign
        fill = "#FFF9C4" if is_lagna else "#FAFAFA"
        parts.append(
            f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" '
            f'fill="{fill}" stroke="#555" stroke-width="1"/>'
        )
        num = SIGN_NUM.get(sign, 0)
        abbr = SIGN_ABBREV.get(sign, sign[:2])
        parts.append(
            f'<text x="{x+4}" y="{y+13}" font-size="9" fill="#888" '
            f'font-family="sans-serif">{num} {abbr}</text>'
        )
        if is_lagna:
            parts.append(
                f'<text x="{x+4}" y="{y+25}" font-size="9" fill="#B71C1C" '
                f'font-weight="bold" font-family="sans-serif">Lag</text>'
            )
        for i, label in enumerate(sign_planets.get(sign, [])):
            parts.append(
                f'<text x="{x+4}" y="{y+38+i*14}" font-size="11" fill="#1A237E" '
                f'font-family="sans-serif">{label}</text>'
            )

    # Centre 2×2 box (cells 1,1 / 1,2 / 2,1 / 2,2)
    cx, cy = cell, cell
    parts.append(
        f'<rect x="{cx}" y="{cy}" width="{cell*2}" height="{cell*2}" '
        f'fill="#F0F0F0" stroke="#555" stroke-width="1"/>'
    )
    label = chart_id if chart_id not in ("MOON", "SUN") else chart_id
    parts.append(
        f'<text x="{cx+cell}" y="{cy+cell-8}" text-anchor="middle" '
        f'font-size="14" fill="#333" font-weight="bold" '
        f'font-family="sans-serif">{label}</text>'
    )
    parts.append(
        f'<text x="{cx+cell}" y="{cy+cell+12}" text-anchor="middle" '
        f'font-size="9" fill="#888" font-family="sans-serif">South Indian</text>'
    )

    parts.append("</svg>")
    return "\n".join(parts)
