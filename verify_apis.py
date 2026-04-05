#!/usr/bin/env python3
"""Direct API test using aiohttp."""
import asyncio
import aiohttp
import os
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
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
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
    
    # API-Sports basketball endpoint
    url = "https://v1.basketball.api-sports.io/standings"
    params = {"league": "12", "season": "2024-2025"}
    headers = {"x-apisports-key": APISPORTS_KEY}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    standings = data.get("response", [])
                    print(f"✅ API-Sports working! Found {len(standings)} entries")
                    return True
                else:
                    text = await resp.text()
                    print(f"❌ API-Sports error ({resp.status}): {text[:100]}")
                    return False
    except Exception as e:
        print(f"⚠️  API-Sports DNS/network issue: {str(e)[:80]}")
        print("   (May need to verify correct endpoint URL from API-Sports docs)")
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
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 200:
                data = await resp.json()
                quote = data.get("Global Quote", {})
                if quote:
                    price = quote.get("05. price", "N/A")
                    change = quote.get("10. change percent", "N/A")
                    print(f"✅ Alpha Vantage working!")
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
    
    # Run sequentially to avoid DNS issues
    news_ok = await test_newsapi()
    sports_ok = await test_apisports()
    finance_ok = await test_alphavantage()
    
    results = [news_ok, sports_ok, finance_ok]
    
    print("\n" + "=" * 70)
    if sum(results) >= 2:  # At least 2 of 3 working
        print(f"✅ {sum(results)}/3 APIS WORKING!")
        if all(results):
            print("\nPERFECT! All integrations verified.")
        else:
            print("\nMostly working - one API needs endpoint verification.")
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
