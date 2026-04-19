"""Tests for agent_loop — Plan CRUD, serialization, dependency tracking."""
import pytest

import agent_loop
from agent_loop import Plan, Step, load_plan, plan_from_markdown, plan_to_markdown, save_plan


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


# ---------------------------------------------------------------------------
# Async skill functions — create / read / update / adjust / cancel / resume
# ---------------------------------------------------------------------------

class TestCreatePlan:
    @pytest.mark.asyncio
    async def test_creates_plan_with_steps(self):
        result = await agent_loop.create_plan("Build a rocket", "Step 1\nStep 2\nStep 3")
        assert "✅" in result
        assert "3 steps" in result

    @pytest.mark.asyncio
    async def test_creates_plan_single_step_from_goal(self):
        result = await agent_loop.create_plan("Deploy the service")
        assert "✅" in result
        assert "1 steps" in result

    @pytest.mark.asyncio
    async def test_blocks_when_too_many_active(self, monkeypatch):
        # Fill up MAX_ACTIVE_PLANS with in-progress plans
        for i in range(agent_loop.MAX_ACTIVE_PLANS):
            p = agent_loop.Plan(plan_id=f"filler-{i:03d}", goal=f"Filler {i}", steps=[
                agent_loop.Step(num=1, description="Work")
            ])
            p.status = "in-progress"
            agent_loop.save_plan(p)
        result = await agent_loop.create_plan("One more")
        assert "Too many active plans" in result


class TestReadPlan:
    @pytest.mark.asyncio
    async def test_agent_loop_returns_error_for_missing_plan(self):
        result = await agent_loop.read_plan("nonexistent-001")
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_returns_plan_contents(self, sample_plan):
        agent_loop.save_plan(sample_plan)
        result = await agent_loop.read_plan("test-001")
        assert "Test roundtrip" in result
        assert "Step 1" in result


class TestUpdatePlanStep:
    @pytest.mark.asyncio
    async def test_updates_step_status(self, sample_plan):
        agent_loop.save_plan(sample_plan)
        result = await agent_loop.update_plan_step("test-001", 2, "done", "Finished ok")
        assert "✅" in result
        plan = agent_loop.load_plan("test-001")
        assert plan.steps[1].status == "done"
        assert plan.steps[1].output == "Finished ok"

    @pytest.mark.asyncio
    async def test_invalid_status_rejected(self, sample_plan):
        agent_loop.save_plan(sample_plan)
        result = await agent_loop.update_plan_step("test-001", 1, "bogus")
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_agent_loop_missing_plan_returns_error(self):
        result = await agent_loop.update_plan_step("ghost-001", 1, "done")
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_missing_step_returns_error(self, sample_plan):
        agent_loop.save_plan(sample_plan)
        result = await agent_loop.update_plan_step("test-001", 99, "done")
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_auto_completes_plan_when_all_done(self, sample_plan):
        # Mark steps 1 and 3 as already done, update step 2 → done
        sample_plan.steps[2].status = "done"
        agent_loop.save_plan(sample_plan)
        await agent_loop.update_plan_step("test-001", 2, "done")
        plan = agent_loop.load_plan("test-001")
        assert plan.status == "completed"


class TestAdjustPlan:
    @pytest.mark.asyncio
    async def test_add_step(self, sample_plan):
        agent_loop.save_plan(sample_plan)
        result = await agent_loop.adjust_plan("test-001", "add_step", "New step")
        assert "✅" in result
        plan = agent_loop.load_plan("test-001")
        assert len(plan.steps) == 4

    @pytest.mark.asyncio
    async def test_remove_step(self, sample_plan):
        agent_loop.save_plan(sample_plan)
        result = await agent_loop.adjust_plan("test-001", "remove_step", position=2)
        assert "✅" in result
        plan = agent_loop.load_plan("test-001")
        assert all(s.num != 2 for s in plan.steps)

    @pytest.mark.asyncio
    async def test_insert_after(self, sample_plan):
        agent_loop.save_plan(sample_plan)
        result = await agent_loop.adjust_plan("test-001", "insert_after", "Inserted", position=1)
        assert "✅" in result
        plan = agent_loop.load_plan("test-001")
        assert any(s.description == "Inserted" and s.num == 2 for s in plan.steps)

    @pytest.mark.asyncio
    async def test_unknown_action(self, sample_plan):
        agent_loop.save_plan(sample_plan)
        result = await agent_loop.adjust_plan("test-001", "fly_away")
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_agent_loop_missing_plan_returns_error_v2(self):
        result = await agent_loop.adjust_plan("ghost-001", "add_step", "X")
        assert "❌" in result


