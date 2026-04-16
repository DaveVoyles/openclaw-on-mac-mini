"""Unit tests for approval_ui.py — build_approval_embed and ApprovalView."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")

import discord

from approval_models import APPROVAL_TTL, ApprovalRequest, RiskLevel
from approval_ui import RISK_COLORS, RISK_EMOJI, ApprovalView, build_approval_embed


def _make_req(**overrides):
    defaults = dict(
        request_id="ab12cd34",
        action="restart_container",
        target="radarr",
        risk_level=RiskLevel.HIGH,
        requester_id=1,
        requester_name="Alice",
        channel_id=42,
    )
    defaults.update(overrides)
    return ApprovalRequest(**defaults)


class TestRiskColorAndEmoji:
    def test_all_risk_levels_have_color(self):
        for level in RiskLevel:
            assert level in RISK_COLORS

    def test_all_risk_levels_have_emoji(self):
        for level in RiskLevel:
            assert level in RISK_EMOJI

    def test_critical_is_red(self):
        assert RISK_COLORS[RiskLevel.CRITICAL] == discord.Color.red()

    def test_low_is_green(self):
        assert RISK_COLORS[RiskLevel.LOW] == discord.Color.green()


class TestBuildApprovalEmbed:
    def test_returns_embed_instance(self):
        req = _make_req()
        embed = build_approval_embed(req)
        assert isinstance(embed, discord.Embed)

    def test_embed_title_contains_action(self):
        req = _make_req(action="deploy_service")
        embed = build_approval_embed(req)
        assert "deploy_service" in embed.title

    def test_embed_description_contains_target(self):
        req = _make_req(target="lidarr")
        embed = build_approval_embed(req)
        assert "lidarr" in embed.description

    def test_embed_description_contains_requester_name(self):
        req = _make_req(requester_name="Charlie")
        embed = build_approval_embed(req)
        assert "Charlie" in embed.description

    def test_embed_description_contains_request_id(self):
        req = _make_req(request_id="deadbeef")
        embed = build_approval_embed(req)
        assert "deadbeef" in embed.description

    def test_embed_description_contains_risk_level(self):
        req = _make_req(risk_level=RiskLevel.CRITICAL)
        embed = build_approval_embed(req)
        assert "CRITICAL" in embed.description

    def test_embed_detail_field_added_when_present(self):
        req = _make_req(detail="some dry-run output")
        embed = build_approval_embed(req)
        field_names = [f.name for f in embed.fields]
        assert "Details" in field_names

    def test_embed_no_detail_field_when_empty(self):
        req = _make_req(detail="")
        embed = build_approval_embed(req)
        field_names = [f.name for f in embed.fields]
        assert "Details" not in field_names

    def test_embed_footer_contains_expiry_info(self):
        req = _make_req()
        embed = build_approval_embed(req)
        assert embed.footer.text is not None
        assert "minute" in embed.footer.text.lower() or "expire" in embed.footer.text.lower()

    def test_embed_high_risk_uses_orange_color(self):
        req = _make_req(risk_level=RiskLevel.HIGH)
        embed = build_approval_embed(req)
        assert embed.color == discord.Color.orange()

    def test_embed_medium_risk_uses_gold_color(self):
        req = _make_req(risk_level=RiskLevel.MEDIUM)
        embed = build_approval_embed(req)
        assert embed.color == discord.Color.gold()


class TestApprovalViewInit:
    def test_init_stores_request_id(self):
        callback = AsyncMock()
        view = ApprovalView(request_id="test1234", action_callback=callback)
        assert view.request_id == "test1234"

    def test_init_stores_callback(self):
        callback = AsyncMock()
        view = ApprovalView(request_id="test1234", action_callback=callback)
        assert view.action_callback is callback

    def test_view_has_timeout_matching_ttl(self):
        callback = AsyncMock()
        view = ApprovalView(request_id="test1234", action_callback=callback)
        assert view.timeout == APPROVAL_TTL

    def test_view_has_approve_and_deny_buttons(self):
        callback = AsyncMock()
        view = ApprovalView(request_id="abc", action_callback=callback)
        button_labels = [c.label for c in view.children if isinstance(c, discord.ui.Button)]
        assert any("Approve" in label for label in button_labels)
        assert any("Deny" in label for label in button_labels)
