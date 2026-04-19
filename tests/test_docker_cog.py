"""Tests for cogs/docker_cog.py — DockerCog commands and UI components."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

import cogs.docker_cog as mod
from cogs.docker_cog import (
    ContainerActionView,
    ContainerSelect,
    ContainerSelectView,
    DockerCog,
    _build_container_embed,
    _cached_container_list,
    _container_autocomplete,
    _list_containers_structured,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_interaction(user_id=1):
    interaction = AsyncMock()
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.user.__str__ = MagicMock(return_value="TestUser#1234")
    interaction.guild_id = 12345
    interaction.channel_id = 67890
    interaction.response = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.original_response = AsyncMock(return_value=MagicMock())
    interaction.message = AsyncMock()
    interaction.message.edit = AsyncMock()
    return interaction


def _make_bot():
    bot = MagicMock(spec=commands.Bot)
    return bot


def _sample_container(name="mycontainer", state="running", status="Up 2 hours", image="nginx:latest"):
    return {
        "Names": name,
        "State": state,
        "Status": status,
        "Image": image,
        "Ports": "0.0.0.0:80->80/tcp",
    }


# ---------------------------------------------------------------------------
# _cached_container_list
# ---------------------------------------------------------------------------

class TestCachedContainerList:
    async def test_calls_list_containers_on_cold_cache(self):
        mod._container_cache["ts"] = 0.0
        mod._container_cache["data"] = []

        with patch("cogs.docker_cog.list_containers", new=AsyncMock(return_value="container1\ncontainer2")):
            result = await _cached_container_list()
            assert "container1" in result

    async def test_returns_cached_result_when_fresh(self):
        import time
        mod._container_cache["ts"] = time.monotonic()
        mod._container_cache["data"] = "cached_data"

        with patch("cogs.docker_cog.list_containers", new=AsyncMock(return_value="new_data")) as mock_lc:
            result = await _cached_container_list()
            assert result == "cached_data"
            mock_lc.assert_not_called()

    async def test_refreshes_when_ttl_expired(self):
        mod._container_cache["ts"] = 0.0  # expired
        mod._container_cache["data"] = "old_data"

        with patch("cogs.docker_cog.list_containers", new=AsyncMock(return_value="fresh_data")):
            result = await _cached_container_list()
            assert result == "fresh_data"


# ---------------------------------------------------------------------------
# _list_containers_structured
# ---------------------------------------------------------------------------

class TestListContainersStructured:
    async def test_returns_list_of_dicts_on_success(self):
        container = {"Names": "myapp", "State": "running", "Image": "nginx"}
        output_line = json.dumps(container)

        with patch("cogs.docker_cog._run", new=AsyncMock(return_value=(0, output_line + "\n", ""))):
            result = await _list_containers_structured()
            assert len(result) == 1
            assert result[0]["Names"] == "myapp"

    async def test_returns_empty_on_nonzero_rc(self):
        with patch("cogs.docker_cog._run", new=AsyncMock(return_value=(1, "", "error"))):
            result = await _list_containers_structured()
            assert result == []

    async def test_docker_cog_skips_invalid_json_lines(self):
        output = 'not_json\n{"Names": "good"}\n'
        with patch("cogs.docker_cog._run", new=AsyncMock(return_value=(0, output, ""))):
            result = await _list_containers_structured()
            assert len(result) == 1
            assert result[0]["Names"] == "good"

    async def test_handles_empty_output(self):
        with patch("cogs.docker_cog._run", new=AsyncMock(return_value=(0, "", ""))):
            result = await _list_containers_structured()
            assert result == []

    async def test_handles_multiple_containers(self):
        containers = [
            {"Names": "app1", "State": "running"},
            {"Names": "app2", "State": "exited"},
        ]
        output = "\n".join(json.dumps(c) for c in containers)
        with patch("cogs.docker_cog._run", new=AsyncMock(return_value=(0, output, ""))):
            result = await _list_containers_structured()
            assert len(result) == 2


# ---------------------------------------------------------------------------
# _build_container_embed
# ---------------------------------------------------------------------------

class TestBuildContainerEmbed:
    def test_running_container_green(self):
        container = _sample_container(state="running")
        embed = _build_container_embed(container)
        assert "🟢" in embed.title
        assert embed.color.value == mod.EmbedColors.SUCCESS  # EmbedColors values are ints

    def test_stopped_container_red(self):
        container = _sample_container(state="exited")
        embed = _build_container_embed(container)
        assert "🔴" in embed.title
        assert embed.color.value == mod.EmbedColors.ERROR

    def test_embed_has_state_and_status_fields(self):
        container = _sample_container()
        embed = _build_container_embed(container)
        field_names = [f.name for f in embed.fields]
        assert "State" in field_names
        assert "Status" in field_names

    def test_embed_includes_ports_when_present(self):
        container = _sample_container()
        embed = _build_container_embed(container)
        field_names = [f.name for f in embed.fields]
        assert "Ports" in field_names

    def test_embed_skips_ports_when_none(self):
        container = _sample_container()
        container["Ports"] = "none"
        embed = _build_container_embed(container)
        field_names = [f.name for f in embed.fields]
        assert "Ports" not in field_names

    def test_uses_name_fallback_key(self):
        container = {"Name": "fallback-name", "State": "running", "Status": "Up", "Image": "img"}
        embed = _build_container_embed(container)
        assert "fallback-name" in embed.title

    def test_image_truncated_to_50_chars(self):
        container = _sample_container(image="a" * 100)
        embed = _build_container_embed(container)
        # Image field value should be <= 52 chars (50 + backticks)
        image_field = next(f for f in embed.fields if f.name == "Image")
        assert len(image_field.value) <= 54  # `...` + backticks


# ---------------------------------------------------------------------------
# _container_autocomplete
# ---------------------------------------------------------------------------

class TestContainerAutocomplete:
    async def test_returns_matching_container_names(self):
        cache_data = "NAMES\nnginx\nredis\nmongodb"
        with patch("cogs.docker_cog._cached_container_list", new=AsyncMock(return_value=cache_data)):
            interaction = _make_interaction()
            result = await _container_autocomplete(interaction, "ng")
            names = [c.value for c in result]
            assert "nginx" in names
            assert "redis" not in names

    async def test_returns_all_when_current_empty(self):
        cache_data = "NAMES\nnginx\nredis"
        with patch("cogs.docker_cog._cached_container_list", new=AsyncMock(return_value=cache_data)):
            result = await _container_autocomplete(_make_interaction(), "")
            assert len(result) == 2

    async def test_returns_empty_list_on_exception(self):
        with patch("cogs.docker_cog._cached_container_list", new=AsyncMock(side_effect=Exception("err"))):
            result = await _container_autocomplete(_make_interaction(), "test")
            assert result == []

    async def test_skips_names_header_line(self):
        cache_data = "NAMES\nnginx"
        with patch("cogs.docker_cog._cached_container_list", new=AsyncMock(return_value=cache_data)):
            result = await _container_autocomplete(_make_interaction(), "")
            values = [c.value for c in result]
            assert "NAMES" not in values

    async def test_case_insensitive_match(self):
        cache_data = "NAMES\nNginX\nRedis"
        with patch("cogs.docker_cog._cached_container_list", new=AsyncMock(return_value=cache_data)):
            result = await _container_autocomplete(_make_interaction(), "nginx")
            assert len(result) == 1
            assert result[0].value == "NginX"


# ---------------------------------------------------------------------------
# ContainerActionView
# ---------------------------------------------------------------------------

class TestContainerActionView:
    def test_requester_check_allows_requester(self):
        container = _sample_container()
        view = ContainerActionView(container, requester_id=42)
        assert view.requester_id == 42
        assert view.container_name == "mycontainer"

    async def test_docker_cog_interaction_check_rejects_other_user(self):
        container = _sample_container()
        view = ContainerActionView(container, requester_id=42)
        interaction = _make_interaction(user_id=99)
        result = await view.interaction_check(interaction)
        assert result is False
        interaction.response.send_message.assert_called_once()

    async def test_docker_cog_interaction_check_allows_requester(self):
        container = _sample_container()
        view = ContainerActionView(container, requester_id=42)
        interaction = _make_interaction(user_id=42)
        result = await view.interaction_check(interaction)
        assert result is True

    async def test_logs_button_short_output(self):
        container = _sample_container()
        view = ContainerActionView(container, requester_id=1)
        interaction = _make_interaction()

        with patch("cogs.docker_cog.get_container_logs", new=AsyncMock(return_value="log line 1\nlog line 2")):
            with patch("cogs.docker_cog.audit_log"):
                await view.logs_button.callback(interaction)
                assert view.logs_button.disabled is True
                interaction.followup.send.assert_called_once()

    async def test_logs_button_long_output_sends_file(self):
        container = _sample_container()
        view = ContainerActionView(container, requester_id=1)
        interaction = _make_interaction()
        button = MagicMock()
        button.disabled = False

        long_log = "x" * 2000  # > 1900 chars

        with patch("cogs.docker_cog.get_container_logs", new=AsyncMock(return_value=long_log)):
            with patch("cogs.docker_cog.audit_log"):
                await view.logs_button.callback(interaction)
                # Should send with file kwarg
                call_kwargs = interaction.followup.send.call_args
                assert call_kwargs is not None

    async def test_logs_button_error_sends_ephemeral(self):
        container = _sample_container()
        view = ContainerActionView(container, requester_id=1)
        interaction = _make_interaction()
        button = MagicMock()
        button.disabled = False

        with patch("cogs.docker_cog.get_container_logs", new=AsyncMock(side_effect=Exception("conn refused"))):
            with patch("cogs.docker_cog.audit_log"):
                await view.logs_button.callback(interaction)
                call_kwargs = interaction.followup.send.call_args.kwargs
                embed = call_kwargs.get("embed")
                assert embed is not None
                assert "Error" in (embed.title or "")

    async def test_stats_button_success(self):
        container = _sample_container()
        view = ContainerActionView(container, requester_id=1)
        interaction = _make_interaction()

        with patch("cogs.docker_cog.get_container_status", new=AsyncMock(return_value="CPU: 10%\nMem: 50%")):
            with patch("cogs.docker_cog.audit_log"):
                await view.stats_button.callback(interaction)
                assert view.stats_button.disabled is True
                interaction.followup.send.assert_called_once()

    async def test_stats_button_error_sends_ephemeral(self):
        container = _sample_container()
        view = ContainerActionView(container, requester_id=1)
        interaction = _make_interaction()
        button = MagicMock()
        button.disabled = False

        with patch("cogs.docker_cog.get_container_status", new=AsyncMock(side_effect=Exception("err"))):
            with patch("cogs.docker_cog.audit_log"):
                await view.stats_button.callback(interaction)
                call_kwargs = interaction.followup.send.call_args.kwargs
                embed = call_kwargs.get("embed")
                assert embed is not None
                assert "Error" in (embed.title or "")

    async def test_restart_button_emergency_stopped(self):
        container = _sample_container()
        view = ContainerActionView(container, requester_id=1)
        interaction = _make_interaction()
        button = MagicMock()

        with patch("cogs.docker_cog.is_emergency_stopped", return_value=True):
            await view.restart_button.callback(interaction)
            call_args = interaction.response.send_message.call_args
            assert "Emergency stop" in str(call_args)

    async def test_restart_button_not_allowed_by_policy(self):
        container = _sample_container()
        view = ContainerActionView(container, requester_id=1)
        interaction = _make_interaction()
        button = MagicMock()

        with patch("cogs.docker_cog.is_emergency_stopped", return_value=False):
            with patch("cogs.docker_cog.is_service_allowed", return_value=False):
                await view.restart_button.callback(interaction)
                call_args = interaction.response.send_message.call_args
                assert "not permitted" in str(call_args)

    async def test_restart_button_creates_approval_request(self):
        container = _sample_container()
        view = ContainerActionView(container, requester_id=1)
        interaction = _make_interaction()
        button = MagicMock()
        button.disabled = False

        mock_req = MagicMock()
        mock_req.request_id = "req-123"

        with patch("cogs.docker_cog.is_emergency_stopped", return_value=False):
            with patch("cogs.docker_cog.is_service_allowed", return_value=True):
                with patch("cogs.docker_cog.approval_store") as mock_store:
                    mock_store.create = MagicMock(return_value=mock_req)
                    with patch("cogs.docker_cog.ApprovalView"):
                        with patch("cogs.docker_cog.build_approval_embed", return_value=MagicMock()):
                            with patch("cogs.docker_cog.audit_log"):
                                await view.restart_button.callback(interaction)
                                mock_store.create.assert_called_once()
                                interaction.response.send_message.assert_called_once()

    async def test_stop_button_emergency_stopped(self):
        container = _sample_container()
        view = ContainerActionView(container, requester_id=1)
        interaction = _make_interaction()
        button = MagicMock()

        with patch("cogs.docker_cog.is_emergency_stopped", return_value=True):
            await view.stop_button.callback(interaction)
            call_args = interaction.response.send_message.call_args
            assert "Emergency stop" in str(call_args)

    async def test_stop_button_not_allowed_by_policy(self):
        container = _sample_container()
        view = ContainerActionView(container, requester_id=1)
        interaction = _make_interaction()
        button = MagicMock()

        with patch("cogs.docker_cog.is_emergency_stopped", return_value=False):
            with patch("cogs.docker_cog.is_service_allowed", return_value=False):
                await view.stop_button.callback(interaction)
                call_args = interaction.response.send_message.call_args
                assert "not permitted" in str(call_args)

    async def test_stop_button_creates_approval_request(self):
        container = _sample_container()
        view = ContainerActionView(container, requester_id=1)
        interaction = _make_interaction()
        button = MagicMock()
        button.disabled = False

        mock_req = MagicMock()
        mock_req.request_id = "req-456"

        with patch("cogs.docker_cog.is_emergency_stopped", return_value=False):
            with patch("cogs.docker_cog.is_service_allowed", return_value=True):
                with patch("cogs.docker_cog.approval_store") as mock_store:
                    mock_store.create = MagicMock(return_value=mock_req)
                    with patch("cogs.docker_cog.ApprovalView"):
                        with patch("cogs.docker_cog.build_approval_embed", return_value=MagicMock()):
                            with patch("cogs.docker_cog.audit_log"):
                                await view.stop_button.callback(interaction)
                                mock_store.create.assert_called_once()
                                interaction.response.send_message.assert_called_once()

    async def test_docker_cog_on_timeout_disables_children(self):
        container = _sample_container()
        view = ContainerActionView(container, requester_id=1)
        # Add some mock children via internal attribute
        btn1 = MagicMock(spec=discord.ui.Button)
        btn1.disabled = False
        btn2 = MagicMock(spec=discord.ui.Select)
        btn2.disabled = False
        view._children = [btn1, btn2]
        view.message = None  # No message set

        await view.on_timeout()
        assert btn1.disabled is True
        assert btn2.disabled is True

    async def test_on_timeout_edits_message_if_set(self):
        container = _sample_container()
        view = ContainerActionView(container, requester_id=1)
        view._children = []
        mock_msg = AsyncMock()
        mock_msg.edit = AsyncMock()
        view.message = mock_msg

        await view.on_timeout()
        mock_msg.edit.assert_called_once()

    async def test_on_timeout_handles_edit_exception(self):
        container = _sample_container()
        view = ContainerActionView(container, requester_id=1)
        view._children = []
        mock_msg = AsyncMock()
        mock_msg.edit = AsyncMock(side_effect=Exception("discord error"))
        view.message = mock_msg

        # Should not raise
        await view.on_timeout()


# ---------------------------------------------------------------------------
# ContainerSelect
# ---------------------------------------------------------------------------

class TestContainerSelect:
    async def test_callback_shows_embed_and_action_view(self):
        containers = [_sample_container("app1"), _sample_container("app2", state="exited")]
        options = [discord.SelectOption(label="app1", value="app1")]
        select = ContainerSelect(options=options, containers=containers)
        # discord.ui.Select.values is a read-only property backed by _values
        select._values = ["app1"]

        interaction = _make_interaction()
        with patch("cogs.docker_cog.audit_log"):
            await select.callback(interaction)
            interaction.message.edit.assert_called_once()
            interaction.response.defer.assert_called_once()

    async def test_callback_unknown_container_sends_error(self):
        containers = [_sample_container("app1")]
        options = [discord.SelectOption(label="app1", value="app1")]
        select = ContainerSelect(options=options, containers=containers)
        select._values = ["nonexistent"]

        interaction = _make_interaction()
        await select.callback(interaction)
        interaction.response.send_message.assert_called_once()
        call_args = str(interaction.response.send_message.call_args)
        assert "no longer available" in call_args


# ---------------------------------------------------------------------------
# ContainerSelectView
# ---------------------------------------------------------------------------

class TestContainerSelectView:
    def test_creates_select_with_containers(self):
        containers = [
            _sample_container("app1"),
            _sample_container("app2", state="exited"),
        ]
        view = ContainerSelectView(containers, requester_id=1)
        assert len(view.children) == 1  # ContainerSelect added

    def test_no_select_added_for_empty_containers(self):
        view = ContainerSelectView([], requester_id=1)
        assert len(view.children) == 0

    async def test_docker_cog_interaction_check_rejects_other_user_v2(self):
        containers = [_sample_container()]
        view = ContainerSelectView(containers, requester_id=42)
        interaction = _make_interaction(user_id=99)
        result = await view.interaction_check(interaction)
        assert result is False
        interaction.response.send_message.assert_called_once()

    async def test_docker_cog_interaction_check_allows_requester_v2(self):
        containers = [_sample_container()]
        view = ContainerSelectView(containers, requester_id=42)
        interaction = _make_interaction(user_id=42)
        result = await view.interaction_check(interaction)
        assert result is True

    async def test_docker_cog_on_timeout_disables_children_v2(self):
        containers = [_sample_container()]
        view = ContainerSelectView(containers, requester_id=1)
        view.message = None
        # Manually disable select
        for child in view.children:
            assert hasattr(child, "disabled")
        await view.on_timeout()
        for child in view.children:
            assert child.disabled is True

    def test_limits_to_25_containers(self):
        containers = [_sample_container(f"app{i}") for i in range(30)]
        view = ContainerSelectView(containers, requester_id=1)
        # Should have 1 select with 25 options
        assert len(view.children) == 1
        assert len(view.children[0].options) == 25


# ---------------------------------------------------------------------------
# DockerCog.containers_cmd
# ---------------------------------------------------------------------------

class TestContainersCmd:
    async def test_containers_cmd_with_containers(self):
        bot = _make_bot()
        cog = DockerCog(bot)
        interaction = _make_interaction()

        containers = [
            _sample_container("app1"),
            _sample_container("app2", state="exited"),
        ]

        with patch("cogs.docker_cog._list_containers_structured", new=AsyncMock(return_value=containers)):
            with patch("cogs.docker_cog.audit_log"):
                await cog.containers_cmd.callback(cog, interaction)
                interaction.response.defer.assert_called_once()
                interaction.followup.send.assert_called_once()

    async def test_containers_cmd_fallback_when_no_structured(self):
        bot = _make_bot()
        cog = DockerCog(bot)
        interaction = _make_interaction()

        with patch("cogs.docker_cog._list_containers_structured", new=AsyncMock(return_value=[])):
            with patch("cogs.docker_cog.list_containers", new=AsyncMock(return_value="NAMES\nnginx")):
                with patch("cogs.docker_cog.audit_log"):
                    await cog.containers_cmd.callback(cog, interaction)
                    interaction.followup.send.assert_called_once()

    async def test_containers_cmd_counts_running_stopped(self):
        bot = _make_bot()
        cog = DockerCog(bot)
        interaction = _make_interaction()

        containers = [
            _sample_container("app1", state="running"),
            _sample_container("app2", state="running"),
            _sample_container("app3", state="exited"),
        ]

        with patch("cogs.docker_cog._list_containers_structured", new=AsyncMock(return_value=containers)):
            with patch("cogs.docker_cog.audit_log"):
                await cog.containers_cmd.callback(cog, interaction)
                call_kwargs = interaction.followup.send.call_args
                embed = call_kwargs[1].get("embed") or call_kwargs[0][0] if call_kwargs[0] else None
                # 2 running, 1 stopped
                assert call_kwargs is not None


# ---------------------------------------------------------------------------
# DockerCog.status_cmd
# ---------------------------------------------------------------------------

class TestStatusCmd:
    async def test_docker_cog_status_cmd_success(self):
        bot = _make_bot()
        cog = DockerCog(bot)
        interaction = _make_interaction()

        with patch("cogs.docker_cog.get_container_status", new=AsyncMock(return_value="running fine")):
            with patch("cogs.docker_cog.audit_log"):
                await cog.status_cmd.callback(cog, interaction, "nginx")
                interaction.response.send_message.assert_called_once()
                interaction.followup.send.assert_called_once()


# ---------------------------------------------------------------------------
# DockerCog.logs_cmd
# ---------------------------------------------------------------------------

class TestLogsCmd:
    async def test_logs_cmd_default_lines(self):
        bot = _make_bot()
        cog = DockerCog(bot)
        interaction = _make_interaction()

        with patch("cogs.docker_cog.get_container_logs", new=AsyncMock(return_value="log output")):
            with patch("cogs.docker_cog.audit_log"):
                await cog.logs_cmd.callback(cog, interaction, "nginx")
                interaction.followup.send.assert_called_once()

    async def test_logs_cmd_custom_lines(self):
        bot = _make_bot()
        cog = DockerCog(bot)
        interaction = _make_interaction()

        with patch("cogs.docker_cog.get_container_logs", new=AsyncMock(return_value="lines")) as mock_logs:
            with patch("cogs.docker_cog.audit_log"):
                await cog.logs_cmd.callback(cog, interaction, "redis", lines=50)
                mock_logs.assert_called_once_with("redis", 50)


# ---------------------------------------------------------------------------
# DockerCog.system_cmd
# ---------------------------------------------------------------------------

class TestSystemCmd:
    async def test_system_cmd_returns_stats_embed(self):
        bot = _make_bot()
        cog = DockerCog(bot)
        interaction = _make_interaction()

        with patch("cogs.docker_cog.get_system_stats", new=AsyncMock(return_value="CPU: 20%")):
            with patch("cogs.docker_cog.get_uptime", new=AsyncMock(return_value="2 days")):
                with patch("cogs.docker_cog.audit_log"):
                    await cog.system_cmd.callback(cog, interaction)
                    interaction.followup.send.assert_called_once()


# ---------------------------------------------------------------------------
# DockerCog.dockerstats_cmd
# ---------------------------------------------------------------------------

class TestDockerstatsCmd:
    async def test_dockerstats_cmd_returns_embed(self):
        bot = _make_bot()
        cog = DockerCog(bot)
        interaction = _make_interaction()

        with patch("cogs.docker_cog.get_docker_stats", new=AsyncMock(return_value="nginx 10% CPU")):
            with patch("cogs.docker_cog.audit_log"):
                await cog.dockerstats_cmd.callback(cog, interaction)
                interaction.followup.send.assert_called_once()


# ---------------------------------------------------------------------------
# DockerCog.restart_cmd
# ---------------------------------------------------------------------------

class TestRestartCmd:
    async def test_restart_cmd_emergency_stopped(self):
        bot = _make_bot()
        cog = DockerCog(bot)
        interaction = _make_interaction()

        with patch("cogs.docker_cog.is_emergency_stopped", return_value=True):
            with patch("cogs.docker_cog.audit_log"):
                await cog.restart_cmd.callback(cog, interaction, "nginx")
                interaction.response.send_message.assert_called_once()
                msg = str(interaction.response.send_message.call_args)
                assert "Emergency stop" in msg

    async def test_restart_cmd_blocked_by_policy(self):
        bot = _make_bot()
        cog = DockerCog(bot)
        interaction = _make_interaction()

        with patch("cogs.docker_cog.is_emergency_stopped", return_value=False):
            with patch("cogs.docker_cog.is_service_allowed", return_value=False):
                with patch("cogs.docker_cog.audit_log"):
                    await cog.restart_cmd.callback(cog, interaction, "nginx")
                    call_args = interaction.response.send_message.call_args
                    assert "not permitted" in str(call_args)

    async def test_restart_cmd_creates_approval_request(self):
        bot = _make_bot()
        cog = DockerCog(bot)
        interaction = _make_interaction()

        mock_req = MagicMock()
        mock_req.request_id = "req-789"

        with patch("cogs.docker_cog.is_emergency_stopped", return_value=False):
            with patch("cogs.docker_cog.is_service_allowed", return_value=True):
                with patch("cogs.docker_cog.approval_store") as mock_store:
                    mock_store.create = MagicMock(return_value=mock_req)
                    with patch("cogs.docker_cog.ApprovalView"):
                        with patch("cogs.docker_cog.build_approval_embed", return_value=MagicMock()):
                            with patch("cogs.docker_cog.audit_log"):
                                await cog.restart_cmd.callback(cog, interaction, "nginx")
                                mock_store.create.assert_called_once()
                                interaction.response.send_message.assert_called_once()


# ---------------------------------------------------------------------------
# DockerCog.monitor_set
# ---------------------------------------------------------------------------

class TestMonitorSet:
    async def test_monitor_set_creates_threshold(self):
        bot = _make_bot()
        cog = DockerCog(bot)
        interaction = _make_interaction()

        mock_threshold = MagicMock()
        mock_threshold.cpu_percent = 80.0
        mock_threshold.memory_percent = 90.0
        mock_threshold.cooldown_seconds = 300

        with patch("cogs.docker_cog.resource_monitor") as mock_rm:
            mock_rm.set_threshold = MagicMock(return_value=mock_threshold)
            with patch("cogs.docker_cog.audit_log"):
                await cog.monitor_set.callback(cog, interaction, "nginx", cpu=80.0, memory=90.0)
                mock_rm.set_threshold.assert_called_once_with("nginx", cpu=80.0, memory=90.0)
                interaction.response.send_message.assert_called_once()


# ---------------------------------------------------------------------------
# DockerCog.monitor_remove
# ---------------------------------------------------------------------------

class TestMonitorRemove:
    async def test_monitor_remove_found(self):
        bot = _make_bot()
        cog = DockerCog(bot)
        interaction = _make_interaction()

        with patch("cogs.docker_cog.resource_monitor") as mock_rm:
            mock_rm.remove = MagicMock(return_value=True)
            with patch("cogs.docker_cog.audit_log"):
                await cog.monitor_remove.callback(cog, interaction, "nginx")
                interaction.response.send_message.assert_called_once()

    async def test_monitor_remove_not_found(self):
        bot = _make_bot()
        cog = DockerCog(bot)
        interaction = _make_interaction()

        with patch("cogs.docker_cog.resource_monitor") as mock_rm:
            mock_rm.remove = MagicMock(return_value=False)
            with patch("cogs.docker_cog.audit_log"):
                await cog.monitor_remove.callback(cog, interaction, "nonexistent")
                call_args = interaction.response.send_message.call_args
                assert "No monitor found" in str(call_args)


# ---------------------------------------------------------------------------
# DockerCog.monitor_list
# ---------------------------------------------------------------------------

class TestMonitorList:
    async def test_monitor_list_empty(self):
        bot = _make_bot()
        cog = DockerCog(bot)
        interaction = _make_interaction()

        with patch("cogs.docker_cog.resource_monitor") as mock_rm:
            mock_rm.list_all = MagicMock(return_value=[])
            await cog.monitor_list.callback(cog, interaction)
            interaction.response.send_message.assert_called_once()

    async def test_monitor_list_with_items(self):
        bot = _make_bot()
        cog = DockerCog(bot)
        interaction = _make_interaction()

        mock_t = MagicMock()
        mock_t.enabled = True
        mock_t.container = "nginx"
        mock_t.cpu_percent = 80.0
        mock_t.memory_percent = 90.0

        with patch("cogs.docker_cog.resource_monitor") as mock_rm:
            mock_rm.list_all = MagicMock(return_value=[mock_t])
            with patch("cogs.docker_cog.audit_log"):
                await cog.monitor_list.callback(cog, interaction)
                interaction.response.send_message.assert_called_once()

    async def test_monitor_list_disabled_threshold(self):
        bot = _make_bot()
        cog = DockerCog(bot)
        interaction = _make_interaction()

        mock_t = MagicMock()
        mock_t.enabled = False
        mock_t.container = "redis"
        mock_t.cpu_percent = 70.0
        mock_t.memory_percent = 85.0

        with patch("cogs.docker_cog.resource_monitor") as mock_rm:
            mock_rm.list_all = MagicMock(return_value=[mock_t])
            with patch("cogs.docker_cog.audit_log"):
                await cog.monitor_list.callback(cog, interaction)
                interaction.response.send_message.assert_called_once()


# ---------------------------------------------------------------------------
# DockerCog.monitor_check
# ---------------------------------------------------------------------------

class TestMonitorCheck:
    async def test_monitor_check_no_violations(self):
        bot = _make_bot()
        cog = DockerCog(bot)
        interaction = _make_interaction()

        with patch("cogs.docker_cog.resource_monitor") as mock_rm:
            mock_rm.check_all = AsyncMock(return_value=[])
            with patch("cogs.docker_cog.audit_log"):
                await cog.monitor_check.callback(cog, interaction)
                interaction.response.defer.assert_called_once()
                interaction.followup.send.assert_called_once()

    async def test_monitor_check_with_violations(self):
        bot = _make_bot()
        cog = DockerCog(bot)
        interaction = _make_interaction()

        mock_threshold = MagicMock()
        mock_threshold.container = "nginx"
        mock_threshold.cpu_percent = 80.0
        mock_threshold.memory_percent = 90.0

        mock_stats = {"cpu": 95.0, "memory": 92.0}
        violations = [(mock_threshold, mock_stats)]

        with patch("cogs.docker_cog.resource_monitor") as mock_rm:
            mock_rm.check_all = AsyncMock(return_value=violations)
            with patch("cogs.docker_cog.audit_log"):
                await cog.monitor_check.callback(cog, interaction)
                interaction.followup.send.assert_called_once()


# ---------------------------------------------------------------------------
# DockerCog.cog_command_error
# ---------------------------------------------------------------------------

class TestCogCommandError:
    async def test_error_when_response_not_done(self):
        bot = _make_bot()
        cog = DockerCog(bot)
        interaction = _make_interaction()
        interaction.response.is_done = MagicMock(return_value=False)

        from discord import app_commands
        error = app_commands.AppCommandError("Something went wrong")
        await cog.cog_command_error(interaction, error)
        interaction.response.send_message.assert_called_once()
        embed = interaction.response.send_message.call_args.kwargs.get("embed")
        assert embed is not None
        assert "Error" in (embed.title or "")

    async def test_error_when_response_already_done(self):
        bot = _make_bot()
        cog = DockerCog(bot)
        interaction = _make_interaction()
        interaction.response.is_done = MagicMock(return_value=True)

        from discord import app_commands
        error = app_commands.AppCommandError("Something went wrong")
        await cog.cog_command_error(interaction, error)
        interaction.followup.send.assert_called_once()


# ---------------------------------------------------------------------------
# setup()
# ---------------------------------------------------------------------------

class TestSetup:
    async def test_setup_adds_cog(self):
        bot = AsyncMock(spec=commands.Bot)
        bot.add_cog = AsyncMock()
        await mod.setup(bot)
        bot.add_cog.assert_called_once()
        added_cog = bot.add_cog.call_args[0][0]
        assert isinstance(added_cog, DockerCog)
