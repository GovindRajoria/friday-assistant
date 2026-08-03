# skills/web/weather.py
"""Current conditions and a short forecast, from Open-Meteo.

Open-Meteo needs no API key and no account, which is the reason it is here
rather than one of the usual weather services: this project's whole claim is
that it runs on local hardware without cloud credentials, and a skill that
demands a signup breaks that for everyone but the person who signed up.

Two calls: a geocoding lookup to turn "Delhi" into coordinates, then the
forecast. Both are documented JSON APIs rather than scraped pages — see
web_search's docstring for what happened the last time this project depended
on a page layout staying still.
"""
import requests
from core.config import SETTINGS

REQUEST_TIMEOUT_SECONDS = 12
FORECAST_DAYS = 3
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# WMO weather interpretation codes. Open-Meteo returns the number; the words
# are ours. Only the codes the API actually emits are listed — an unknown one
# degrades to "code N" rather than silently reading as clear skies.
WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    56: "light freezing drizzle", 57: "dense freezing drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snowfall", 73: "moderate snowfall", 75: "heavy snowfall",
    77: "snow grains",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "slight snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
}


def describe_code(code) -> str:
    """WMO code to plain English, without pretending an unknown code is fine."""
    try:
        return WEATHER_CODES.get(int(code), f"weather code {code}")
    except (TypeError, ValueError):
        return "unknown conditions"


def _get(url, params):
    response = requests.get(url, params=params, headers={"User-Agent": USER_AGENT},
                            timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def geocode(place: str) -> dict | None:
    """Resolve a place name to coordinates, or None if it is not a place."""
    payload = _get("https://geocoding-api.open-meteo.com/v1/search",
                   {"name": place, "count": 1, "language": "en", "format": "json"})
    results = payload.get("results") or []
    return results[0] if results else None


def format_report(place_label: str, current: dict, daily: dict) -> str:
    """Turn the two API payloads into one sentence plus a short outlook.

    Separate from the fetch so the formatting is testable against canned
    payloads — the shape of the response is exactly the thing that changes
    upstream without warning.
    """
    lines = [
        f"Weather for {place_label}: {describe_code(current.get('weather_code'))}, "
        f"{current.get('temperature_2m')}°C "
        f"(feels like {current.get('apparent_temperature')}°C), "
        f"humidity {current.get('relative_humidity_2m')}%, "
        f"wind {current.get('wind_speed_10m')} km/h."
    ]

    days = daily.get("time") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    codes = daily.get("weather_code") or []
    # zip stops at the shortest, so a payload missing one series produces a
    # shorter outlook rather than an IndexError.
    outlook = [
        f"{day}: {low}–{high}°C, {describe_code(code)}"
        for day, high, low, code in zip(days, highs, lows, codes)
    ]
    if outlook:
        lines.append("Next few days — " + "; ".join(outlook) + ".")
    return "\n".join(lines)


class WeatherSkill:
    def __init__(self):
        self.manifest = {
            "name": "weather",
            "description": (
                "Reports current weather and a three-day forecast for a place. Use this "
                "for any question about weather, temperature, rain or forecasts. "
                "Parameter: 'location' (a city or town name; omit it to use the "
                "operator's configured location). DO NOT use web_search for weather."
            ),
            "parameters": ["location"],
        }

    def execute(self, params=None):
        params = params or {}
        location = str(params.get("location") or "").strip()
        if not location:
            # Falls back to the operator profile so "what's the weather?" with
            # no city still works for the person who configured this install.
            location = str(SETTINGS["user"].get("location") or "").strip()
        if not location:
            return {"status": "error",
                    "message": "I need a place name — or set user.location in settings."}

        try:
            place = geocode(location)
            if place is None:
                return {"status": "error", "message": f"I could not find a place called '{location}'."}
            forecast = _get("https://api.open-meteo.com/v1/forecast", {
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                "timezone": "auto",
                "forecast_days": FORECAST_DAYS,
            })
        except requests.RequestException as error:
            return {"status": "error", "message": f"I could not reach the weather service: {error}"}
        except (KeyError, ValueError) as error:
            return {"status": "error", "message": f"The weather service returned something unexpected: {error}"}

        label = ", ".join(part for part in (place.get("name"), place.get("country")) if part)
        return {
            "status": "success",
            "message": format_report(label, forecast.get("current") or {}, forecast.get("daily") or {}),
            "data": {"latitude": place["latitude"], "longitude": place["longitude"]},
        }


def setup():
    return WeatherSkill()