class TestCancelPlan:
    @pytest.mark.asyncio
    async def test_cancels_active_plan(self, sample_plan):
        sample_plan.status = "in-progress"
        agent_loop.save_plan(sample_plan)
        result = await agent_loop.cancel_plan("test-001")
        assert "interrupted" in result
        plan = agent_loop.load_plan("test-001")
        assert plan.status == "interrupted"

    @pytest.mark.asyncio
    async def test_resets_in_progress_steps(self, sample_plan):
        sample_plan.status = "in-progress"
        sample_plan.steps[1].status = "in-progress"
        agent_loop.save_plan(sample_plan)
        await agent_loop.cancel_plan("test-001")
        plan = agent_loop.load_plan("test-001")
        assert plan.steps[1].status == "pending"

    @pytest.mark.asyncio
    async def test_agent_loop_missing_plan_returns_error_v3(self):
        result = await agent_loop.cancel_plan("ghost-001")
        assert "❌" in result


class TestResumePlan:
    @pytest.mark.asyncio
    async def test_resumes_interrupted_plan(self, sample_plan):
        sample_plan.status = "interrupted"
        agent_loop.save_plan(sample_plan)
        result = await agent_loop.resume_plan("test-001")
        assert "Resumed" in result or "��" in result

    @pytest.mark.asyncio
    async def test_marks_completed_when_no_steps_remaining(self, sample_plan):
        sample_plan.status = "interrupted"
        for s in sample_plan.steps:
            s.status = "done"
        agent_loop.save_plan(sample_plan)
        result = await agent_loop.resume_plan("test-001")
        assert "completed" in result
        plan = agent_loop.load_plan("test-001")
        assert plan.status == "completed"

    @pytest.mark.asyncio
    async def test_refuses_non_interrupted_plan(self, sample_plan):
        sample_plan.status = "completed"
        agent_loop.save_plan(sample_plan)
        result = await agent_loop.resume_plan("test-001")
        assert "⚠️" in result

    @pytest.mark.asyncio
    async def test_agent_loop_missing_plan_returns_error_v4(self):
        result = await agent_loop.resume_plan("ghost-001")
        assert "❌" in result


class TestListPlansSkill:
    @pytest.mark.asyncio
    async def test_returns_all_plans(self, sample_plan):
        agent_loop.save_plan(sample_plan)
        result = await agent_loop.list_plans_skill()
        assert "test-001" in result or "Test roundtrip" in result

    @pytest.mark.asyncio
    async def test_filters_by_status(self, sample_plan):
        agent_loop.save_plan(sample_plan)
        result = await agent_loop.list_plans_skill("completed")
        # sample_plan is in-progress, should not appear in completed filter
        assert "test-001" not in result or "No plans" in result


