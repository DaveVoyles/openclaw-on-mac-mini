"""
OpenWeatherMap API integration for weather data.

Free tier: 1,000 calls/day
Provides current weather, forecasts, and air quality data.
"""

import logging
from typing import Any

from config import cfg
from http_session import SessionManager
from tool_health import circuit_breaker, tool_health

log = logging.getLogger("openclaw.weather_skills")

_sessions = SessionManager(timeout=15, name="weather_skills")

OPENWEATHER_BASE_URL = "https://api.openweathermap.org/data/2.5"


async def get_current_weather(
    location: str,
    units: str = "metric",
) -> dict[str, Any]:
    """
    Get current weather conditions for a location.

    Args:
        location: City name (e.g., "Seattle"), "City, Country" (e.g., "London, UK"),
                 or "lat,lon" coordinates (e.g., "47.6,-122.3")
        units: Temperature units - "metric" (Celsius), "imperial" (Fahrenheit), or "standard" (Kelvin)

    Returns:
        {
            "status": "ok",
            "location": "Seattle",
            "country": "US",
            "temperature": 15.3,
            "feels_like": 14.2,
            "conditions": "light rain",
            "description": "Cloudy with light rain showers",
            "humidity": 82,
            "pressure": 1013,
            "wind_speed": 5.2,
            "wind_direction": 240,
            "clouds": 75,
            "visibility": 10000,
            "units": "metric"
        }

    Free tier limit: 1,000 calls/day
    """
    if not cfg.openweather_api_key:
        return {
            "status": "error",
            "message": "OPENWEATHER_API_KEY not configured. Get a free key at https://openweathermap.org/api",
        }

    if circuit_breaker.is_open("openweather"):
        return {
            "status": "error",
            "message": "OpenWeather API temporarily unavailable (circuit breaker open)",
        }

    try:
        # Check if location is coordinates (lat,lon)
        if "," in location and all(part.replace(".", "").replace("-", "").isdigit() for part in location.split(",")):
            lat, lon = location.split(",")
            params = {
                "lat": lat.strip(),
                "lon": lon.strip(),
                "appid": cfg.openweather_api_key,
                "units": units,
            }
        else:
            params = {
                "q": location,
                "appid": cfg.openweather_api_key,
                "units": units,
            }

        session = await _sessions.get()
        async with session.get(
            f"{OPENWEATHER_BASE_URL}/weather",
            params=params,
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                circuit_breaker.record_success("openweather")
                tool_health.record("openweather", success=True)

                return {
                    "status": "ok",
                    "location": data["name"],
                    "country": data["sys"]["country"],
                    "temperature": data["main"]["temp"],
                    "feels_like": data["main"]["feels_like"],
                    "temp_min": data["main"]["temp_min"],
                    "temp_max": data["main"]["temp_max"],
                    "conditions": data["weather"][0]["main"],
                    "description": data["weather"][0]["description"],
                    "humidity": data["main"]["humidity"],
                    "pressure": data["main"]["pressure"],
                    "wind_speed": data["wind"]["speed"],
                    "wind_direction": data["wind"].get("deg", 0),
                    "clouds": data["clouds"]["all"],
                    "visibility": data.get("visibility", 0),
                    "sunrise": data["sys"]["sunrise"],
                    "sunset": data["sys"]["sunset"],
                    "units": units,
                }
            elif resp.status == 401:
                tool_health.record("openweather", success=False)
                return {
                    "status": "error",
                    "message": "Invalid API key. Check OPENWEATHER_API_KEY configuration.",
                }
            elif resp.status == 404:
                tool_health.record("openweather", success=False)
                return {
                    "status": "error",
                    "message": f"Location '{location}' not found. Try a different city name or use coordinates.",
                }
            elif resp.status == 429:
                circuit_breaker.record_failure("openweather")
                tool_health.record("openweather", success=False)
                return {
                    "status": "error",
                    "message": "Rate limit exceeded. Free tier: 1,000 calls/day.",
                }
            else:
                error_text = await resp.text()
                circuit_breaker.record_failure("openweather")
                tool_health.record("openweather", success=False)
                log.warning("OpenWeather API error %d: %s", resp.status, error_text)
                return {
                    "status": "error",
                    "message": f"API error {resp.status}: {error_text[:200]}",
                }

    except Exception as e:
        circuit_breaker.record_failure("openweather")
        tool_health.record("openweather", success=False)
        log.exception("Failed to fetch weather for %s", location)
        return {
            "status": "error",
            "message": f"Request failed: {str(e)}",
        }


async def get_forecast(
    location: str,
    days: int = 5,
    units: str = "metric",
) -> dict[str, Any]:
    """
    Get weather forecast for a location.

    Args:
        location: City name or "lat,lon" coordinates
        days: Number of forecast days (1-5 for free tier, 3-hour intervals)
        units: "metric", "imperial", or "standard"

    Returns:
        {
            "status": "ok",
            "location": "Seattle",
            "country": "US",
            "forecasts": [
                {
                    "datetime": "2024-04-05 12:00:00",
                    "temperature": 16.5,
                    "feels_like": 15.8,
                    "conditions": "Clouds",
                    "description": "overcast clouds",
                    "humidity": 75,
                    "wind_speed": 4.2,
                    "pop": 0.3  # probability of precipitation
                },
                ...
            ],
            "units": "metric"
        }

    Note: Free tier provides 5-day forecast in 3-hour steps (40 data points)
    """
    if not cfg.openweather_api_key:
        return {
            "status": "error",
            "message": "OPENWEATHER_API_KEY not configured",
        }

    if circuit_breaker.is_open("openweather"):
        return {
            "status": "error",
            "message": "OpenWeather API temporarily unavailable",
        }

    try:
        # Check if location is coordinates
        if "," in location and all(part.replace(".", "").replace("-", "").isdigit() for part in location.split(",")):
            lat, lon = location.split(",")
            params = {
                "lat": lat.strip(),
                "lon": lon.strip(),
                "appid": cfg.openweather_api_key,
                "units": units,
                "cnt": min(days * 8, 40),  # 8 datapoints per day (3-hour intervals)
            }
        else:
            params = {
                "q": location,
                "appid": cfg.openweather_api_key,
                "units": units,
                "cnt": min(days * 8, 40),
            }

        session = await _sessions.get()
        async with session.get(
            f"{OPENWEATHER_BASE_URL}/forecast",
            params=params,
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                circuit_breaker.record_success("openweather")
                tool_health.record("openweather", success=True)

                forecasts = []
                for item in data["list"]:
                    forecasts.append({
                        "datetime": item["dt_txt"],
                        "timestamp": item["dt"],
                        "temperature": item["main"]["temp"],
                        "feels_like": item["main"]["feels_like"],
                        "temp_min": item["main"]["temp_min"],
                        "temp_max": item["main"]["temp_max"],
                        "conditions": item["weather"][0]["main"],
                        "description": item["weather"][0]["description"],
                        "humidity": item["main"]["humidity"],
                        "pressure": item["main"]["pressure"],
                        "wind_speed": item["wind"]["speed"],
                        "wind_direction": item["wind"].get("deg", 0),
                        "clouds": item["clouds"]["all"],
                        "pop": item.get("pop", 0),  # probability of precipitation
                        "visibility": item.get("visibility", 10000),
                    })

                return {
                    "status": "ok",
                    "location": data["city"]["name"],
                    "country": data["city"]["country"],
                    "forecasts": forecasts,
                    "units": units,
                }
            elif resp.status == 429:
                circuit_breaker.record_failure("openweather")
                tool_health.record("openweather", success=False)
                return {
                    "status": "error",
                    "message": "Rate limit exceeded",
                }
            else:
                error_text = await resp.text()
                circuit_breaker.record_failure("openweather")
                tool_health.record("openweather", success=False)
                log.warning("OpenWeather forecast API error %d: %s", resp.status, error_text)
                return {
                    "status": "error",
                    "message": f"API error {resp.status}",
                }

    except Exception as e:
        circuit_breaker.record_failure("openweather")
        tool_health.record("openweather", success=False)
        log.exception("Failed to fetch forecast for %s", location)
        return {
            "status": "error",
            "message": f"Request failed: {str(e)}",
        }


async def get_air_quality(location: str) -> dict[str, Any]:
    """
    Get air quality index (AQI) and pollutant levels for a location.

    Args:
        location: City name or "lat,lon" coordinates

    Returns:
        {
            "status": "ok",
            "location": "Seattle",
            "aqi": 2,  # 1=Good, 2=Fair, 3=Moderate, 4=Poor, 5=Very Poor
            "aqi_label": "Fair",
            "components": {
                "co": 230.3,     # Carbon monoxide (μg/m³)
                "no": 0.15,      # Nitric oxide
                "no2": 15.2,     # Nitrogen dioxide
                "o3": 55.8,      # Ozone
                "so2": 2.1,      # Sulphur dioxide
                "pm2_5": 8.5,    # Fine particles
                "pm10": 12.3,    # Coarse particles
                "nh3": 1.2       # Ammonia
            }
        }

    Note: Requires coordinates. If city name is provided, it will be geocoded first.
    """
    if not cfg.openweather_api_key:
        return {
            "status": "error",
            "message": "OPENWEATHER_API_KEY not configured",
        }

    if circuit_breaker.is_open("openweather"):
        return {
            "status": "error",
            "message": "OpenWeather API temporarily unavailable",
        }

    try:
        # Get coordinates first if location is a city name
        if "," in location and all(part.replace(".", "").replace("-", "").isdigit() for part in location.split(",")):
            lat, lon = location.split(",")
            lat, lon = lat.strip(), lon.strip()
            city_name = f"{lat},{lon}"
        else:
            # Geocode the location
            session = await _sessions.get()
            geo_params = {
                "q": location,
                "limit": 1,
                "appid": cfg.openweather_api_key,
            }
            async with session.get(
                "http://api.openweathermap.org/geo/1.0/direct",
                params=geo_params,
            ) as geo_resp:
                if geo_resp.status != 200:
                    return {
                        "status": "error",
                        "message": f"Could not geocode location '{location}'",
                    }
                geo_data = await geo_resp.json()
                if not geo_data:
                    return {
                        "status": "error",
                        "message": f"Location '{location}' not found",
                    }
                lat = geo_data[0]["lat"]
                lon = geo_data[0]["lon"]
                city_name = geo_data[0].get("name", location)

        # Fetch air quality
        params = {
            "lat": lat,
            "lon": lon,
            "appid": cfg.openweather_api_key,
        }

        session = await _sessions.get()
        async with session.get(
            f"{OPENWEATHER_BASE_URL}/air_pollution",
            params=params,
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                circuit_breaker.record_success("openweather")
                tool_health.record("openweather", success=True)

                aqi = data["list"][0]["main"]["aqi"]
                aqi_labels = {1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very Poor"}

                return {
                    "status": "ok",
                    "location": city_name,
                    "lat": lat,
                    "lon": lon,
                    "aqi": aqi,
                    "aqi_label": aqi_labels.get(aqi, "Unknown"),
                    "components": data["list"][0]["components"],
                    "timestamp": data["list"][0]["dt"],
                }
            elif resp.status == 429:
                circuit_breaker.record_failure("openweather")
                tool_health.record("openweather", success=False)
                return {
                    "status": "error",
                    "message": "Rate limit exceeded",
                }
            else:
                error_text = await resp.text()
                circuit_breaker.record_failure("openweather")
                tool_health.record("openweather", success=False)
                log.warning("OpenWeather air quality API error %d: %s", resp.status, error_text)
                return {
                    "status": "error",
                    "message": f"API error {resp.status}",
                }

    except Exception as e:
        circuit_breaker.record_failure("openweather")
        tool_health.record("openweather", success=False)
        log.exception("Failed to fetch air quality for %s", location)
        return {
            "status": "error",
            "message": f"Request failed: {str(e)}",
        }


# Export skills for registration
WEATHER_SKILLS = [
    {
        "name": "get_current_weather",
        "function": get_current_weather,
        "description": "Get current weather conditions for any location worldwide. "
                      "Supports city names, country codes, and lat/lon coordinates. "
                      "Returns temperature, conditions, humidity, wind, and more.",
    },
    {
        "name": "get_forecast",
        "function": get_forecast,
        "description": "Get weather forecast up to 5 days (3-hour intervals). "
                      "Returns detailed predictions including temperature, conditions, "
                      "precipitation probability, wind, and humidity.",
    },
    {
        "name": "get_air_quality",
        "function": get_air_quality,
        "description": "Get air quality index (AQI) and pollutant levels for a location. "
                      "Returns AQI rating (Good/Fair/Moderate/Poor/Very Poor) and detailed "
                      "measurements for CO, NO2, O3, SO2, PM2.5, PM10, and NH3.",
    },
]
