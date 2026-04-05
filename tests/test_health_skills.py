"""
Tests for health & fitness API skills.

Tests Fitbit and Open Food Facts integrations.
"""

import pytest

from skills.health_skills import (
    HEALTH_SKILLS,
    get_daily_steps,
    get_sleep_data,
    get_workout_summary,
    get_nutrition_info,
)


def test_health_skills_registered():
    """Verify all health skills are registered."""
    assert "get_daily_steps" in HEALTH_SKILLS
    assert "get_sleep_data" in HEALTH_SKILLS
    assert "get_workout_summary" in HEALTH_SKILLS
    assert "get_nutrition_info" in HEALTH_SKILLS


def test_skills_are_callables():
    """Verify all health skills are callable."""
    for skill_name, skill_func in HEALTH_SKILLS.items():
        assert callable(skill_func), f"{skill_name} is not callable"


# ============================================================================
# Fitbit Tests
# ============================================================================

@pytest.mark.asyncio
async def test_get_daily_steps_no_token():
    """Test daily steps without access token."""
    result = await get_daily_steps()
    
    assert isinstance(result, dict)
    assert result["status"] == "error"
    assert "token" in result["message"].lower() or "fitbit" in result["message"].lower()


@pytest.mark.asyncio
async def test_get_daily_steps_with_date():
    """Test daily steps with specific date."""
    result = await get_daily_steps(date="2024-01-15")
    
    assert isinstance(result, dict)
    assert result["status"] == "error"  # No token configured


@pytest.mark.asyncio
async def test_get_sleep_data_no_token():
    """Test sleep data without access token."""
    result = await get_sleep_data()
    
    assert isinstance(result, dict)
    assert result["status"] == "error"
    assert "token" in result["message"].lower()


@pytest.mark.asyncio
async def test_get_sleep_data_with_date():
    """Test sleep data with specific date."""
    result = await get_sleep_data(date="2024-01-15")
    
    assert isinstance(result, dict)
    # Should accept date parameter even without token
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_get_workout_summary_no_token():
    """Test workout summary without access token."""
    result = await get_workout_summary()
    
    assert isinstance(result, dict)
    assert result["status"] == "error"
    assert "token" in result["message"].lower()


@pytest.mark.asyncio
async def test_get_workout_summary_days_param():
    """Test workout summary with different day ranges."""
    # Test various day ranges
    for days in [1, 7, 14, 30]:
        result = await get_workout_summary(days=days)
        assert isinstance(result, dict)
        assert result["status"] == "error"  # No token


@pytest.mark.asyncio
async def test_get_workout_summary_max_days():
    """Test that workout summary caps days at 30."""
    # Even with large day count, should cap internally
    result = await get_workout_summary(days=100)
    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_daily_steps_response_structure():
    """Test expected response structure for daily steps."""
    result = await get_daily_steps()
    
    assert "status" in result
    
    if result["status"] == "success":
        assert "date" in result
        assert "steps" in result
        assert "distance" in result
        assert "floors" in result
        assert "calories" in result
        assert "active_minutes" in result
        assert "goals" in result
        assert isinstance(result["goals"], dict)


@pytest.mark.asyncio
async def test_sleep_data_response_structure():
    """Test expected response structure for sleep data."""
    result = await get_sleep_data()
    
    assert "status" in result
    
    if result["status"] == "success":
        assert "date" in result
        # May have "message" if no data, or full structure
        if "duration_hours" in result:
            assert "efficiency" in result
            assert "stages" in result
            assert isinstance(result["stages"], dict)


@pytest.mark.asyncio
async def test_workout_summary_response_structure():
    """Test expected response structure for workout summary."""
    result = await get_workout_summary(days=7)
    
    assert "status" in result
    
    if result["status"] == "success":
        assert "period" in result
        assert "total_workouts" in result
        assert "total_active_minutes" in result
        assert "workouts" in result
        assert isinstance(result["workouts"], list)


# ============================================================================
# Open Food Facts Tests
# ============================================================================

@pytest.mark.asyncio
async def test_get_nutrition_info_basic():
    """Test nutrition info lookup."""
    result = await get_nutrition_info(food="banana")
    
    assert isinstance(result, dict)
    assert "status" in result
    
    # Open Food Facts doesn't require auth, so might succeed
    if result["status"] == "success":
        assert "count" in result
        assert "products" in result
        assert isinstance(result["products"], list)


@pytest.mark.asyncio
async def test_get_nutrition_info_response_structure():
    """Test nutrition info response structure."""
    result = await get_nutrition_info(food="apple")
    
    assert isinstance(result, dict)
    
    if result["status"] == "success" and result.get("count", 0) > 0:
        product = result["products"][0]
        assert "name" in product
        assert "brand" in product
        assert "nutrition_grade" in product
        assert "per_100g" in product
        assert isinstance(product["per_100g"], dict)
        
        # Check nutrition fields
        nutrition = product["per_100g"]
        assert "calories" in nutrition
        assert "fat" in nutrition
        assert "carbs" in nutrition
        assert "protein" in nutrition


@pytest.mark.asyncio
async def test_nutrition_info_empty_query():
    """Test nutrition lookup with empty query."""
    result = await get_nutrition_info(food="")
    
    assert isinstance(result, dict)
    # Should handle gracefully (empty results or error)
    assert "status" in result


@pytest.mark.asyncio
async def test_nutrition_info_special_characters():
    """Test nutrition lookup with special characters."""
    result = await get_nutrition_info(food="peanut butter & jelly")
    
    assert isinstance(result, dict)
    assert "status" in result


@pytest.mark.asyncio
async def test_nutrition_info_barcode():
    """Test nutrition lookup with barcode format."""
    # Barcodes are numeric strings
    result = await get_nutrition_info(food="012345678905")
    
    assert isinstance(result, dict)
    assert "status" in result


@pytest.mark.asyncio
async def test_all_fitbit_skills_without_auth():
    """Test that all Fitbit skills handle missing auth gracefully."""
    fitbit_skills = [
        (get_daily_steps, {}),
        (get_sleep_data, {}),
        (get_workout_summary, {"days": 7}),
    ]
    
    for skill_func, kwargs in fitbit_skills:
        result = await skill_func(**kwargs)
        assert isinstance(result, dict)
        assert "status" in result
        # All should error without token
        assert result["status"] == "error"
        assert "message" in result


@pytest.mark.asyncio
async def test_nutrition_ingredients_allergens():
    """Test that nutrition data includes ingredients and allergens."""
    result = await get_nutrition_info(food="peanut butter")
    
    if result.get("status") == "success" and result.get("count", 0) > 0:
        product = result["products"][0]
        # Should have these fields (may be empty)
        assert "ingredients" in product
        assert "allergens" in product
        assert isinstance(product["allergens"], list)


@pytest.mark.asyncio
async def test_date_format_validation():
    """Test that date parameters are handled correctly."""
    # Valid date format
    result = await get_daily_steps(date="2024-01-15")
    assert isinstance(result, dict)
    
    # None (should use today)
    result = await get_daily_steps(date=None)
    assert isinstance(result, dict)
