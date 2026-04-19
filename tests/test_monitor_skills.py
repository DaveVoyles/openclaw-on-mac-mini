"""
Tests for monitor_skills.py — URL content change detection.

Covers: SSRF guard, content hashing, snapshot CRUD, change detection,
list/remove operations, and _fetch_text normalization.
"""

import hashlib

import pytest

import monitor_skills as ms

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolate_snapshots(tmp_path, monkeypatch):
    """Redirect snapshot storage to a temp directory for every test."""
    monkeypatch.setattr(ms, "MEMORY_DIR", tmp_path)
    monkeypatch.setattr(ms, "_SNAPSHOTS_FILE", tmp_path / "url_snapshots.json")
    # Reset the SessionManager's cached session so tests don't share connections
    ms._sessions._session = None


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------

class TestSSRFGuard:
    @pytest.mark.asyncio
    async def test_rejects_localhost(self):
        with pytest.raises(ValueError, match="private|localhost"):
            await ms._fetch_text("http://localhost:8080/secret")

    @pytest.mark.asyncio
    async def test_rejects_127_ip(self):
        with pytest.raises(ValueError, match="private|localhost"):
            await ms._fetch_text("http://127.0.0.1/admin")

    @pytest.mark.asyncio
    async def test_rejects_192_168(self):
        with pytest.raises(ValueError, match="private|localhost"):
            await ms._fetch_text("http://192.168.1.1/config")

    @pytest.mark.asyncio
    async def test_rejects_10_dot(self):
        with pytest.raises(ValueError, match="private|localhost"):
            await ms._fetch_text("http://10.0.0.1/")

    @pytest.mark.asyncio
    async def test_rejects_172_private(self):
        with pytest.raises(ValueError, match="private|localhost"):
            await ms._fetch_text("http://172.16.0.1/")

    @pytest.mark.asyncio
    async def test_rejects_non_http(self):
        with pytest.raises(ValueError, match="http"):
            await ms._fetch_text("ftp://example.com/file")

    @pytest.mark.asyncio
    async def test_allows_public_url_pattern(self):
        """Public URLs should pass the SSRF regex check (not blocked by guard)."""
        # We just confirm the SSRF pattern does not match public URLs
        assert not ms._SSRF_PRIVATE.match("https://example.com")
        assert not ms._SSRF_PRIVATE.match("https://google.com/search?q=test")


# ---------------------------------------------------------------------------
# Content hashing
# ---------------------------------------------------------------------------

class TestContentHash:
    def test_monitor_skills_deterministic(self):
        assert ms._content_hash("hello") == ms._content_hash("hello")

    def test_different_for_different_input(self):
        assert ms._content_hash("a") != ms._content_hash("b")

    def test_uses_sha256_prefix(self):
        expected = hashlib.sha256("test".encode()).hexdigest()[:16]
        assert ms._content_hash("test") == expected

    def test_length_is_16(self):
        assert len(ms._content_hash("any content")) == 16


# ---------------------------------------------------------------------------
# Snapshot storage (load/save)
# ---------------------------------------------------------------------------

class TestSnapshotStorage:
    def test_monitor_skills_load_returns_empty_when_no_file(self):
        assert ms._load_snapshots() == {}

    def test_monitor_skills_save_and_load_roundtrip(self, tmp_path):
        data = {"https://example.com": {"url": "https://example.com", "hash": "abc123"}}
        ms._save_snapshots(data)
        loaded = ms._load_snapshots()
        assert loaded == data

    def test_load_handles_corrupt_json(self, tmp_path):
        snap_file = tmp_path / "url_snapshots.json"
        snap_file.write_text("NOT VALID JSON {{{")
        assert ms._load_snapshots() == {}


# ---------------------------------------------------------------------------
# snapshot_url
# ---------------------------------------------------------------------------

