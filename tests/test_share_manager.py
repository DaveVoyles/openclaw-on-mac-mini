"""
Tests for Share Manager
"""

import tempfile
import time
from pathlib import Path

import pytest

from share_manager import (
    ShareManager,
    ShareDuration,
    ResourceType,
    ShareLink,
)
from user_manager import UserManager, UserRole


@pytest.fixture
def temp_dbs():
    """Create temporary databases for testing"""
    with tempfile.NamedTemporaryFile(suffix="_users.db", delete=False) as f:
        users_db = Path(f.name)
    with tempfile.NamedTemporaryFile(suffix="_shares.db", delete=False) as f:
        shares_db = Path(f.name)
    
    yield users_db, shares_db
    
    users_db.unlink(missing_ok=True)
    shares_db.unlink(missing_ok=True)


@pytest.fixture
def managers(temp_dbs):
    """Create manager instances"""
    users_db, shares_db = temp_dbs
    
    user_manager = UserManager(db_path=users_db)
    share_manager = ShareManager(db_path=shares_db)
    
    yield user_manager, share_manager
    
    user_manager.close()
    share_manager.close()


@pytest.fixture
def sample_user(managers):
    """Create a sample user for testing"""
    user_manager, _ = managers
    return user_manager.register_user(1, "testuser", UserRole.ADMIN)


class TestShareCreation:
    """Test share link creation"""
    
    def test_create_share(self, managers, sample_user):
        """Test creating a share link"""
        _, share_manager = managers
        
        share = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dashboard-123",
            sample_user.id,
            ShareDuration.DAYS_7,
        )
        
        assert share.id > 0
        assert len(share.token) > 20
        assert share.resource_type == ResourceType.DASHBOARD
        assert share.resource_id == "dashboard-123"
        assert share.created_by == sample_user.id
        assert share.is_public
        assert share.is_active
        assert share.view_count == 0
    
    def test_token_is_unique(self, managers, sample_user):
        """Test share tokens are unique"""
        _, share_manager = managers
        
        share1 = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dash-1",
            sample_user.id,
        )
        
        share2 = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dash-2",
            sample_user.id,
        )
        
        assert share1.token != share2.token
    
    def test_create_share_with_metadata(self, managers, sample_user):
        """Test creating share with metadata"""
        _, share_manager = managers
        
        metadata = {"description": "Test dashboard", "tags": ["analytics", "sales"]}
        share = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dash-1",
            sample_user.id,
            metadata=metadata,
        )
        
        assert share.metadata["description"] == "Test dashboard"
        assert "analytics" in share.metadata["tags"]
    
    def test_private_share(self, managers, sample_user):
        """Test creating private share"""
        _, share_manager = managers
        
        share = share_manager.create_share(
            ResourceType.QUERY,
            "query-1",
            sample_user.id,
            is_public=False,
        )
        
        assert share.is_public is False


class TestShareRetrieval:
    """Test share link retrieval"""
    
    def test_get_share_by_id(self, managers, sample_user):
        """Test getting share by ID"""
        _, share_manager = managers
        
        share = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dash-1",
            sample_user.id,
        )
        
        retrieved = share_manager.get_share(share.id)
        assert retrieved is not None
        assert retrieved.token == share.token
    
    def test_get_share_by_token(self, managers, sample_user):
        """Test getting share by token"""
        _, share_manager = managers
        
        share = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dash-1",
            sample_user.id,
        )
        
        retrieved = share_manager.get_share_by_token(share.token)
        assert retrieved is not None
        assert retrieved.id == share.id
    
    def test_get_share_by_resource(self, managers, sample_user):
        """Test getting share by resource"""
        _, share_manager = managers
        
        share = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dash-1",
            sample_user.id,
        )
        
        retrieved = share_manager.get_share_by_resource(
            ResourceType.DASHBOARD,
            "dash-1",
        )
        assert retrieved is not None
        assert retrieved.id == share.id
    
    def test_list_shares(self, managers, sample_user):
        """Test listing shares"""
        _, share_manager = managers
        
        share_manager.create_share(ResourceType.DASHBOARD, "d1", sample_user.id)
        share_manager.create_share(ResourceType.QUERY, "q1", sample_user.id)
        share_manager.create_share(ResourceType.DASHBOARD, "d2", sample_user.id)
        
        all_shares = share_manager.list_shares()
        assert len(all_shares) == 3
        
        user_shares = share_manager.list_shares(created_by=sample_user.id)
        assert len(user_shares) == 3
        
        dashboard_shares = share_manager.list_shares(resource_type=ResourceType.DASHBOARD)
        assert len(dashboard_shares) == 2


class TestShareExpiration:
    """Test share expiration"""
    
    def test_share_duration_24h(self, managers, sample_user):
        """Test 24-hour expiration"""
        _, share_manager = managers
        
        share = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dash-1",
            sample_user.id,
            ShareDuration.HOURS_24,
        )
        
        assert share.expires_at is not None
        expected_expiry = time.time() + 24 * 3600
        assert abs(share.expires_at - expected_expiry) < 60  # Within 1 minute
    
    def test_share_duration_never(self, managers, sample_user):
        """Test never-expiring share"""
        _, share_manager = managers
        
        share = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dash-1",
            sample_user.id,
            ShareDuration.NEVER,
        )
        
        assert share.expires_at is None
        assert not share.is_expired
    
    def test_is_expired_property(self, managers, sample_user):
        """Test is_expired property"""
        _, share_manager = managers
        
        # Create share that expires in past
        share = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dash-1",
            sample_user.id,
            ShareDuration.HOURS_24,
        )
        
        # Manually set expiry to past
        past_time = time.time() - 3600
        share_manager.update_share(share.id, expires_at=past_time)
        
        updated = share_manager.get_share(share.id)
        assert updated.is_expired is True
        assert updated.is_valid is False
    
    def test_cleanup_expired(self, managers, sample_user):
        """Test cleaning up expired shares"""
        _, share_manager = managers
        
        # Create expired share
        share1 = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dash-1",
            sample_user.id,
        )
        share_manager.update_share(share1.id, expires_at=time.time() - 3600)
        
        # Create valid share
        share2 = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dash-2",
            sample_user.id,
            ShareDuration.DAYS_7,
        )
        
        # Cleanup
        removed = share_manager.cleanup_expired()
        assert removed == 1
        
        # Valid share should still exist
        assert share_manager.get_share(share2.id) is not None
        assert share_manager.get_share(share1.id) is None


