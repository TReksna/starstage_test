"""Google Maps geocoding + timezone lookup for birth data."""
import datetime
import os

import httpx
from fastapi import HTTPException

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

# ---------------------------------------------------------------------------
# Geo / Timezone
# ---------------------------------------------------------------------------

async def get_geo_data_for_user(user: dict) -> dict:
    if not GOOGLE_MAPS_API_KEY:
        raise HTTPException(status_code=500, detail="Google Maps API key not configured")

    async with httpx.AsyncClient(timeout=10.0) as client:
        ac_resp = await client.get(
            "https://maps.googleapis.com/maps/api/place/autocomplete/json",
            params={"input": user["birth_city"], "key": GOOGLE_MAPS_API_KEY, "types": "(cities)", "language": "en"},
        )
        ac_json = ac_resp.json()
        if ac_json.get("status") != "OK" or not ac_json.get("predictions"):
            raise HTTPException(status_code=400, detail="Could not resolve birth_city to coordinates")

        place_id = ac_json["predictions"][0]["place_id"]

        det_resp = await client.get(
            "https://maps.googleapis.com/maps/api/place/details/json",
            params={"place_id": place_id, "fields": "geometry,address_components", "key": GOOGLE_MAPS_API_KEY},
        )
        det_json = det_resp.json()
        if det_json.get("status") != "OK":
            raise HTTPException(status_code=400, detail="Could not resolve birth_city to coordinates")

        loc = det_json["result"]["geometry"]["location"]
        user["latitude"] = float(loc["lat"])
        user["longitude"] = float(loc["lng"])
        user["country_code"] = next(
            (c["short_name"] for c in det_json["result"].get("address_components", []) if "country" in c["types"]),
            "Unknown",
        )

        api_date = f"{user['birth_year']}-{user['birth_month']:02d}-{user['birth_date']:02d}"
        ts = int(datetime.datetime.strptime(api_date, "%Y-%m-%d").timestamp())
        tz_resp = await client.get(
            "https://maps.googleapis.com/maps/api/timezone/json",
            params={"location": f"{user['latitude']},{user['longitude']}", "timestamp": ts, "key": GOOGLE_MAPS_API_KEY},
        )
        tz_json = tz_resp.json()
        if tz_json.get("status") != "OK":
            raise HTTPException(status_code=502, detail="Timezone lookup failed")
        user["timezone"] = (tz_json.get("rawOffset", 0) + tz_json.get("dstOffset", 0)) / 3600

    return user
