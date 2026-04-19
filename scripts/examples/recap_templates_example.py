#!/usr/bin/env python3
"""
Example usage of recap templates.

Run with: python3 examples/recap_templates_example.py
"""

import asyncio


async def main():
    """Demonstrate recap template usage."""

    # Import the template functions
    from skills.recap_templates import (
        apply_template,
        get_available_templates,
    )

    print("=" * 70)
    print("RECAP TEMPLATES DEMO")
    print("=" * 70)

    # 1. List available templates
    print("\n1. Available Templates:")
    print("-" * 70)
    templates = get_available_templates()
    for template_name in templates["templates"]:
        details = templates["details"][template_name]
        print(f"\n   📋 {template_name.upper()}")
        print(f"      Name: {details['name']}")
        print(f"      Format: {details['format']}")
        print(f"      Sections: {', '.join(details['sections'][:3])}...")

    # 2. Apply a template (without generating - just show config)
    print("\n\n2. Template Configuration:")
    print("-" * 70)
    config = apply_template("entertainment", "7d")
    print(f"\n   Template: {config['template']}")
    print(f"   Topics: {config['query_params']['topics']}")
    print(f"   Stocks: {config['query_params']['stocks']}")
    print(f"   Date Range: {config['query_params']['date_from']} to {config['query_params']['date_to']}")

    # 3. Show different date range formats
    print("\n\n3. Date Range Formats:")
    print("-" * 70)
    for date_range in ["7d", "2w", "1m", "14"]:
        config = apply_template("tech", date_range)
        print(f"   {date_range:6} -> {config['query_params']['date_from']} to {config['query_params']['date_to']}")

    # 4. Example: Generate a recap (note: requires API keys)
    print("\n\n4. Generate Recap Example:")
    print("-" * 70)
    print("   To generate a recap:")
    print("   ")
    print("   recap = await generate_recap_from_template('entertainment', '7d')")
    print("   ")
    print("   Returns:")
    print("   {")
    print("       'status': 'ok',")
    print("       'template': 'entertainment',")
    print("       'recap': {")
    print("           'title': 'Entertainment Industry Recap',")
    print("           'period': '2024-01-01 to 2024-01-08',")
    print("           'sections': {...},")
    print("           'summary': '...',")
    print("           'generated_at': '2024-01-08T10:30:00Z'")
    print("       }")
    print("   }")

    # 5. All templates overview
    print("\n\n5. Template Overview:")
    print("-" * 70)

    overview = {
        "entertainment": {
            "emoji": "��",
            "data": "Box office, streaming, studio stocks (DIS, NFLX, WBD, PARA)",
        },
        "sports": {
            "emoji": "🏀",
            "data": "NBA scores, standings, upcoming games, sports news",
        },
        "tech": {
            "emoji": "💻",
            "data": "Tech headlines, FAANG+ stocks, product launches, funding",
        },
        "finance": {
            "emoji": "💰",
            "data": "Market indices (SPY, QQQ, DIA), top movers, sector sentiment",
        },
        "everything": {
            "emoji": "🌍",
            "data": "Condensed summary of all categories above",
        },
    }

    for template_name, info in overview.items():
        print(f"\n   {info['emoji']} {template_name.upper()}")
        print(f"      {info['data']}")

    print("\n" + "=" * 70)
    print("✅ Templates ready to use!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
