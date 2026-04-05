#!/usr/bin/env python3
"""Direct API test using aiohttp."""
import asyncio
import os

import aiohttp
from dotenv import load_dotenv

load_dotenv()

NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
APISPORTS_KEY = os.getenv("APISPORTS_KEY")
ALPHAVANTAGE_KEY = os.getenv("ALPHAVANTAGE_KEY")


async def test_newsapi():
    """Test NewsAPI directly."""
    print("\n🗞️  Testing NewsAPI...")

    url = "https://newsapi.org/v2/everything"
    params = {
        "q": "artificial intelligence",
        "pageSize": 3,
        "apiKey": NEWSAPI_KEY,
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                print(f"✅ NewsAPI working! Found {data.get('totalResults', 0)} articles")
                for article in data.get("articles", [])[:2]:
                    print(f"   - {article.get('title', 'N/A')[:70]}")
                return True
            else:
                text = await resp.text()
                print(f"❌ NewsAPI error ({resp.status}): {text[:100]}")
                return False


async def test_apisports():
    """Test API-Sports directly."""
    print("\n🏀 Testing API-Sports...")

    url = "https://v3.api-sports.io/basketball/standings"
    params = {"league": "12", "season": "2024-2025"}
    headers = {"x-apisports-key": APISPORTS_KEY}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, headers=headers, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                standings = data.get("response", [])
                print(f"✅ API-Sports working! Found {len(standings)} entries")
                return True
            else:
                text = await resp.text()
                print(f"❌ API-Sports error ({resp.status}): {text[:100]}")
                return False


async def test_alphavantage():
    """Test Alpha Vantage directly."""
    print("\n💰 Testing Alpha Vantage...")

    url = "https://www.alphavantage.co/query"
    params = {
        "function": "GLOBAL_QUOTE",
        "symbol": "DIS",
        "apikey": ALPHAVANTAGE_KEY,
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                quote = data.get("Global Quote", {})
                if quote:
                    price = quote.get("05. price", "N/A")
                    change = quote.get("10. change percent", "N/A")
                    print("✅ Alpha Vantage working!")
                    print(f"   Disney (DIS): ${price} ({change})")
                    return True
                else:
                    print(f"❌ Alpha Vantage: {data}")
                    return False
            else:
                text = await resp.text()
                print(f"❌ Alpha Vantage error ({resp.status}): {text[:100]}")
                return False


async def main():
    print("=" * 70)
    print("🚀 Testing Free Tier API Integrations (Direct API Calls)")
    print("=" * 70)

    results = await asyncio.gather(
        test_newsapi(),
        test_apisports(),
        test_alphavantage(),
    )

    print("\n" + "=" * 70)
    if all(results):
        print("✅ ALL 3 APIS WORKING PERFECTLY!")
        print("\nThe bot can now answer:")
        print("  • /ask what's trending in AI news?")
        print("  • /ask NBA standings")
        print("  • /ask Disney stock price and sentiment")
        print("  • /ask box office news for major studios")
        print("\nRate limits:")
        print("  • NewsAPI: 100 requests/day")
        print("  • API-Sports: 100 requests/day")
        print("  • Alpha Vantage: 25 requests/day")
    else:
        print("⚠️  Some APIs failed - check error messages above")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