class TestSnapshotUrl:
    @pytest.mark.asyncio
    async def test_successful_snapshot(self, monkeypatch):
        """Snapshot stores URL metadata and returns confirmation."""
        async def mock_fetch(url):
            return "Hello World page content"
        monkeypatch.setattr(ms, "_fetch_text", mock_fetch)

        result = await ms.snapshot_url("https://example.com", "Example")
        assert "Snapshot saved" in result
        assert "Example" in result

        # Verify stored data
        snaps = ms._load_snapshots()
        assert "https://example.com" in snaps
        assert snaps["https://example.com"]["label"] == "Example"
        assert snaps["https://example.com"]["change_count"] == 0

    @pytest.mark.asyncio
    async def test_snapshot_fetch_failure(self, monkeypatch):
        async def mock_fetch(url):
            raise RuntimeError("Connection refused")
        monkeypatch.setattr(ms, "_fetch_text", mock_fetch)

        result = await ms.snapshot_url("https://down.example.com")
        assert "Could not fetch" in result


# ---------------------------------------------------------------------------
# check_url_for_changes
# ---------------------------------------------------------------------------

class TestCheckUrlForChanges:
    @pytest.mark.asyncio
    async def test_no_baseline_snapshot(self):
        result = await ms.check_url_for_changes("https://unknown.example.com")
        assert "No baseline snapshot" in result

    @pytest.mark.asyncio
    async def test_no_change_detected(self, monkeypatch):
        """When content hash matches, report no change."""
        content = "Static page content"
        async def mock_fetch(url):
            return content
        monkeypatch.setattr(ms, "_fetch_text", mock_fetch)

        # Create a baseline
        await ms.snapshot_url("https://example.com", "Test")

        # Check — same content
        result = await ms.check_url_for_changes("https://example.com")
        assert "No change" in result

    @pytest.mark.asyncio
    async def test_change_detected(self, monkeypatch):
        """When content hash differs, report change."""
        call_count = 0
        async def mock_fetch(url):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "Original content"
            return "Updated content!"
        monkeypatch.setattr(ms, "_fetch_text", mock_fetch)

        await ms.snapshot_url("https://example.com", "Test")
        result = await ms.check_url_for_changes("https://example.com")
        assert "Change detected" in result
        assert "change #1" in result

    @pytest.mark.asyncio
    async def test_change_count_increments(self, monkeypatch):
        """Change count should increment on each change detection."""
        version = [0]
        async def mock_fetch(url):
            version[0] += 1
            return f"Content version {version[0]}"
        monkeypatch.setattr(ms, "_fetch_text", mock_fetch)

        await ms.snapshot_url("https://example.com")
        await ms.check_url_for_changes("https://example.com")
        await ms.check_url_for_changes("https://example.com")

        snaps = ms._load_snapshots()
        assert snaps["https://example.com"]["change_count"] == 2


# ---------------------------------------------------------------------------
# list_monitored_urls / remove_url_monitor
# ---------------------------------------------------------------------------

class TestListAndRemove:
    @pytest.mark.asyncio
    async def test_monitor_skills_list_empty(self):
        result = await ms.list_monitored_urls()
        assert "No URLs" in result

    @pytest.mark.asyncio
    async def test_list_after_snapshot(self, monkeypatch):
        async def mock_fetch(url):
            return "content"
        monkeypatch.setattr(ms, "_fetch_text", mock_fetch)
        await ms.snapshot_url("https://example.com", "MyLabel")

        result = await ms.list_monitored_urls()
        assert "MyLabel" in result
        assert "1 total" in result

    @pytest.mark.asyncio
    async def test_remove_existing_url(self, monkeypatch):
        async def mock_fetch(url):
            return "content"
        monkeypatch.setattr(ms, "_fetch_text", mock_fetch)
        await ms.snapshot_url("https://example.com", "MyLabel")

        result = await ms.remove_url_monitor("https://example.com")
        assert "Removed" in result
        assert ms._load_snapshots() == {}

    @pytest.mark.asyncio
    async def test_remove_nonexistent_url(self):
        result = await ms.remove_url_monitor("https://nope.example.com")
        assert "not in the monitor list" in result
