#!/usr/bin/env python3
"""
Verification script for OpenWeatherMap API integration.

Tests all three weather skills with real API calls.
Requires OPENWEATHER_API_KEY to be set in .env file.
"""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import cfg
from skills.weather_skills import get_air_quality, get_current_weather, get_forecast


async def main():
    """Run verification tests."""
    print("=" * 70)
    print("OpenWeatherMap API Verification")
    print("=" * 70)

    # Check API key
    if not cfg.openweather_api_key:
        print("\n❌ OPENWEATHER_API_KEY not configured in .env")
        print("   Sign up at: https://openweathermap.org/api")
        return 1

    print(f"\n✅ API Key configured: {cfg.openweather_api_key[:8]}...")

    # Test 1: Current weather
    print("\n" + "-" * 70)
    print("Test 1: Current Weather (San Francisco)")
    print("-" * 70)
    result = await get_current_weather("San Francisco", units="imperial")
    if result["status"] == "ok":
        print("✅ Success!")
        print(f"   Location: {result['location']}, {result['country']}")
        print(f"   Temperature: {result['temperature']}°F")
        print(f"   Feels Like: {result['feels_like']}°F")
        print(f"   Conditions: {result['description']}")
        print(f"   Humidity: {result['humidity']}%")
        print(f"   Wind: {result['wind_speed']} mph")
    else:
        print(f"❌ Failed: {result['message']}")
        return 1

    # Test 2: Weather with coordinates
    print("\n" + "-" * 70)
    print("Test 2: Current Weather (Seattle via coordinates)")
    print("-" * 70)
    result = await get_current_weather("47.6,-122.3", units="metric")
    if result["status"] == "ok":
        print("✅ Success!")
        print(f"   Location: {result['location']}, {result['country']}")
        print(f"   Temperature: {result['temperature']}°C")
        print(f"   Conditions: {result['description']}")
    else:
        print(f"❌ Failed: {result['message']}")
        return 1

    # Test 3: Forecast
    print("\n" + "-" * 70)
    print("Test 3: 3-Day Forecast (London)")
    print("-" * 70)
    result = await get_forecast("London, UK", days=3, units="metric")
    if result["status"] == "ok":
        print("✅ Success!")
        print(f"   Location: {result['location']}, {result['country']}")
        print(f"   Forecast points: {len(result['forecasts'])}")
        print("\n   First 3 forecasts:")
        for i, forecast in enumerate(result["forecasts"][:3], 1):
            print(f"   {i}. {forecast['datetime']}")
            print(f"      Temp: {forecast['temperature']}°C, {forecast['description']}")
            print(f"      Precip. probability: {int(forecast['pop'] * 100)}%")
    else:
        print(f"❌ Failed: {result['message']}")
        return 1

    # Test 4: Air quality
    print("\n" + "-" * 70)
    print("Test 4: Air Quality (Tokyo)")
    print("-" * 70)
    result = await get_air_quality("Tokyo")
    if result["status"] == "ok":
        print("✅ Success!")
        print(f"   Location: {result['location']}")
        print(f"   AQI: {result['aqi']} ({result['aqi_label']})")
        print(f"   PM2.5: {result['components']['pm2_5']} μg/m³")
        print(f"   PM10: {result['components']['pm10']} μg/m³")
        print(f"   O3: {result['components']['o3']} μg/m³")
    else:
        print(f"❌ Failed: {result['message']}")
        return 1

    # All tests passed
    print("\n" + "=" * 70)
    print("✅ All tests passed! OpenWeatherMap integration working correctly.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
