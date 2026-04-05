"""
Tests for RBAC Permissions System
"""

import tempfile
from pathlib import Path

import pytest

import user_manager as user_manager_module
import workspace_manager as workspace_manager_module
from rbac_permissions import DEFAULT_PERMISSIONS, Permission, PermissionManager
from user_manager import UserManager, UserRole
from workspace_manager import WorkspaceManager


@pytest.fixture
def temp_dbs():
    """Create temporary databases for testing"""
    with tempfile.NamedTemporaryFile(suffix="_users.db", delete=False) as f:
        users_db = Path(f.name)
    with tempfile.NamedTemporaryFile(suffix="_workspaces.db", delete=False) as f:
        workspaces_db = Path(f.name)
    with tempfile.NamedTemporaryFile(suffix="_permissions.db", delete=False) as f:
        permissions_db = Path(f.name)

    yield users_db, workspaces_db, permissions_db

    users_db.unlink(missing_ok=True)
    workspaces_db.unlink(missing_ok=True)
    permissions_db.unlink(missing_ok=True)


@pytest.fixture
def managers(temp_dbs):
    """Create manager instances"""
    users_db, workspaces_db, permissions_db = temp_dbs

    user_manager = UserManager(db_path=users_db)
    workspace_manager = WorkspaceManager(db_path=workspaces_db)
    previous_global_user_manager = user_manager_module._user_manager
    previous_global_workspace_manager = workspace_manager_module._workspace_manager
    user_manager_module._user_manager = user_manager
    workspace_manager_module._workspace_manager = workspace_manager
    perm_manager = PermissionManager(db_path=permissions_db)

    yield user_manager, workspace_manager, perm_manager

    user_manager.close()
    workspace_manager.close()
    perm_manager.close()
    user_manager_module._user_manager = previous_global_user_manager
    workspace_manager_module._workspace_manager = previous_global_workspace_manager


@pytest.fixture
def sample_users(managers):
    """Create sample users with different roles"""
    user_manager, _, _ = managers

    admin = user_manager.register_user(1, "admin", UserRole.ADMIN)
    member = user_manager.register_user(2, "member", UserRole.MEMBER)
    viewer = user_manager.register_user(3, "viewer", UserRole.VIEWER)

    return admin, member, viewer


class TestDefaultPermissions:
    """Test default role-based permissions"""

    def test_admin_has_all_permissions(self, managers, sample_users):
        """Test admin role has all permissions"""
        _, _, perm_manager = managers
        admin, _, _ = sample_users

        for permission in Permission:
            assert perm_manager.has_permission(admin, permission)

    def test_member_has_standard_permissions(self, managers, sample_users):
        """Test member role has standard permissions"""
        _, _, perm_manager = managers
        _, member, _ = sample_users

        # Should have these
        assert perm_manager.has_permission(member, Permission.EXECUTE_COMMANDS)
        assert perm_manager.has_permission(member, Permission.MANAGE_SCHEDULES)
        assert perm_manager.has_permission(member, Permission.VIEW_ANALYTICS)

        # Should NOT have these
        assert not perm_manager.has_permission(member, Permission.EXECUTE_ADMIN_COMMANDS)
        assert not perm_manager.has_permission(member, Permission.DELETE_DATA)
        assert not perm_manager.has_permission(member, Permission.MANAGE_ROLES)

    def test_viewer_has_read_only_permissions(self, managers, sample_users):
        """Test viewer role has only read permissions"""
        _, _, perm_manager = managers
        _, _, viewer = sample_users

        # Should have these
        assert perm_manager.has_permission(viewer, Permission.VIEW_SCHEDULES)
        assert perm_manager.has_permission(viewer, Permission.VIEW_ANALYTICS)

        # Should NOT have these
        assert not perm_manager.has_permission(viewer, Permission.EXECUTE_COMMANDS)
        assert not perm_manager.has_permission(viewer, Permission.MANAGE_SCHEDULES)
        assert not perm_manager.has_permission(viewer, Permission.CREATE_RESOURCES)


class TestPermissionOverrides:
    """Test user-specific permission overrides"""

    def test_grant_permission(self, managers, sample_users):
        """Test granting permission to user"""
        _, _, perm_manager = managers
        admin, member, _ = sample_users

        # Member doesn't have this permission by default
        assert not perm_manager.has_permission(member, Permission.DELETE_DATA)

        # Grant permission
        perm_manager.grant_permission(
            member.id,
            Permission.DELETE_DATA,
            granted_by=admin.id,
        )

        # Now should have it
        assert perm_manager.has_permission(member, Permission.DELETE_DATA)

    def test_revoke_permission(self, managers, sample_users):
        """Test revoking permission from user"""
        _, _, perm_manager = managers
        admin, member, _ = sample_users

        # Member has this permission by default
        assert perm_manager.has_permission(member, Permission.EXECUTE_COMMANDS)

        # Revoke permission
        perm_manager.revoke_permission(
            member.id,
            Permission.EXECUTE_COMMANDS,
            revoked_by=admin.id,
        )

        # Now should NOT have it
        assert not perm_manager.has_permission(member, Permission.EXECUTE_COMMANDS)

    def test_list_permissions(self, managers, sample_users):
        """Test listing all permissions for user"""
        _, _, perm_manager = managers
        _, member, _ = sample_users

        permissions = perm_manager.list_permissions(member.id)

        assert isinstance(permissions, dict)
        assert len(permissions) == len(Permission)
        assert permissions[Permission.EXECUTE_COMMANDS.value] is True
        assert permissions[Permission.DELETE_DATA.value] is False


