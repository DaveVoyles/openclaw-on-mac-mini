"""
Tests for User Management System
"""

import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from user_manager import User, UserManager, UserRole


@pytest.fixture
def temp_db():
    """Create temporary database for testing"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    yield db_path
    db_path.unlink(missing_ok=True)


@pytest.fixture
def user_manager(temp_db):
    """Create UserManager instance with temp database"""
    manager = UserManager(db_path=temp_db)
    yield manager
    manager.close()


class TestUserRegistration:
    """Test user registration and retrieval"""
    
    def test_register_new_user(self, user_manager):
        """Test registering a new user"""
        user = user_manager.register_user(
            discord_id=123456789,
            username="testuser",
            role=UserRole.MEMBER,
        )
        
        assert user.id > 0
        assert user.discord_id == 123456789
        assert user.username == "testuser"
        assert user.role == UserRole.MEMBER
        assert user.is_active
        assert user.created_at > 0
    
    def test_register_duplicate_discord_id(self, user_manager):
        """Test registering user with existing Discord ID updates username"""
        user1 = user_manager.register_user(123456789, "oldname")
        user2 = user_manager.register_user(123456789, "newname")
        
        assert user1.id == user2.id
        assert user2.username == "newname"
    
    def test_get_user_by_id(self, user_manager):
        """Test getting user by internal ID"""
        user = user_manager.register_user(123, "test")
        retrieved = user_manager.get_user(user.id)
        
        assert retrieved is not None
        assert retrieved.id == user.id
        assert retrieved.username == "test"
    
    def test_get_user_by_discord_id(self, user_manager):
        """Test getting user by Discord ID"""
        user = user_manager.register_user(456, "test")
        retrieved = user_manager.get_user_by_discord_id(456)
        
        assert retrieved is not None
        assert retrieved.discord_id == 456
    
    def test_get_nonexistent_user(self, user_manager):
        """Test getting non-existent user returns None"""
        assert user_manager.get_user(999) is None
        assert user_manager.get_user_by_discord_id(999) is None
    
    def test_list_users(self, user_manager):
        """Test listing all users"""
        user_manager.register_user(1, "user1", UserRole.ADMIN)
        user_manager.register_user(2, "user2", UserRole.MEMBER)
        user_manager.register_user(3, "user3", UserRole.VIEWER)
        
        all_users = user_manager.list_users()
        assert len(all_users) == 3
        
        admins = user_manager.list_users(role=UserRole.ADMIN)
        assert len(admins) == 1
        assert admins[0].role == UserRole.ADMIN


class TestUserRoles:
    """Test user role management"""
    
    def test_default_role(self, user_manager):
        """Test default role is MEMBER"""
        user = user_manager.register_user(123, "test")
        assert user.role == UserRole.MEMBER
    
    def test_update_role(self, user_manager):
        """Test updating user role"""
        user = user_manager.register_user(123, "test", UserRole.MEMBER)
        user_manager.update_role(user.id, UserRole.ADMIN)
        
        updated = user_manager.get_user(user.id)
        assert updated.role == UserRole.ADMIN
    
    def test_role_hierarchy(self):
        """Test role hierarchy comparison"""
        assert UserRole.ADMIN > UserRole.MEMBER
        assert UserRole.MEMBER > UserRole.VIEWER
        assert UserRole.ADMIN >= UserRole.ADMIN


class TestUserSettings:
    """Test user settings and preferences"""
    
    def test_update_settings(self, user_manager):
        """Test updating user settings"""
        user = user_manager.register_user(123, "test")
        
        settings = {"theme": "dark", "notifications": True}
        user_manager.update_settings(user.id, settings)
        
        retrieved_settings = user_manager.get_settings(user.id)
        assert retrieved_settings["theme"] == "dark"
        assert retrieved_settings["notifications"] is True
    
    def test_merge_settings(self, user_manager):
        """Test settings are merged, not replaced"""
        user = user_manager.register_user(123, "test")
        
        user_manager.update_settings(user.id, {"key1": "value1"})
        user_manager.update_settings(user.id, {"key2": "value2"})
        
        settings = user_manager.get_settings(user.id)
        assert settings["key1"] == "value1"
        assert settings["key2"] == "value2"


class TestSessionManagement:
    """Test user session management"""
    
    def test_create_session(self, user_manager):
        """Test creating a session token"""
        user = user_manager.register_user(123, "test")
        token = user_manager.create_session(user.id)
        
        assert token is not None
        assert len(token) > 20
        
        # Verify token is stored
        retrieved = user_manager.get_user_by_session_token(token)
        assert retrieved is not None
        assert retrieved.id == user.id
    
    def test_invalidate_session(self, user_manager):
        """Test invalidating a session"""
        user = user_manager.register_user(123, "test")
        token = user_manager.create_session(user.id)
        
        user_manager.invalidate_session(user.id)
        
        # Token should no longer work
        retrieved = user_manager.get_user_by_session_token(token)
        assert retrieved is None
    
    def test_update_activity(self, user_manager):
        """Test updating last active timestamp"""
        user = user_manager.register_user(123, "test")
        original_time = user.last_active
        
        time.sleep(0.1)
        user_manager.update_activity(user.id)
        
        updated = user_manager.get_user(user.id)
        assert updated.last_active > original_time


class TestQuotaManagement:
    """Test API quota management"""
    
    def test_default_quota(self, user_manager):
        """Test default quota is 100"""
        user = user_manager.register_user(123, "test")
        assert user.api_quota_daily == 100
        assert user.api_quota_used == 0
    
    def test_check_quota(self, user_manager):
        """Test checking quota availability"""
        user = user_manager.register_user(123, "test")
        assert user_manager.check_quota(user.id) is True
    
    def test_consume_quota(self, user_manager):
        """Test consuming quota"""
        user = user_manager.register_user(123, "test")
        
        assert user_manager.consume_quota(user.id, 10) is True
        
        updated = user_manager.get_user(user.id)
        assert updated.api_quota_used == 10
    
    def test_quota_exceeded(self, user_manager):
        """Test quota enforcement"""
        user = user_manager.register_user(123, "test")
        user_manager.set_quota(user.id, 10)
        
        # Consume all quota
        user_manager.consume_quota(user.id, 10)
        
        # Should not allow more
        assert user_manager.check_quota(user.id) is False
        assert user_manager.consume_quota(user.id, 1) is False
    
    def test_set_custom_quota(self, user_manager):
        """Test setting custom quota limit"""
        user = user_manager.register_user(123, "test")
        user_manager.set_quota(user.id, 500)
        
        updated = user_manager.get_user(user.id)
        assert updated.api_quota_daily == 500


class TestUserStatus:
    """Test user account status management"""
    
    def test_suspend_user(self, user_manager):
        """Test suspending user account"""
        user = user_manager.register_user(123, "test")
        token = user_manager.create_session(user.id)
        
        user_manager.suspend_user(user.id)
        
        updated = user_manager.get_user(user.id)
        assert updated.is_active is False
        
        # Session should be invalidated
        assert user_manager.get_user_by_session_token(token) is None
    
    def test_activate_user(self, user_manager):
        """Test activating suspended account"""
        user = user_manager.register_user(123, "test")
        user_manager.suspend_user(user.id)
        user_manager.activate_user(user.id)
        
        updated = user_manager.get_user(user.id)
        assert updated.is_active is True


class TestActivityLogging:
    """Test user activity logging"""
    
    def test_log_activity(self, user_manager):
        """Test activity logging"""
        user = user_manager.register_user(123, "test")
        
        # Registration should be logged
        activity = user_manager.get_user_activity(user.id)
        assert len(activity) > 0
        assert activity[0]["type"] == "registration"
    
    def test_get_activity_log(self, user_manager):
        """Test retrieving activity log"""
        user = user_manager.register_user(123, "test")
        user_manager.create_session(user.id)
        user_manager.update_role(user.id, UserRole.ADMIN)
        
        activity = user_manager.get_user_activity(user.id, limit=10)
        assert len(activity) >= 3  # registration, login, role_change


class TestStatistics:
    """Test user statistics"""
    
    def test_get_stats(self, user_manager):
        """Test getting user statistics"""
        user_manager.register_user(1, "admin", UserRole.ADMIN)
        user_manager.register_user(2, "member1", UserRole.MEMBER)
        user_manager.register_user(3, "member2", UserRole.MEMBER)
        user_manager.register_user(4, "viewer", UserRole.VIEWER)
        
        user_manager.suspend_user(4)
        
        stats = user_manager.get_stats()
        
        assert stats["total_users"] == 4
        assert stats["active_users"] == 3
        assert stats["suspended_users"] == 1
        assert stats["by_role"]["admin"] == 1
        assert stats["by_role"]["member"] == 2
        assert stats["by_role"]["viewer"] == 1
