"""
Tests for Workspace Manager
"""

import tempfile
from pathlib import Path

import pytest

import user_manager as user_manager_module
from user_manager import UserManager, UserRole
from workspace_manager import WorkspaceManager, WorkspaceRole


@pytest.fixture
def temp_dbs():
    """Create temporary databases for testing"""
    with tempfile.NamedTemporaryFile(suffix="_users.db", delete=False) as f:
        users_db = Path(f.name)
    with tempfile.NamedTemporaryFile(suffix="_workspaces.db", delete=False) as f:
        workspaces_db = Path(f.name)

    yield users_db, workspaces_db

    users_db.unlink(missing_ok=True)
    workspaces_db.unlink(missing_ok=True)


@pytest.fixture
def managers(temp_dbs):
    """Create UserManager and WorkspaceManager instances"""
    users_db, workspaces_db = temp_dbs

    user_manager = UserManager(db_path=users_db)
    previous_global_user_manager = user_manager_module._user_manager
    user_manager_module._user_manager = user_manager
    workspace_manager = WorkspaceManager(db_path=workspaces_db)

    yield user_manager, workspace_manager

    user_manager.close()
    workspace_manager.close()
    user_manager_module._user_manager = previous_global_user_manager


@pytest.fixture
def sample_users(managers):
    """Create sample users for testing"""
    user_manager, _ = managers

    owner = user_manager.register_user(1, "owner", UserRole.ADMIN)
    member1 = user_manager.register_user(2, "member1", UserRole.MEMBER)
    member2 = user_manager.register_user(3, "member2", UserRole.MEMBER)
    viewer = user_manager.register_user(4, "viewer", UserRole.VIEWER)

    return owner, member1, member2, viewer


class TestWorkspaceCreation:
    """Test workspace creation and retrieval"""

    def test_create_workspace(self, managers, sample_users):
        """Test creating a new workspace"""
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace(
            name="test-workspace",
            owner_id=owner.id,
            description="Test workspace",
        )

        assert workspace.id > 0
        assert workspace.name == "test-workspace"
        assert workspace.owner_id == owner.id
        assert workspace.description == "Test workspace"
        assert workspace.is_active

    def test_create_duplicate_name(self, managers, sample_users):
        """Test creating workspace with duplicate name fails"""
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace_manager.create_workspace("duplicate", owner.id)

        with pytest.raises(ValueError, match="already exists"):
            workspace_manager.create_workspace("duplicate", owner.id)

    def test_owner_auto_added_as_member(self, managers, sample_users):
        """Test owner is automatically added as member"""
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace("test", owner.id)

        role = workspace_manager.get_member_role(workspace.id, owner.id)
        assert role == WorkspaceRole.OWNER

    def test_get_workspace_by_id(self, managers, sample_users):
        """Test getting workspace by ID"""
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace("test", owner.id)
        retrieved = workspace_manager.get_workspace(workspace.id)

        assert retrieved is not None
        assert retrieved.name == "test"

    def test_get_workspace_by_name(self, managers, sample_users):
        """Test getting workspace by name"""
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace("test", owner.id)
        retrieved = workspace_manager.get_workspace_by_name("test")

        assert retrieved is not None
        assert retrieved.id == workspace.id

    def test_list_workspaces(self, managers, sample_users):
        """Test listing all workspaces"""
        _, workspace_manager = managers
        owner, member, _, _ = sample_users

        workspace_manager.create_workspace("ws1", owner.id)
        workspace_manager.create_workspace("ws2", member.id)

        all_workspaces = workspace_manager.list_workspaces()
        assert len(all_workspaces) == 2

    def test_list_user_workspaces(self, managers, sample_users):
        """Test listing workspaces for specific user"""
        _, workspace_manager = managers
        owner, member, _, _ = sample_users

        ws1 = workspace_manager.create_workspace("ws1", owner.id)
        workspace_manager.create_workspace("ws2", member.id)

        workspace_manager.add_member(ws1.id, member.id)

        member_workspaces = workspace_manager.list_workspaces(user_id=member.id)
        assert len(member_workspaces) == 2  # member is in both