class TestShareRevocation:
    """Test share revocation"""
    
    def test_revoke_share(self, managers, sample_user):
        """Test revoking a share"""
        _, share_manager = managers
        
        share = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dash-1",
            sample_user.id,
        )
        
        share_manager.revoke_share(share.id)
        
        updated = share_manager.get_share(share.id)
        assert updated.is_active is False
        assert updated.is_valid is False
    
    def test_revoke_by_token(self, managers, sample_user):
        """Test revoking share by token"""
        _, share_manager = managers
        
        share = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dash-1",
            sample_user.id,
        )
        
        share_manager.revoke_share_by_token(share.token)
        
        updated = share_manager.get_share(share.id)
        assert updated.is_active is False


class TestAccessTracking:
    """Test access tracking and analytics"""
    
    def test_record_access(self, managers, sample_user):
        """Test recording access to share"""
        _, share_manager = managers
        
        share = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dash-1",
            sample_user.id,
        )
        
        share_manager.record_access(
            share.id,
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
            referrer="https://example.com",
        )
        
        updated = share_manager.get_share(share.id)
        assert updated.view_count == 1
        assert updated.last_accessed is not None
    
    def test_multiple_accesses(self, managers, sample_user):
        """Test recording multiple accesses"""
        _, share_manager = managers
        
        share = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dash-1",
            sample_user.id,
        )
        
        share_manager.record_access(share.id, ip_address="192.168.1.1")
        share_manager.record_access(share.id, ip_address="192.168.1.2")
        share_manager.record_access(share.id, ip_address="192.168.1.1")
        
        updated = share_manager.get_share(share.id)
        assert updated.view_count == 3
    
    def test_get_access_analytics(self, managers, sample_user):
        """Test getting access analytics"""
        _, share_manager = managers
        
        share = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dash-1",
            sample_user.id,
        )
        
        share_manager.record_access(share.id, ip_address="192.168.1.1")
        share_manager.record_access(share.id, ip_address="192.168.1.2")
        
        analytics = share_manager.get_access_analytics(share.id)
        
        assert len(analytics) == 2
        assert analytics[0]["ip"] == "192.168.1.2"  # Most recent first
        assert analytics[1]["ip"] == "192.168.1.1"
    
    def test_get_access_stats(self, managers, sample_user):
        """Test getting aggregated access stats"""
        _, share_manager = managers
        
        share = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dash-1",
            sample_user.id,
        )
        
        share_manager.record_access(share.id, ip_address="192.168.1.1")
        share_manager.record_access(share.id, ip_address="192.168.1.2")
        share_manager.record_access(share.id, ip_address="192.168.1.1")  # Duplicate IP
        
        stats = share_manager.get_access_stats(share.id)
        
        assert stats["total_views"] == 3
        assert stats["unique_visitors"] == 2
        assert stats["last_accessed"] is not None


class TestEmbedCode:
    """Test embed code generation"""
    
    def test_create_embed_code(self, managers, sample_user):
        """Test generating embed code"""
        _, share_manager = managers
        
        share = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dash-1",
            sample_user.id,
        )
        
        embed_code = share_manager.create_embed_code(share.id)
        
        assert "<iframe" in embed_code
        assert share.token in embed_code
        assert 'width="100%"' in embed_code
    
    def test_custom_embed_dimensions(self, managers, sample_user):
        """Test custom embed dimensions"""
        _, share_manager = managers
        
        share = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dash-1",
            sample_user.id,
        )
        
        embed_code = share_manager.create_embed_code(
            share.id,
            width="800px",
            height="400px",
        )
        
        assert 'width="800px"' in embed_code
        assert 'height="400px"' in embed_code
    
    def test_get_embed_config(self, managers, sample_user):
        """Test retrieving embed configuration"""
        _, share_manager = managers
        
        share = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dash-1",
            sample_user.id,
        )
        
        allowed_domains = ["example.com", "test.com"]
        embed_code = share_manager.create_embed_code(
            share.id,
            allowed_domains=allowed_domains,
        )
        
        config = share_manager.get_embed_config(share.id)
        
        assert config is not None
        assert config["embed_code"] == embed_code
        assert config["allowed_domains"] == allowed_domains


class TestStatistics:
    """Test global sharing statistics"""
    
    def test_get_stats(self, managers, sample_user):
        """Test getting global statistics"""
        _, share_manager = managers
        
        share1 = share_manager.create_share(
            ResourceType.DASHBOARD,
            "dash-1",
            sample_user.id,
        )
        share2 = share_manager.create_share(
            ResourceType.QUERY,
            "query-1",
            sample_user.id,
        )
        
        share_manager.record_access(share1.id)
        share_manager.record_access(share1.id)
        share_manager.record_access(share2.id)
        
        share_manager.revoke_share(share2.id)
        
        stats = share_manager.get_stats()
        
        assert stats["total_shares"] == 2
        assert stats["active_shares"] == 1
        assert stats["total_views"] == 3
