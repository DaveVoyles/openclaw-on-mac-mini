"""
Tests for smart media automation skills (Phase 3).
"""

from unittest.mock import AsyncMock, patch

import pytest

from skills.smart_media_skills import (
    cleanup_duplicates,
    determine_quality_profile,
    find_duplicates,
    get_storage_info,
    optimize_quality_profiles,
    schedule_downloads,
    sync_imdb_list,
    sync_trakt_watchlist,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_sonarr_storage():
    """Mock Sonarr storage API response."""
    return [
        {
            "path": "/data",
            "freeSpace": 500 * (1024**3),  # 500 GB free
            "totalSpace": 1000 * (1024**3),  # 1 TB total
        }
    ]


@pytest.fixture
def mock_radarr_storage_low():
    """Mock Radarr storage API response (low space)."""
    return [
        {
            "path": "/data",
            "freeSpace": 150 * (1024**3),  # 150 GB free
            "totalSpace": 1000 * (1024**3),  # 1 TB total
        }
    ]


@pytest.fixture
def mock_movies_list():
    """Mock Radarr movies list."""
    return [
        {"id": 1, "title": "The Matrix", "year": 1999},
        {"id": 2, "title": "Inception", "year": 2010},
        {"id": 3, "title": "The Matrix", "year": 1999},  # Duplicate
        {"id": 4, "title": "Interstellar", "year": 2014},
    ]


@pytest.fixture
def mock_quality_profiles():
    """Mock quality profiles."""
    return [
        {"id": 1, "name": "Bluray-1080p"},
        {"id": 2, "name": "Web-1080p"},
        {"id": 3, "name": "Web-720p"},
    ]


# ---------------------------------------------------------------------------
# Storage Management Tests
# ---------------------------------------------------------------------------


class TestStorageManagement:
    @pytest.mark.asyncio
    async def test_get_storage_info_sonarr(self, mock_sonarr_storage):
        with patch("skills.smart_media_skills._api_get", AsyncMock(return_value=mock_sonarr_storage)):
            result = await get_storage_info("sonarr")

            assert "error" not in result
            assert result["free_gb"] == 500.0
            assert result["total_gb"] == 1000.0
            assert result["used_gb"] == 500.0
            assert result["percent_free"] == 50.0

    @pytest.mark.asyncio
    async def test_get_storage_info_radarr(self, mock_radarr_storage_low):
        with patch("skills.smart_media_skills._api_get", AsyncMock(return_value=mock_radarr_storage_low)):
            result = await get_storage_info("radarr")

            assert "error" not in result
            assert result["free_gb"] == 150.0
            assert result["percent_free"] == 15.0

    @pytest.mark.asyncio
    async def test_get_storage_info_error(self):
        with patch("skills.smart_media_skills._api_get", AsyncMock(return_value="Connection failed")):
            result = await get_storage_info("sonarr")

            assert "error" in result
            assert result["error"] == "Connection failed"

    @pytest.mark.asyncio
    async def test_get_storage_info_unknown_service(self):
        result = await get_storage_info("unknown")

        assert "error" in result
        assert "Unknown service" in result["error"]


# ---------------------------------------------------------------------------
# Quality Profile Tests
# ---------------------------------------------------------------------------


class TestQualityProfiles:
    @pytest.mark.asyncio
    async def test_determine_quality_high_storage(self, mock_sonarr_storage):
        with patch("skills.smart_media_skills.get_storage_info", AsyncMock(return_value={
            "free_gb": 600.0,
            "total_gb": 1000.0,
        })):
            result = await determine_quality_profile("sonarr")

            assert result["recommended_profile"] == "high"
            assert "Bluray" in result["profile_name"]
            assert "high quality recommended" in result["reason"]

    @pytest.mark.asyncio
    async def test_determine_quality_medium_storage(self):
        with patch("skills.smart_media_skills.get_storage_info", AsyncMock(return_value={
            "free_gb": 300.0,
            "total_gb": 1000.0,
        })):
            result = await determine_quality_profile("radarr")

            assert result["recommended_profile"] == "medium"
            assert "Web-1080p" in result["profile_name"]

    @pytest.mark.asyncio
    async def test_determine_quality_low_storage(self):
        with patch("skills.smart_media_skills.get_storage_info", AsyncMock(return_value={
            "free_gb": 100.0,
            "total_gb": 1000.0,
        })):
            result = await determine_quality_profile("sonarr")

            assert result["recommended_profile"] == "low"
            assert "720p" in result["profile_name"]

    @pytest.mark.asyncio
    async def test_optimize_quality_profiles(self):
        with patch("skills.smart_media_skills.determine_quality_profile") as mock_determine:
            mock_determine.side_effect = [
                {"recommended_profile": "high", "profile_name": "Bluray-1080p",
                 "reason": "600GB available - high quality recommended"},
                {"recommended_profile": "medium", "profile_name": "Web-1080p",
                 "reason": "300GB available - medium quality recommended"},
            ]

            result = await optimize_quality_profiles()

            assert "Sonarr" in result
            assert "Radarr" in result
            assert "Bluray-1080p" in result
            assert "Web-1080p" in result


# ---------------------------------------------------------------------------
# Watchlist Sync Tests
# ---------------------------------------------------------------------------


class TestWatchlistSync:
    @pytest.mark.asyncio
    async def test_sync_trakt_watchlist(self):
        result = await sync_trakt_watchlist(username="testuser", list_type="watchlist")

        assert "testuser" in result
        assert "watchlist" in result
        assert "Trakt" in result

    @pytest.mark.asyncio
    async def test_sync_trakt_no_username(self):
        result = await sync_trakt_watchlist()

        assert "❌" in result
        assert "username" in result.lower()

    @pytest.mark.asyncio
    async def test_sync_imdb_list(self):
        mock_import_lists = [
            {"id": 1, "implementation": "IMDbListImport", "name": "My IMDb List"}
        ]

        with patch("skills.smart_media_skills._api_get", AsyncMock(return_value=mock_import_lists)):
            with patch("skills.smart_media_skills._api_post", AsyncMock(return_value={})):
                result = await sync_imdb_list(list_id="ls123456789")

                assert "✅" in result
                assert "ls123456789" in result

    @pytest.mark.asyncio
    async def test_sync_imdb_list_not_configured(self):
        with patch("skills.smart_media_skills._api_get", AsyncMock(return_value=[])):
            result = await sync_imdb_list(list_id="ls123456789")

            assert "not configured" in result.lower()


# ---------------------------------------------------------------------------
# Download Scheduling Tests
# ---------------------------------------------------------------------------


class TestDownloadScheduling:
    @pytest.mark.asyncio
    async def test_schedule_downloads_default(self):
        result = await schedule_downloads()

        assert "02:00" in result
        assert "05:59" in result
        assert "Download scheduling configured" in result

    @pytest.mark.asyncio
    async def test_schedule_downloads_custom(self):
        result = await schedule_downloads(hours=[1, 2, 3])

        assert "01:00" in result
        assert "03:59" in result

    @pytest.mark.asyncio
    async def test_schedule_downloads_invalid_hours(self):
        result = await schedule_downloads(hours=[25, 30])

        assert "❌" in result
        assert "0 and 23" in result


# ---------------------------------------------------------------------------
# Duplicate Detection Tests
# ---------------------------------------------------------------------------


class TestDuplicateDetection:
    @pytest.mark.asyncio
    async def test_find_duplicates_found(self, mock_movies_list):
        with patch("skills.smart_media_skills._api_get", AsyncMock(return_value=mock_movies_list)):
            result = await find_duplicates("radarr")

            assert "duplicates" in result.lower()
            assert "The Matrix" in result
            assert "2 copies" in result or "copies" in result.lower()

    @pytest.mark.asyncio
    async def test_find_duplicates_none(self):
        unique_movies = [
            {"id": 1, "title": "The Matrix", "year": 1999},
            {"id": 2, "title": "Inception", "year": 2010},
        ]

        with patch("skills.smart_media_skills._api_get", AsyncMock(return_value=unique_movies)):
            result = await find_duplicates("radarr")

            assert "No duplicates" in result
            assert "2 movies checked" in result or "2" in result

    @pytest.mark.asyncio
    async def test_find_duplicates_error(self):
        with patch("skills.smart_media_skills._api_get", AsyncMock(return_value="API Error")):
            result = await find_duplicates("radarr")

            assert "❌" in result
            assert "Failed" in result

    @pytest.mark.asyncio
    async def test_cleanup_duplicates_dry_run(self, mock_movies_list):
        with patch("skills.smart_media_skills._api_get", AsyncMock(return_value=mock_movies_list)):
            result = await cleanup_duplicates("radarr", dry_run=True)

            assert "Dry run" in result
            assert "The Matrix" in result

    @pytest.mark.asyncio
    async def test_cleanup_duplicates_live(self):
        result = await cleanup_duplicates("radarr", dry_run=False)

        assert "not yet implemented" in result.lower()


# ---------------------------------------------------------------------------
# LLM-Callable Skills Tests
# ---------------------------------------------------------------------------


class TestLLMCallableSkills:
    @pytest.mark.asyncio
    async def test_get_media_storage(self):
        with patch("skills.smart_media_skills.get_storage_info") as mock_storage:
            mock_storage.side_effect = [
                {"free_gb": 500, "total_gb": 1000, "percent_free": 50},
                {"free_gb": 300, "total_gb": 1000, "percent_free": 30},
            ]

            from skills.smart_media_skills import get_media_storage
            result = await get_media_storage()

            assert "Sonarr" in result
            assert "Radarr" in result
            assert "500GB" in result
            assert "300GB" in result

    @pytest.mark.asyncio
    async def test_sync_watchlist_trakt(self):
        from skills.smart_media_skills import sync_watchlist

        result = await sync_watchlist(source="trakt", username="testuser")

        assert "testuser" in result
        assert "Trakt" in result

    @pytest.mark.asyncio
    async def test_sync_watchlist_imdb(self):
        from skills.smart_media_skills import sync_watchlist

        with patch("skills.smart_media_skills.sync_imdb_list", AsyncMock(return_value="Success")):
            result = await sync_watchlist(source="imdb", list_id="ls123")

            assert result == "Success"

    @pytest.mark.asyncio
    async def test_sync_watchlist_unknown_source(self):
        from skills.smart_media_skills import sync_watchlist

        result = await sync_watchlist(source="unknown")

        assert "❌" in result
        assert "Unknown source" in result

    @pytest.mark.asyncio
    async def test_schedule_downloads_skill(self):
        from skills.smart_media_skills import schedule_downloads_skill

        result = await schedule_downloads_skill(hours="1,2,3,4")

        assert "01:00" in result
        assert "04:59" in result

    @pytest.mark.asyncio
    async def test_schedule_downloads_skill_invalid(self):
        from skills.smart_media_skills import schedule_downloads_skill

        result = await schedule_downloads_skill(hours="invalid")

        assert "❌" in result
        assert "Invalid hours" in result

    @pytest.mark.asyncio
    async def test_optimize_quality_skill(self):
        from skills.smart_media_skills import optimize_quality

        with patch("skills.smart_media_skills.optimize_quality_profiles",
                   AsyncMock(return_value="Quality recommendations")):
            result = await optimize_quality()

            assert result == "Quality recommendations"

    @pytest.mark.asyncio
    async def test_find_media_duplicates_skill(self):
        from skills.smart_media_skills import find_media_duplicates

        with patch("skills.smart_media_skills.find_duplicates",
                   AsyncMock(return_value="Duplicates found")):
            result = await find_media_duplicates("radarr")

            assert result == "Duplicates found"


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestSmartMediaIntegration:
    @pytest.mark.asyncio
    async def test_full_workflow_storage_to_quality(self):
        """Test workflow: check storage -> recommend quality -> apply."""
        from skills import smart_media_skills as smart_media

        # Mock low storage
        with patch("skills.smart_media_skills.get_storage_info", AsyncMock(return_value={
            "free_gb": 180.0,
            "total_gb": 1000.0,
            "percent_free": 18.0,
        })):
            storage = await smart_media.get_storage_info("sonarr")
            assert storage["free_gb"] == 180.0

            # Determine quality
            quality = await determine_quality_profile("sonarr")
            assert quality["recommended_profile"] == "low"
            assert "720p" in quality["profile_name"]

    @pytest.mark.asyncio
    async def test_duplicate_detection_and_cleanup_workflow(self):
        """Test workflow: find duplicates -> review -> cleanup."""
        mock_movies = [
            {"id": 1, "title": "Avatar", "year": 2009},
            {"id": 2, "title": "Avatar", "year": 2009},
        ]

        with patch("skills.smart_media_skills._api_get", AsyncMock(return_value=mock_movies)):
            # Find duplicates
            result = await find_duplicates("radarr")
            assert "Avatar" in result
            assert "duplicate" in result.lower()

            # Dry run cleanup
            cleanup_result = await cleanup_duplicates("radarr", dry_run=True)
            assert "Dry run" in cleanup_result
            assert "Avatar" in cleanup_result