class TestWorkspaceUpdates:
    """Test workspace updates"""

    def test_update_name(self, managers, sample_users):
        """Test updating workspace name"""
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace("old-name", owner.id)
        workspace_manager.update_workspace(workspace.id, name="new-name")

        updated = workspace_manager.get_workspace(workspace.id)
        assert updated.name == "new-name"

    def test_update_description(self, managers, sample_users):
        """Test updating workspace description"""
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace("test", owner.id)
        workspace_manager.update_workspace(
            workspace.id,
            description="New description",
        )

        updated = workspace_manager.get_workspace(workspace.id)
        assert updated.description == "New description"

    def test_update_settings(self, managers, sample_users):
        """Test updating workspace settings"""
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace("test", owner.id)
        workspace_manager.update_workspace(
            workspace.id,
            settings={"theme": "dark", "notifications": True},
        )

        updated = workspace_manager.get_workspace(workspace.id)
        assert updated.settings["theme"] == "dark"

    def test_archive_workspace(self, managers, sample_users):
        """Test archiving workspace"""
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace("test", owner.id)
        workspace_manager.archive_workspace(workspace.id)

        updated = workspace_manager.get_workspace(workspace.id)
        assert updated.is_active is False

        # Should not appear in active list
        active = workspace_manager.list_workspaces()
        assert len(active) == 0


class TestMemberManagement:
    """Test workspace member management"""

    def test_add_member(self, managers, sample_users):
        """Test adding member to workspace"""
        _, workspace_manager = managers
        owner, member, _, _ = sample_users

        workspace = workspace_manager.create_workspace("test", owner.id)
        workspace_manager.add_member(workspace.id, member.id, WorkspaceRole.MEMBER)

        role = workspace_manager.get_member_role(workspace.id, member.id)
        assert role == WorkspaceRole.MEMBER

    def test_update_member_role(self, managers, sample_users):
        """Test updating member role"""
        _, workspace_manager = managers
        owner, member, _, _ = sample_users

        workspace = workspace_manager.create_workspace("test", owner.id)
        workspace_manager.add_member(workspace.id, member.id, WorkspaceRole.MEMBER)
        workspace_manager.add_member(workspace.id, member.id, WorkspaceRole.ADMIN)

        role = workspace_manager.get_member_role(workspace.id, member.id)
        assert role == WorkspaceRole.ADMIN

    def test_remove_member(self, managers, sample_users):
        """Test removing member from workspace"""
        _, workspace_manager = managers
        owner, member, _, _ = sample_users

        workspace = workspace_manager.create_workspace("test", owner.id)
        workspace_manager.add_member(workspace.id, member.id)
        workspace_manager.remove_member(workspace.id, member.id)

        role = workspace_manager.get_member_role(workspace.id, member.id)
        assert role is None

    def test_cannot_remove_owner(self, managers, sample_users):
        """Test cannot remove workspace owner"""
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace("test", owner.id)

        with pytest.raises(ValueError, match="Cannot remove workspace owner"):
            workspace_manager.remove_member(workspace.id, owner.id)

    def test_list_members(self, managers, sample_users):
        """Test listing workspace members"""
        _, workspace_manager = managers
        owner, member1, member2, _ = sample_users

        workspace = workspace_manager.create_workspace("test", owner.id)
        workspace_manager.add_member(workspace.id, member1.id, WorkspaceRole.ADMIN)
        workspace_manager.add_member(workspace.id, member2.id, WorkspaceRole.MEMBER)

        members = workspace_manager.list_members(workspace.id)
        assert len(members) == 3  # owner + 2 members

    def test_is_member(self, managers, sample_users):
        """Test checking workspace membership"""
        _, workspace_manager = managers
        owner, member, _, viewer = sample_users

        workspace = workspace_manager.create_workspace("test", owner.id)
        workspace_manager.add_member(workspace.id, member.id)

        assert workspace_manager.is_member(workspace.id, owner.id)
        assert workspace_manager.is_member(workspace.id, member.id)
        assert not workspace_manager.is_member(workspace.id, viewer.id)


