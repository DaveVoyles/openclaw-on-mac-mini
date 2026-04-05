"""
Sports data skills using API-Sports (api-sports.io)

Free tier: 100 requests/day across all sports endpoints
Covers: NBA, NFL, MLB, NHL, soccer, and 8 more sports
"""

from datetime import datetime
from typing import Any

from config import cfg
from src.http_session import SessionManager
from src.tool_health import ToolHealthMonitor

APISPORTS_BASE_URL = "https://v3.api-sports.io"


async def get_nba_scores(date: str | None = None, team_id: int | None = None) -> dict[str, Any]:
    """
    Get NBA game scores for a specific date.

    Args:
        date: Date in YYYY-MM-DD format (default: today)
        team_id: Optional specific team ID to filter

    Returns:
        {
            "results": 5,
            "games": [
                {
                    "id": 123456,
                    "date": "2024-01-15",
                    "teams": {
                        "home": {"name": "Lakers", "score": 108},
                        "away": {"name": "Warriors", "score": 112}
                    },
                    "status": "Finished",
                    "quarter": "4/4"
                },
                ...
            ]
        }

    Free tier: 100 requests/day (shared across all sports)
    """
    if not cfg.apisports_key:
        return {
            "status": "error",
            "message": "APISPORTS_KEY not configured",
            "games": [],
        }

    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    params = {"date": date, "league": "12", "season": "2024-2025"}
    if team_id:
        params["team"] = str(team_id)

    url = f"{APISPORTS_BASE_URL}/basketball/games"
    headers = {"x-apisports-key": cfg.apisports_key}

    async with SessionManager.get_session() as session:
        async with session.get(url, params=params, headers=headers, timeout=30) as resp:
            if resp.status == 429:
                ToolHealthMonitor.record_failure("apisports", "Rate limit exceeded")
                return {
                    "status": "error",
                    "message": "API-Sports rate limit exceeded. Free tier: 100 requests/day.",
                    "games": [],
                }

            if resp.status != 200:
                error_text = await resp.text()
                ToolHealthMonitor.record_failure("apisports", f"HTTP {resp.status}")
                return {
                    "status": "error",
                    "message": f"API-Sports error: {error_text}",
                    "games": [],
                }

            data = await resp.json()
            ToolHealthMonitor.record_success("apisports")

            # Simplify response
            games = []
            for game in data.get("response", []):
                games.append({
                    "id": game["id"],
                    "date": game["date"],
                    "teams": {
                        "home": {
                            "name": game["teams"]["home"]["name"],
                            "score": game["scores"]["home"]["total"],
                        },
                        "away": {
                            "name": game["teams"]["away"]["name"],
                            "score": game["scores"]["away"]["total"],
                        },
                    },
                    "status": game["status"]["long"],
                    "quarter": f"{game['status']['timer'] or 'N/A'}",
                })

            return {
                "status": "ok",
                "results": len(games),
                "games": games,
            }


async def get_nfl_scores(date: str | None = None, team_id: int | None = None) -> dict[str, Any]:
    """
    Get NFL game scores for a specific date.

    Args:
        date: Date in YYYY-MM-DD format (default: today)
        team_id: Optional specific team ID to filter

    Returns:
        Same format as get_nba_scores()

    Free tier: 100 requests/day (shared across all sports)
    """
    if not cfg.apisports_key:
        return {
            "status": "error",
            "message": "APISPORTS_KEY not configured",
            "games": [],
        }

    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    params = {"date": date, "league": "1", "season": "2024"}
    if team_id:
        params["team"] = str(team_id)

    url = f"{APISPORTS_BASE_URL}/american-football/games"
    headers = {"x-apisports-key": cfg.apisports_key}

    async with SessionManager.get_session() as session:
        async with session.get(url, params=params, headers=headers, timeout=30) as resp:
            if resp.status == 429:
                ToolHealthMonitor.record_failure("apisports", "Rate limit exceeded")
                return {
                    "status": "error",
                    "message": "API-Sports rate limit exceeded. Free tier: 100 requests/day.",
                    "games": [],
                }

            if resp.status != 200:
                error_text = await resp.text()
                ToolHealthMonitor.record_failure("apisports", f"HTTP {resp.status}")
                return {
                    "status": "error",
                    "message": f"API-Sports error: {error_text}",
                    "games": [],
                }

            data = await resp.json()
            ToolHealthMonitor.record_success("apisports")

            # Simplify response
            games = []
            for game in data.get("response", []):
                games.append({
                    "id": game["id"],
                    "date": game["date"],
                    "teams": {
                        "home": {
                            "name": game["teams"]["home"]["name"],
                            "score": game["scores"]["home"]["total"],
                        },
                        "away": {
                            "name": game["teams"]["away"]["name"],
                            "score": game["scores"]["away"]["total"],
                        },
                    },
                    "status": game["status"]["long"],
                    "quarter": game["status"]["short"],
                })

            return {
                "status": "ok",
                "results": len(games),
                "games": games,
            }


