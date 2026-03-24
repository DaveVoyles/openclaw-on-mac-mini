
import asyncio
import logging
import os
import json
from skills.advanced_skills import search_web

async def test_search():
    print("Testing search_web with property query...")
    # Tavily requires an API key. If not present in environment, it will fallback to DDG.
    query = "homes for sale in Narberth PA $300k-$450k"

    try:
        # Testing with float num_results to verify the fix
        result = await search_web(query, num_results=3.0)
        print("\n--- Search Result ---")
        print(result[:1000] + "..." if len(result) > 1000 else result)
        print("--- End Result ---\n")

        if "❌" in result:
            print("Test failed: Error in result string.")
        elif "Narberth" in result or "PA" in result or "Pennsylvania" in result:
            print("Test passed: Relevant results found.")
        else:
            print("Test inconclusive: No clear location match in output.")

    except Exception as e:
        print(f"Test failed with exception: {e}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(test_search())
