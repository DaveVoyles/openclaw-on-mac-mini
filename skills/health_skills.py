"""
Health & Fitness API integration.

Integrates with Fitbit API and Open Food Facts for health tracking,
nutrition info, and fitness data.

APIs:
  - Fitbit: Activity, sleep, nutrition tracking (OAuth2)
  - Open Food Facts: Food database lookup (no auth required)

Free tiers available for both.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from config import cfg
from http_session import SessionManager

log = logging.getLogger("openclaw.health_skills")
_sessions = SessionManager(timeout=30, name="health_skills")

FITBIT_BASE_URL = "https://api.fitbit.com/1/user/-"
OPENFOODFACTS_BASE_URL = "https://world.openfoodfacts.org/api/v2"


# ============================================================================
# Fitbit API Skills (OAuth2 required)
# ============================================================================

async def get_daily_steps(date: str | None = None) -> dict[str, Any]:
    """
    Get daily step count from Fitbit.

    Args:
        date: Date in YYYY-MM-DD format (default: today)

    Returns:
        {
            "status": "success",
            "date": "2024-01-15",
            "steps": 8432,
            "distance": 5.8,  # km
            "floors": 12,
            "calories": 2150,
            "active_minutes": 45,
            "goals": {
                "steps": 10000,
                "distance": 8.0,
                "floors": 10
            }
        }

    Requires: FITBIT_ACCESS_TOKEN
    """
    if not cfg.fitbit_access_token:
        return {
            "status": "error",
            "message": "Fitbit access token not configured. Run OAuth2 flow first.",
        }

    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    try:
        headers = {
            "Authorization": f"Bearer {cfg.fitbit_access_token}",
        }

        async with _sessions.get() as session:
            url = f"{FITBIT_BASE_URL}/activities/date/{date}.json"
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    summary = data.get("summary", {})
                    goals = data.get("goals", {})

                    return {
                        "status": "success",
                        "date": date,
                        "steps": summary.get("steps", 0),
                        "distance": summary.get("distances", [{}])[0].get("distance", 0),
                        "floors": summary.get("floors", 0),
                        "calories": summary.get("caloriesOut", 0),
                        "active_minutes": summary.get("fairlyActiveMinutes", 0) +
                                        summary.get("veryActiveMinutes", 0),
                        "goals": {
                            "steps": goals.get("steps", 0),
                            "distance": goals.get("distance", 0),
                            "floors": goals.get("floors", 0),
                        }
                    }
                elif resp.status == 401:
                    return {
                        "status": "error",
                        "message": "Unauthorized. Access token may be expired. Re-authenticate.",
                    }
                else:
                    error_text = await resp.text()
                    return {
                        "status": "error",
                        "message": f"Fitbit API error: {resp.status}",
                        "details": error_text,
                    }

    except Exception as e:
        log.error("Error fetching daily steps: %s", e)
        return {"status": "error", "message": str(e)}


async def get_sleep_data(date: str | None = None) -> dict[str, Any]:
    """
    Get sleep analysis from Fitbit.

    Args:
        date: Date in YYYY-MM-DD format (default: today)

    Returns:
        {
            "status": "success",
            "date": "2024-01-15",
            "duration_hours": 7.5,
            "efficiency": 92,
            "stages": {
                "deep": 1.8,
                "light": 4.2,
                "rem": 1.5,
                "wake": 0.5
            },
            "start_time": "23:15",
            "end_time": "06:45",
            "quality_score": 85
        }

    Requires: FITBIT_ACCESS_TOKEN
    """
    if not cfg.fitbit_access_token:
        return {
            "status": "error",
            "message": "Fitbit access token not configured. Run OAuth2 flow first.",
        }

    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    try:
        headers = {
            "Authorization": f"Bearer {cfg.fitbit_access_token}",
        }

        async with _sessions.get() as session:
            url = f"{FITBIT_BASE_URL}/sleep/date/{date}.json"
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    sleep = data.get("sleep", [])

                    if not sleep:
                        return {
                            "status": "success",
                            "date": date,
                            "message": "No sleep data recorded for this date",
                        }

                    main_sleep = sleep[0]  # Primary sleep session
                    duration_ms = main_sleep.get("duration", 0)
                    duration_hours = duration_ms / (1000 * 60 * 60)

                    stages = main_sleep.get("levels", {}).get("summary", {})

                    return {
                        "status": "success",
                        "date": date,
                        "duration_hours": round(duration_hours, 2),
                        "efficiency": main_sleep.get("efficiency", 0),
                        "stages": {
                            "deep": round(stages.get("deep", {}).get("minutes", 0) / 60, 2),
                            "light": round(stages.get("light", {}).get("minutes", 0) / 60, 2),
                            "rem": round(stages.get("rem", {}).get("minutes", 0) / 60, 2),
                            "wake": round(stages.get("wake", {}).get("minutes", 0) / 60, 2),
                        },
                        "start_time": main_sleep.get("startTime", ""),
                        "end_time": main_sleep.get("endTime", ""),
                        "quality_score": main_sleep.get("efficiency", 0),
                    }
                elif resp.status == 401:
                    return {
                        "status": "error",
                        "message": "Unauthorized. Access token may be expired. Re-authenticate.",
                    }
                else:
                    error_text = await resp.text()
                    return {
                        "status": "error",
                        "message": f"Fitbit API error: {resp.status}",
                        "details": error_text,
                    }

    except Exception as e:
        log.error("Error fetching sleep data: %s", e)
        return {"status": "error", "message": str(e)}


async def get_workout_summary(days: int = 7) -> dict[str, Any]:
    """
    Get workout summary for the past N days from Fitbit.

    Args:
        days: Number of days to include (1-30, default: 7)

    Returns:
        {
            "status": "success",
            "period": "7 days",
            "total_workouts": 5,
            "total_active_minutes": 245,
            "avg_steps": 8543,
            "avg_calories": 2234,
            "workouts": [
                {
                    "date": "2024-01-15",
                    "type": "Run",
                    "duration_minutes": 35,
                    "calories": 324,
                    "distance": 5.2
                },
                ...
            ]
        }

    Requires: FITBIT_ACCESS_TOKEN
    """
    if not cfg.fitbit_access_token:
        return {
            "status": "error",
            "message": "Fitbit access token not configured. Run OAuth2 flow first.",
        }

    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=min(days, 30))

        headers = {
            "Authorization": f"Bearer {cfg.fitbit_access_token}",
        }

        # Get activities list
        async with _sessions.get() as session:
            url = f"{FITBIT_BASE_URL}/activities/list.json"
            params = {
                "afterDate": start_date.strftime("%Y-%m-%d"),
                "sort": "desc",
                "limit": 100,
                "offset": 0,
            }

            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    activities = data.get("activities", [])

                    workouts = []
                    total_active_minutes = 0

                    for activity in activities:
                        duration = activity.get("duration", 0) / (1000 * 60)  # ms to minutes
                        total_active_minutes += duration

                        workouts.append({
                            "date": activity.get("startDate", "").split("T")[0],
                            "type": activity.get("activityName", "Unknown"),
                            "duration_minutes": int(duration),
                            "calories": activity.get("calories", 0),
                            "distance": activity.get("distance", 0),
                        })

                    return {
                        "status": "success",
                        "period": f"{days} days",
                        "total_workouts": len(workouts),
                        "total_active_minutes": int(total_active_minutes),
                        "workouts": workouts,
                    }
                elif resp.status == 401:
                    return {
                        "status": "error",
                        "message": "Unauthorized. Access token may be expired. Re-authenticate.",
                    }
                else:
                    error_text = await resp.text()
                    return {
                        "status": "error",
                        "message": f"Fitbit API error: {resp.status}",
                        "details": error_text,
                    }

    except Exception as e:
        log.error("Error fetching workout summary: %s", e)
        return {"status": "error", "message": str(e)}


# ============================================================================
# Open Food Facts API Skills (No auth required)
# ============================================================================

async def get_nutrition_info(food: str) -> dict[str, Any]:
    """
    Get nutrition information for a food item from Open Food Facts.

    Args:
        food: Food name or barcode

    Returns:
        {
            "status": "success",
            "count": 3,
            "products": [
                {
                    "name": "Organic Peanut Butter",
                    "brand": "Trader Joe's",
                    "nutrition_grade": "c",
                    "per_100g": {
                        "calories": 588,
                        "fat": 50.0,
                        "carbs": 20.0,
                        "protein": 25.0,
                        "sugar": 6.0,
                        "fiber": 6.0,
                        "sodium": 0.4
                    },
                    "ingredients": "peanuts, salt",
                    "allergens": ["peanuts"]
                },
                ...
            ]
        }

    Free tier: Unlimited (no key required)
    """
    try:
        params = {
            "search_terms": food,
            "page_size": 5,
            "json": 1,
        }

        headers = {
            "User-Agent": cfg.openfoodfacts_user_agent,
        }

        async with _sessions.get() as session:
            url = f"{OPENFOODFACTS_BASE_URL}/search"
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    products = data.get("products", [])

                    results = []
                    for product in products:
                        nutriments = product.get("nutriments", {})

                        results.append({
                            "name": product.get("product_name", "Unknown"),
                            "brand": product.get("brands", "Unknown"),
                            "nutrition_grade": product.get("nutrition_grade_fr", "unknown"),
                            "per_100g": {
                                "calories": nutriments.get("energy-kcal_100g", 0),
                                "fat": nutriments.get("fat_100g", 0),
                                "carbs": nutriments.get("carbohydrates_100g", 0),
                                "protein": nutriments.get("proteins_100g", 0),
                                "sugar": nutriments.get("sugars_100g", 0),
                                "fiber": nutriments.get("fiber_100g", 0),
                                "sodium": nutriments.get("sodium_100g", 0),
                            },
                            "ingredients": product.get("ingredients_text", ""),
                            "allergens": product.get("allergens_tags", []),
                        })

                    return {
                        "status": "success",
                        "count": len(results),
                        "products": results,
                    }
                else:
                    error_text = await resp.text()
                    return {
                        "status": "error",
                        "message": f"Open Food Facts API error: {resp.status}",
                        "details": error_text,
                    }

    except Exception as e:
        log.error("Error fetching nutrition info: %s", e)
        return {"status": "error", "message": str(e)}


# Skill metadata for registration
HEALTH_SKILLS = {
    "get_daily_steps": get_daily_steps,
    "get_sleep_data": get_sleep_data,
    "get_workout_summary": get_workout_summary,
    "get_nutrition_info": get_nutrition_info,
}