class TestWorkspacePermissions:
    """Test workspace-specific permissions"""

    def test_workspace_permission_override(self, managers, sample_users):
        """Test workspace-specific permission override"""
        user_manager, workspace_manager, perm_manager = managers
        admin, member, _ = sample_users

        # Create workspace
        workspace = workspace_manager.create_workspace("test", admin.id)
        workspace_manager.add_member(workspace.id, member.id)

        # Member doesn't have this permission globally
        assert not perm_manager.has_permission(member, Permission.DELETE_DATA)

        # Grant permission in workspace context
        perm_manager.grant_permission(
            member.id,
            Permission.DELETE_DATA,
            granted_by=admin.id,
            workspace_id=workspace.id,
        )

        # Should have it in workspace context
        assert perm_manager.has_permission(
            member,
            Permission.DELETE_DATA,
            workspace_id=workspace.id,
        )

        # Should NOT have it globally
        assert not perm_manager.has_permission(member, Permission.DELETE_DATA)

    def test_workspace_owner_has_all_permissions(self, managers, sample_users):
        """Test workspace owner has all permissions in their workspace"""
        user_manager, workspace_manager, perm_manager = managers
        admin, member, _ = sample_users

        # Create workspace owned by member
        workspace = workspace_manager.create_workspace("test", member.id)

        # Member doesn't have admin permissions globally
        assert not perm_manager.has_permission(member, Permission.MANAGE_ROLES)

        # But should have all permissions in their workspace
        assert perm_manager.has_permission(
            member,
            Permission.MANAGE_ROLES,
            workspace_id=workspace.id,
        )


class TestAuditLogging:
    """Test permission audit logging"""

    def test_grant_logged(self, managers, sample_users):
        """Test permission grants are logged"""
        _, _, perm_manager = managers
        admin, member, _ = sample_users

        perm_manager.grant_permission(
            member.id,
            Permission.MANAGE_SCHEDULES,
            granted_by=admin.id,
        )

        log = perm_manager.get_audit_log(user_id=admin.id)

        assert len(log) > 0
        assert log[0]["action"] == "grant_permission"
        assert log[0]["permission"] == Permission.MANAGE_SCHEDULES.value
        assert log[0]["target_user_id"] == member.id

    def test_revoke_logged(self, managers, sample_users):
        """Test permission revocations are logged"""
        _, _, perm_manager = managers
        admin, member, _ = sample_users

        perm_manager.revoke_permission(
            member.id,
            Permission.EXECUTE_COMMANDS,
            revoked_by=admin.id,
        )

        log = perm_manager.get_audit_log(user_id=admin.id)

        assert len(log) > 0
        assert log[0]["action"] == "revoke_permission"

    def test_get_audit_log_filtered(self, managers, sample_users):
        """Test getting filtered audit log"""
        _, _, perm_manager = managers
        admin, member, viewer = sample_users

        # Generate some audit events
        perm_manager.grant_permission(member.id, Permission.DELETE_DATA, admin.id)
        perm_manager.grant_permission(viewer.id, Permission.EXECUTE_COMMANDS, admin.id)

        # Get admin's log
        admin_log = perm_manager.get_audit_log(user_id=admin.id)
        assert len(admin_log) >= 2

        # All entries should be by admin
        for entry in admin_log:
            assert entry["user_id"] == admin.id

    def test_get_all_audit_log(self, managers, sample_users):
        """Test getting complete audit log"""
        _, _, perm_manager = managers
        admin, member, _ = sample_users

        perm_manager.grant_permission(member.id, Permission.DELETE_DATA, admin.id)
        perm_manager.revoke_permission(member.id, Permission.EXECUTE_COMMANDS, admin.id)

        log = perm_manager.get_audit_log()
        assert len(log) >= 2


class TestPermissionEnums:
    """Test permission enum definitions"""

    def test_all_permissions_defined(self):
        """Test all expected permissions are defined"""
        expected = {
            "execute_commands",
            "execute_admin_commands",
            "manage_schedules",
            "view_schedules",
            "view_analytics",
            "export_data",
            "invite_members",
            "remove_members",
            "manage_roles",
            "modify_settings",
            "manage_integrations",
            "create_resources",
            "delete_resources",
            "share_resources",
            "access_sensitive_data",
            "delete_data",
        }

        actual = {p.value for p in Permission}
        assert actual == expected

    def test_default_permissions_complete(self):
        """Test default permissions defined for all roles"""
        for role in UserRole:
            assert role in DEFAULT_PERMISSIONS
            assert isinstance(DEFAULT_PERMISSIONS[role], set)


class TestPermissionDecorators:
    """Test permission decorator behavior (unit tests without Discord)"""

    def test_require_permission_validates_user(self, managers, sample_users):
        """Test permission decorator checks are valid"""
        user_manager, _, perm_manager = managers
        _, member, _ = sample_users

        # Manually test the permission check logic
        has_perm = perm_manager.has_permission(member, Permission.MANAGE_SCHEDULES)
        assert has_perm is True

        has_admin_perm = perm_manager.has_permission(
            member,
            Permission.EXECUTE_ADMIN_COMMANDS,
        )
        assert has_admin_perm is False