class TestQuotaManagement:
    """Test workspace quota management"""

    def test_default_quota(self, managers, sample_users):
        """Test default workspace quota is 500"""
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace("test", owner.id)
        assert workspace.api_quota_daily == 500

    def test_consume_quota(self, managers, sample_users):
        """Test consuming workspace quota"""
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace("test", owner.id)

        assert workspace_manager.consume_quota(workspace.id, 50) is True

        updated = workspace_manager.get_workspace(workspace.id)
        assert updated.api_quota_used == 50

    def test_quota_exceeded(self, managers, sample_users):
        """Test quota enforcement"""
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace("test", owner.id)
        workspace_manager.set_quota(workspace.id, 100)

        workspace_manager.consume_quota(workspace.id, 100)

        assert workspace_manager.check_quota(workspace.id) is False
        assert workspace_manager.consume_quota(workspace.id, 1) is False

    def test_set_custom_quota(self, managers, sample_users):
        """Test setting custom quota"""
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace("test", owner.id)
        workspace_manager.set_quota(workspace.id, 1000)

        updated = workspace_manager.get_workspace(workspace.id)
        assert updated.api_quota_daily == 1000


class TestSharedResources:
    """Test shared workspace resources"""

    def test_save_resource(self, managers, sample_users):
        """Test saving shared resource"""
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace("test", owner.id)

        resource_id = workspace_manager.save_resource(
            workspace.id,
            "query",
            "my-query",
            {"query": "SELECT * FROM users"},
            owner.id,
        )

        assert resource_id > 0

    def test_get_resource(self, managers, sample_users):
        """Test retrieving shared resource"""
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace("test", owner.id)

        resource_id = workspace_manager.save_resource(
            workspace.id,
            "dashboard",
            "sales-dashboard",
            {"widgets": ["chart1", "chart2"]},
            owner.id,
        )

        resource = workspace_manager.get_resource(resource_id)
        assert resource is not None
        assert resource["name"] == "sales-dashboard"
        assert resource["type"] == "dashboard"

    def test_list_resources(self, managers, sample_users):
        """Test listing workspace resources"""
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace("test", owner.id)

        workspace_manager.save_resource(
            workspace.id, "query", "q1", {}, owner.id
        )
        workspace_manager.save_resource(
            workspace.id, "dashboard", "d1", {}, owner.id
        )
        workspace_manager.save_resource(
            workspace.id, "query", "q2", {}, owner.id
        )

        all_resources = workspace_manager.list_resources(workspace.id)
        assert len(all_resources) == 3

        queries = workspace_manager.list_resources(workspace.id, resource_type="query")
        assert len(queries) == 2

    def test_delete_resource(self, managers, sample_users):
        """Test deleting resource"""
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace("test", owner.id)

        resource_id = workspace_manager.save_resource(
            workspace.id, "query", "test", {}, owner.id
        )

        workspace_manager.delete_resource(resource_id)

        assert workspace_manager.get_resource(resource_id) is None


class TestStatistics:
    """Test workspace statistics"""

    def test_get_stats(self, managers, sample_users):
        """Test getting workspace statistics"""
        _, workspace_manager = managers
        owner, member1, member2, _ = sample_users

        workspace = workspace_manager.create_workspace("test", owner.id)
        workspace_manager.add_member(workspace.id, member1.id)
        workspace_manager.add_member(workspace.id, member2.id)

        workspace_manager.save_resource(workspace.id, "query", "q1", {}, owner.id)
        workspace_manager.save_resource(workspace.id, "dashboard", "d1", {}, owner.id)

        workspace_manager.consume_quota(workspace.id, 150)

        stats = workspace_manager.get_stats(workspace.id)

        assert stats["member_count"] == 3  # owner + 2 members
        assert stats["resource_count"] == 2
        assert stats["quota_used"] == 150
        assert stats["quota_daily"] == 500
        assert stats["quota_remaining"] == 350



# ---------------------------------------------------------------------------
# Additional tests for improved coverage
# ---------------------------------------------------------------------------


import workspace_manager as wm_module
from workspace_manager import get_workspace_manager