class TestExecutePlan:
    @pytest.mark.asyncio
    async def test_agent_loop_returns_error_for_missing_plan_v2(self):
        result = await agent_loop.execute_plan("ghost-001")
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_refuses_completed_plan(self, sample_plan):
        sample_plan.status = "completed"
        agent_loop.save_plan(sample_plan)
        result = await agent_loop.execute_plan("test-001")
        assert "⚠️" in result

    @pytest.mark.asyncio
    async def test_happy_path_single_step(self):
        """Single-step plan: LLM returns text directly, step marked done, plan completed."""
        from unittest.mock import AsyncMock, patch

        plan = agent_loop.Plan(plan_id="happy-001", goal="Do one thing", steps=[
            agent_loop.Step(num=1, description="Just do it"),
        ])
        plan.status = "in-progress"
        agent_loop.save_plan(plan)

        mock_chat = AsyncMock(return_value=("Task accomplished.", [], "gemini"))
        with patch("llm.chat", mock_chat):
            result = await agent_loop.execute_plan("happy-001")

        assert "happy-001" in result
        loaded = agent_loop.load_plan("happy-001")
        assert loaded.status == "completed"
        assert loaded.steps[0].status == "done"
        assert "Task accomplished." in loaded.steps[0].output

    @pytest.mark.asyncio
    async def test_executes_steps_via_llm(self, sample_plan, monkeypatch):
        """execute_plan() drives each step via llm.chat; mock it."""
        from unittest.mock import AsyncMock, patch

        sample_plan.status = "in-progress"
        # Make step 2 and 3 depend only on step 1 (which is already done)
        sample_plan.steps[1].depends_on = []
        sample_plan.steps[2].depends_on = []
        agent_loop.save_plan(sample_plan)

        mock_chat = AsyncMock(return_value=("Step completed successfully.", [], "gemini"))
        with patch("llm.chat", mock_chat):
            result = await agent_loop.execute_plan("test-001")
        # Should mention steps executed
        assert "test-001" in result

    @pytest.mark.asyncio
    async def test_agent_loop_handles_llm_timeout(self, monkeypatch):
        """Timeout on LLM call marks step as failed; multi-step plan stays interrupted."""
        import asyncio as _asyncio
        from unittest.mock import patch

        # Two steps: step 1 times out → plan is interrupted (step 2 still pending)
        plan = agent_loop.Plan(plan_id="timeout-001", goal="Time me out", steps=[
            agent_loop.Step(num=1, description="This will timeout"),
            agent_loop.Step(num=2, description="Never reached", depends_on=[1]),
        ])
        plan.status = "in-progress"
        agent_loop.save_plan(plan)

        with patch("agent_loop.asyncio.wait_for", side_effect=_asyncio.TimeoutError):
            result = await agent_loop.execute_plan("timeout-001")
        assert "timeout-001" in result
        loaded = agent_loop.load_plan("timeout-001")
        # step 1 failed → step 2 (pending) means not all complete → interrupted
        assert loaded.status == "interrupted"

    @pytest.mark.asyncio
    async def test_on_progress_callback_called(self, monkeypatch):
        """on_progress callback receives step updates."""
        from unittest.mock import AsyncMock, patch

        plan = agent_loop.Plan(plan_id="cb-001", goal="Callback test", steps=[
            agent_loop.Step(num=1, description="Do work"),
        ])
        plan.status = "in-progress"
        agent_loop.save_plan(plan)

        progress_calls = []

        async def _on_progress(step_num, status, text):
            progress_calls.append((step_num, status))

        mock_chat = AsyncMock(return_value=("Done.", [], "gemini"))
        with patch("llm.chat", mock_chat):
            await agent_loop.execute_plan("cb-001", on_progress=_on_progress)
        # at minimum "in-progress" was called for step 1
        assert any(step == 1 for step, _ in progress_calls)

    @pytest.mark.asyncio
    async def test_llm_exception_marks_step_failed_and_interrupts_plan(self):
        """When llm.chat raises a generic exception the step is failed and plan interrupted."""
        from unittest.mock import AsyncMock, patch

        plan = agent_loop.Plan(plan_id="exc-001", goal="Blow up", steps=[
            agent_loop.Step(num=1, description="Step that errors"),
            agent_loop.Step(num=2, description="Never reached", depends_on=[1]),
        ])
        plan.status = "in-progress"
        agent_loop.save_plan(plan)

        mock_chat = AsyncMock(side_effect=RuntimeError("LLM exploded"))
        with patch("llm.chat", mock_chat):
            result = await agent_loop.execute_plan("exc-001")

        assert "exc-001" in result
        loaded = agent_loop.load_plan("exc-001")
        assert loaded.steps[0].status == "failed"
        assert "LLM exploded" in loaded.steps[0].output
        # step 2 is still pending → plan should be interrupted, not completed
        assert loaded.status == "interrupted"

    @pytest.mark.asyncio
    async def test_multi_step_context_accumulates(self):
        """Outputs from completed steps are stored in plan.context for subsequent steps."""
        from unittest.mock import AsyncMock, patch

        plan = agent_loop.Plan(plan_id="ctx-001", goal="Accumulate context", steps=[
            agent_loop.Step(num=1, description="Search for data"),
            agent_loop.Step(num=2, description="Summarise findings"),
        ])
        plan.status = "in-progress"
        agent_loop.save_plan(plan)

        responses = [
            ("Found 42 results.", [], "gemini"),
            ("Summary complete.", [], "gemini"),
        ]
        mock_chat = AsyncMock(side_effect=responses)
        with patch("llm.chat", mock_chat):
            result = await agent_loop.execute_plan("ctx-001")

        loaded = agent_loop.load_plan("ctx-001")
        assert loaded.status == "completed"
        # Step 1 output must be stored in context so step 2 can see it
        assert "step_1_output" in loaded.context
        assert "Found 42 results." in loaded.context["step_1_output"]
        assert "2 steps executed" in result

    @pytest.mark.asyncio
    async def test_max_iterations_guard_via_timeout(self):
        """If the LLM never finishes, asyncio.wait_for raises TimeoutError stopping the loop."""
        import asyncio as _asyncio
        from unittest.mock import patch

        plan = agent_loop.Plan(plan_id="inf-001", goal="Run forever", steps=[
            agent_loop.Step(num=1, description="Infinite step"),
            agent_loop.Step(num=2, description="Unreachable", depends_on=[1]),
            agent_loop.Step(num=3, description="Also unreachable", depends_on=[2]),
        ])
        plan.status = "in-progress"
        agent_loop.save_plan(plan)

        # Simulate an LLM that always times out — the loop must terminate
        with patch("agent_loop.asyncio.wait_for", side_effect=_asyncio.TimeoutError):
            result = await agent_loop.execute_plan("inf-001")

        # Must return (not hang) and report the plan as interrupted
        assert "inf-001" in result
        loaded = agent_loop.load_plan("inf-001")
        assert loaded.status == "interrupted"
        assert loaded.steps[0].status == "failed"


class TestScanInterrupted:
    def test_returns_interrupted_plans(self, sample_plan):
        sample_plan.status = "interrupted"
        agent_loop.save_plan(sample_plan)
        interrupted = agent_loop.scan_interrupted()
        ids = [p.plan_id for p in interrupted]
        assert "test-001" in ids