async def get_team_standings(sport: str = "nba", league_id: int | None = None, season: str | None = None) -> dict[str, Any]:
    """
    Get league standings for a sport.

    Args:
        sport: nba, nfl, nhl, mlb, soccer
        league_id: Specific league (default: NBA=12, NFL=1, NHL=57, MLB=1)
        season: Season year (default: current season)

    Returns:
        {
            "status": "ok",
            "standings": [
                {
                    "rank": 1,
                    "team": "Boston Celtics",
                    "wins": 35,
                    "losses": 12,
                    "win_pct": ".745",
                    "games_behind": "0"
                },
                ...
            ]
        }

    Free tier: 100 requests/day
    """
    if not cfg.apisports_key:
        return {
            "status": "error",
            "message": "APISPORTS_KEY not configured",
            "standings": [],
        }

    # Map sports to endpoints and default leagues
    sport_config = {
        "nba": {"endpoint": "basketball", "league": 12, "season": "2024-2025"},
        "nfl": {"endpoint": "american-football", "league": 1, "season": "2024"},
        "nhl": {"endpoint": "hockey", "league": 57, "season": "2024"},
        "mlb": {"endpoint": "baseball", "league": 1, "season": "2024"},
    }

    if sport.lower() not in sport_config:
        return {
            "status": "error",
            "message": f"Unsupported sport: {sport}. Choose: nba, nfl, nhl, mlb",
            "standings": [],
        }

    config = sport_config[sport.lower()]
    params = {
        "league": str(league_id or config["league"]),
        "season": season or config["season"],
    }

    url = f"{APISPORTS_BASE_URL}/{config['endpoint']}/standings"
    headers = {"x-apisports-key": cfg.apisports_key}

    async with SessionManager.get_session() as session:
        async with session.get(url, params=params, headers=headers, timeout=30) as resp:
            if resp.status == 429:
                ToolHealthMonitor.record_failure("apisports", "Rate limit exceeded")
                return {
                    "status": "error",
                    "message": "API-Sports rate limit exceeded. Free tier: 100 requests/day.",
                    "standings": [],
                }

            if resp.status != 200:
                error_text = await resp.text()
                ToolHealthMonitor.record_failure("apisports", f"HTTP {resp.status}")
                return {
                    "status": "error",
                    "message": f"API-Sports error: {error_text}",
                    "standings": [],
                }

            data = await resp.json()
            ToolHealthMonitor.record_success("apisports")

            # Response format varies by sport, normalize it
            standings = []
            for item in data.get("response", []):
                if isinstance(item, list):
                    # Some sports nest standings in arrays
                    for team_data in item:
                        standings.append(_extract_team_standing(team_data))
                else:
                    standings.append(_extract_team_standing(item))

            return {
                "status": "ok",
                "sport": sport,
                "standings": standings[:20],  # Top 20
            }


def _extract_team_standing(team_data: dict) -> dict:
    """Extract standardized standing info from API response."""
    team = team_data.get("team", {})
    all_stats = team_data.get("all", {}) or team_data.get("games", {})

    return {
        "rank": team_data.get("position", "N/A"),
        "team": team.get("name", "Unknown"),
        "wins": all_stats.get("win", 0),
        "losses": all_stats.get("lose", 0),
        "win_pct": team_data.get("form", "N/A"),
        "points": team_data.get("points", "N/A"),
    }


