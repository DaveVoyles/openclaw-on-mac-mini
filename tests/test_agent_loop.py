"""Tests for agent_loop — Plan CRUD, serialization, dependency tracking."""
import pytest

import agent_loop
from agent_loop import Plan, Step, plan_to_markdown, plan_from_markdown, save_plan, load_plan


@pytest.fixture(autouse=True)
def _isolate_plans(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_loop, "PLANS_DIR", tmp_path)


@pytest.fixture
def sample_plan():
    plan = Plan(plan_id="test-001", goal="Test roundtrip", steps=[
        Step(num=1, description="First step", status="done", output="Result A"),
        Step(num=2, description="Second step", depends_on=[1]),
        Step(num=3, description="Third step", depends_on=[1, 2]),
    ])
    plan.context = {"search_results": "found 3 items"}
    return plan


class TestPlanRoundtrip:
    def test_preserves_goal(self, sample_plan):
        md = plan_to_markdown(sample_plan)
        plan2 = plan_from_markdown(md, plan_id="test-001")
        assert plan2.goal == "Test roundtrip"

    def test_preserves_steps(self, sample_plan):
        md = plan_to_markdown(sample_plan)
        plan2 = plan_from_markdown(md, plan_id="test-001")
        assert len(plan2.steps) == 3
        assert plan2.steps[0].status == "done"
        assert plan2.steps[0].output == "Result A"

    def test_preserves_dependencies(self, sample_plan):
        md = plan_to_markdown(sample_plan)
        plan2 = plan_from_markdown(md, plan_id="test-001")
        assert plan2.steps[1].depends_on == [1]
        assert plan2.steps[2].depends_on == [1, 2]

    def test_preserves_context(self, sample_plan):
        md = plan_to_markdown(sample_plan)
        plan2 = plan_from_markdown(md, plan_id="test-001")
        assert plan2.context.get("search_results") == "found 3 items"


class TestPlanSaveLoad:
    def test_save_and_load(self, sample_plan):
        save_plan(sample_plan)
        loaded = load_plan("test-001")
        assert loaded is not None
        assert loaded.goal == "Test roundtrip"
        assert loaded.steps[0].output == "Result A"


class TestPlanDependencyTracking:
    def test_next_incomplete_step(self, sample_plan):
        nxt = sample_plan.next_incomplete_step()
        assert nxt is not None and nxt.num == 2

    def test_progress_str(self, sample_plan):
        assert sample_plan.progress_str() == "1/3"

    def test_independent_pending_steps(self, sample_plan):
        indep = sample_plan.independent_pending_steps()
        assert len(indep) == 1 and indep[0].num == 2

    def test_step3_unlocks_after_step2(self, sample_plan):
        sample_plan.steps[1].status = "done"
        nxt = sample_plan.next_incomplete_step()
        assert nxt is not None and nxt.num == 3