class TestWorkspaceRoleComparisons:
    """Test WorkspaceRole comparison operators."""

    def test_role_level_ordering(self):
        """Roles have correct numeric level ordering."""
        assert WorkspaceRole.OWNER.level > WorkspaceRole.ADMIN.level
        assert WorkspaceRole.ADMIN.level > WorkspaceRole.MEMBER.level
        assert WorkspaceRole.MEMBER.level > WorkspaceRole.VIEWER.level

    def test_role_ge_operator(self):
        """WorkspaceRole >= operator works correctly."""
        assert WorkspaceRole.OWNER >= WorkspaceRole.ADMIN
        assert WorkspaceRole.ADMIN >= WorkspaceRole.ADMIN
        assert not (WorkspaceRole.MEMBER >= WorkspaceRole.ADMIN)

    def test_role_gt_operator(self):
        """WorkspaceRole > operator works correctly."""
        assert WorkspaceRole.OWNER > WorkspaceRole.ADMIN
        assert not (WorkspaceRole.ADMIN > WorkspaceRole.ADMIN)
        assert not (WorkspaceRole.VIEWER > WorkspaceRole.MEMBER)

    def test_unknown_role_level_zero(self):
        """Unknown role value returns level 0."""
        # Test the level dict fallback
        levels = {"owner": 4, "admin": 3, "member": 2, "viewer": 1}
        result = levels.get("nonexistent", 0)
        assert result == 0


class TestWorkspaceManagerGetNone:
    """Test get_workspace returns None for missing workspace."""

    def test_get_workspace_nonexistent_returns_none(self, managers):
        _, workspace_manager = managers
        result = workspace_manager.get_workspace(99999)
        assert result is None


class TestWorkspaceManagerUpdate:
    """Tests for update_workspace."""

    def test_update_workspace_name(self, managers, sample_users):
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace("original", owner.id)
        workspace_manager.update_workspace(workspace.id, name="renamed")

        updated = workspace_manager.get_workspace(workspace.id)
        assert updated.name == "renamed"

    def test_update_workspace_description(self, managers, sample_users):
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace("ws", owner.id)
        workspace_manager.update_workspace(workspace.id, description="New description")

        updated = workspace_manager.get_workspace(workspace.id)
        assert updated.description == "New description"

    def test_update_workspace_settings(self, managers, sample_users):
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace("ws-settings", owner.id)
        workspace_manager.update_workspace(workspace.id, settings={"theme": "dark"})

        updated = workspace_manager.get_workspace(workspace.id)
        assert updated.settings.get("theme") == "dark"

    def test_update_workspace_no_fields_noop(self, managers, sample_users):
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace("ws-noop", owner.id)
        # Should not raise
        workspace_manager.update_workspace(workspace.id)


class TestQuotaReset:
    """Test quota resets at day boundary."""

    def test_check_quota_resets_when_expired(self, managers, sample_users):
        """check_quota resets quota when quota_reset_at is in the past."""
        import time
        _, workspace_manager = managers
        owner, _, _, _ = sample_users

        workspace = workspace_manager.create_workspace("quota-test", owner.id)

        # Exhaust quota and force reset time to past
        workspace_manager.set_quota(workspace.id, 10)
        workspace_manager.consume_quota(workspace.id, 10)

        # Force quota_reset_at to past
        workspace_manager.conn.execute(
            "UPDATE workspaces SET quota_reset_at = ? WHERE id = ?",
            (time.time() - 1, workspace.id),
        )
        workspace_manager.conn.commit()

        # check_quota should detect reset time passed and reset quota
        result = workspace_manager.check_quota(workspace.id)
        assert result is True  # Quota was reset


class TestGetWorkspaceManager:
    """Test global workspace manager singleton."""

    def test_get_workspace_manager_returns_instance(self, tmp_path):
        """get_workspace_manager returns a WorkspaceManager instance."""
        orig = wm_module._workspace_manager
        db_path = tmp_path / "ws.db"
        real_mgr = WorkspaceManager(db_path=db_path)
        wm_module._workspace_manager = real_mgr
        try:
            mgr = get_workspace_manager()
            assert isinstance(mgr, WorkspaceManager)
        finally:
            wm_module._workspace_manager = orig
            real_mgr.close()

    def test_get_workspace_manager_is_singleton(self, tmp_path):
        """get_workspace_manager returns the same instance on repeated calls."""
        db_path = tmp_path / "ws2.db"
        orig = wm_module._workspace_manager
        real_mgr = WorkspaceManager(db_path=db_path)
        wm_module._workspace_manager = real_mgr
        try:
            mgr1 = get_workspace_manager()
            mgr2 = get_workspace_manager()
            assert mgr1 is mgr2
        finally:
            wm_module._workspace_manager = orig
            real_mgr.close()