async def get_schedule(sport: str = "nba", team_name: str | None = None, date_from: str | None = None, date_to: str | None = None) -> dict[str, Any]:
    """
    Get upcoming games schedule.

    Args:
        sport: nba, nfl, nhl, mlb
        team_name: Optional team name to filter
        date_from: Start date (YYYY-MM-DD)
        date_to: End date (YYYY-MM-DD)

    Returns:
        List of upcoming games with date, teams, venue

    Free tier: 100 requests/day
    """
    if not cfg.apisports_key:
        return {
            "status": "error",
            "message": "APISPORTS_KEY not configured",
            "games": [],
        }

    sport_config = {
        "nba": {"endpoint": "basketball", "league": 12, "season": "2024-2025"},
        "nfl": {"endpoint": "american-football", "league": 1, "season": "2024"},
        "nhl": {"endpoint": "hockey", "league": 57, "season": "2024"},
        "mlb": {"endpoint": "baseball", "league": 1, "season": "2024"},
    }

    if sport.lower() not in sport_config:
        return {
            "status": "error",
            "message": f"Unsupported sport: {sport}",
            "games": [],
        }

    config = sport_config[sport.lower()]
    
    # Default to next 7 days if no dates specified
    if not date_from:
        date_from = datetime.now().strftime("%Y-%m-%d")
    
    params = {
        "league": config["league"],
        "season": config["season"],
        "from": date_from,
    }
    
    if date_to:
        params["to"] = date_to

    url = f"{APISPORTS_BASE_URL}/{config['endpoint']}/games"
    headers = {"x-apisports-key": cfg.apisports_key}

    async with SessionManager.get_session() as session:
        async with session.get(url, params=params, headers=headers, timeout=30) as resp:
            if resp.status == 429:
                ToolHealthMonitor.record_failure("apisports", "Rate limit exceeded")
                return {
                    "status": "error",
                    "message": "API-Sports rate limit exceeded. Free tier: 100 requests/day.",
                    "games": [],
                }

            if resp.status != 200:
                error_text = await resp.text()
                ToolHealthMonitor.record_failure("apisports", f"HTTP {resp.status}")
                return {
                    "status": "error",
                    "message": f"API-Sports error: {error_text}",
                    "games": [],
                }

            data = await resp.json()
            ToolHealthMonitor.record_success("apisports")

            games = []
            for game in data.get("response", []):
                home_team = game["teams"]["home"]["name"]
                away_team = game["teams"]["away"]["name"]
                
                # Filter by team if specified
                if team_name and team_name.lower() not in home_team.lower() and team_name.lower() not in away_team.lower():
                    continue
                
                games.append({
                    "date": game["date"],
                    "home": home_team,
                    "away": away_team,
                    "venue": game.get("venue", "TBD"),
                    "status": game["status"]["long"],
                })

            return {
                "status": "ok",
                "sport": sport,
                "games": games[:20],  # Limit to 20 games
            }


# LLM-callable skill definitions
SPORTS_SKILLS = [
    {
        "name": "get_nba_scores",
        "description": "Get NBA game scores for a specific date. Use for 'NBA scores last night' queries. Free tier: 100 req/day (shared across all sports).",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format (default: today)",
                },
                "team_id": {
                    "type": "integer",
                    "description": "Optional team ID to filter specific team games",
                },
            },
            "required": [],
        },
        "function": get_nba_scores,
    },
    {
        "name": "get_nfl_scores",
        "description": "Get NFL game scores for a specific date. Free tier: 100 req/day (shared).",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format (default: today)",
                },
                "team_id": {
                    "type": "integer",
                    "description": "Optional team ID to filter",
                },
            },
            "required": [],
        },
        "function": get_nfl_scores,
    },
    {
        "name": "get_team_standings",
        "description": "Get current league standings for NBA, NFL, NHL, or MLB. Shows wins, losses, rankings. Free tier: 100 req/day.",
        "parameters": {
            "type": "object",
            "properties": {
                "sport": {
                    "type": "string",
                    "enum": ["nba", "nfl", "nhl", "mlb"],
                    "description": "Sport league",
                    "default": "nba",
                },
                "league_id": {
                    "type": "integer",
                    "description": "Optional specific league ID (defaults: NBA=12, NFL=1, NHL=57, MLB=1)",
                },
                "season": {
                    "type": "string",
                    "description": "Season year (default: current season)",
                },
            },
            "required": [],
        },
        "function": get_team_standings,
    },
    {
        "name": "get_schedule",
        "description": "Get upcoming games schedule for NBA, NFL, NHL, or MLB. Can filter by team. Free tier: 100 req/day.",
        "parameters": {
            "type": "object",
            "properties": {
                "sport": {
                    "type": "string",
                    "enum": ["nba", "nfl", "nhl", "mlb"],
                    "description": "Sport league",
                    "default": "nba",
                },
                "team_name": {
                    "type": "string",
                    "description": "Optional team name to filter (e.g., 'Lakers', 'Patriots')",
                },
                "date_from": {
                    "type": "string",
                    "description": "Start date YYYY-MM-DD (default: today)",
                },
                "date_to": {
                    "type": "string",
                    "description": "End date YYYY-MM-DD",
                },
            },
            "required": [],
        },
        "function": get_schedule,
    },
]
