#!/usr/bin/env python3
"""
Test script for the weekly recap generation engine.
Validates integration with NewsAPI, API-Sports, and Alpha Vantage.
"""

import asyncio
import sys
from pathlib import Path

# Add src directory to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root / "src"))
sys.path.insert(0, str(project_root))

# Load .env file
from dotenv import load_dotenv

load_dotenv(project_root / ".env")

from skills.reporting_skills import generate_weekly_recap


async def test_recap_basic():
    """Test basic weekly recap with all topics."""
    print("=" * 60)
    print("Test 1: Full Weekly Recap (All Topics)")
    print("=" * 60)

    result = await generate_weekly_recap(
        topics=["entertainment", "sports", "tech", "finance"],
        date_range="last_3_days",
    )

    print(result)
    print(f"\n📏 Report length: {len(result)} characters")

    # Validate structure
    assert "# 📊 Weekly Recap:" in result, "Missing main header"
    assert "## 🗞️ News Highlights" in result, "Missing news section"
    assert "## 📚 Data Sources" in result, "Missing sources section"

    print("\n✅ Basic test passed!\n")
    return result


async def test_recap_sports_only():
    """Test sports-only recap."""
    print("=" * 60)
    print("Test 2: Sports-Only Recap")
    print("=" * 60)

    result = await generate_weekly_recap(
        topics=["sports"],
        date_range="last_week",
    )

    print(result)
    print(f"\n📏 Report length: {len(result)} characters")

    assert "## 🏀 Sports Recap" in result, "Missing sports section"

    print("\n✅ Sports-only test passed!\n")
    return result


async def test_recap_custom_dates():
    """Test custom date range."""
    print("=" * 60)
    print("Test 3: Custom Date Range")
    print("=" * 60)

    result = await generate_weekly_recap(
        topics=["tech", "finance"],
        date_range="custom",
        from_date="2025-01-01",
        to_date="2025-01-15",
    )

    print(result)
    print(f"\n📏 Report length: {len(result)} characters")

    assert "2025-01-01 to 2025-01-15" in result, "Missing custom date range"

    print("\n✅ Custom date test passed!\n")
    return result


async def test_recap_error_handling():
    """Test error handling for custom dates."""
    print("=" * 60)
    print("Test 4: Error Handling (Missing from_date)")
    print("=" * 60)

    result = await generate_weekly_recap(
        topics=["tech"],
        date_range="custom",
        # Missing from_date - should error
    )

    print(result)

    assert "Error" in result, "Should return error message"

    print("\n✅ Error handling test passed!\n")
    return result


async def main():
    """Run all tests."""
    print("\n🚀 Starting Weekly Recap Engine Tests\n")

    tests = [
        test_recap_basic,
        test_recap_sports_only,
        test_recap_custom_dates,
        test_recap_error_handling,
    ]

    results = []
    failed = []

    for test in tests:
        try:
            result = await test()
            results.append(result)
        except AssertionError as e:
            print(f"❌ Test failed: {e}")
            failed.append(test.__name__)
        except Exception as e:
            print(f"❌ Test error: {e}")
            failed.append(test.__name__)

    print("\n" + "=" * 60)
    print("📊 Test Summary")
    print("=" * 60)
    print(f"Total tests: {len(tests)}")
    print(f"Passed: {len(tests) - len(failed)}")
    print(f"Failed: {len(failed)}")

    if failed:
        print(f"\nFailed tests: {', '.join(failed)}")
        sys.exit(1)
    else:
        print("\n🎉 All tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
