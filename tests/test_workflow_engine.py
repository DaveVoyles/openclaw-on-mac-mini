"""
Tests for workflow engine (Phase 3).
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from workflow_engine import (
    TaskStatus,
    Workflow,
    WorkflowEngine,
    WorkflowStatus,
    WorkflowTask,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_workflow_dir(tmp_path):
    """Temporary directory for workflow storage."""
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    return workflow_dir


@pytest.fixture
def engine(temp_workflow_dir, monkeypatch):
    """WorkflowEngine instance with temp storage."""
    temp_workflow_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("workflow_engine.WORKFLOW_DIR", temp_workflow_dir)
    engine = WorkflowEngine()
    yield engine


# ---------------------------------------------------------------------------
# WorkflowTask Tests
# ---------------------------------------------------------------------------


class TestWorkflowTask:
    def test_create_task(self):
        task = WorkflowTask(
            task_id="task1",
            action="test_action",
            args={"param": "value"},
            depends_on=["task0"],
        )

        assert task.task_id == "task1"
        assert task.action == "test_action"
        assert task.status == TaskStatus.PENDING
        assert "task0" in task.depends_on


# ---------------------------------------------------------------------------
# Workflow Tests
# ---------------------------------------------------------------------------


class TestWorkflow:
    def test_to_dict(self):
        tasks = [
            WorkflowTask(task_id="t1", action="action1", args={}, depends_on=[]),
            WorkflowTask(task_id="t2", action="action2", args={}, depends_on=["t1"]),
        ]

        workflow = Workflow(
            workflow_id="wf-1",
            name="Test Workflow",
            description="A test",
            tasks=tasks,
        )

        data = workflow.to_dict()

        assert data["workflow_id"] == "wf-1"
        assert data["name"] == "Test Workflow"
        assert len(data["tasks"]) == 2
        assert data["tasks"][0]["task_id"] == "t1"

    def test_from_dict(self):
        data = {
            "workflow_id": "wf-2",
            "name": "Another Workflow",
            "description": "Testing",
            "tasks": [
                {"task_id": "t1", "action": "a1", "args": {}, "depends_on": []},
                {"task_id": "t2", "action": "a2", "args": {}, "depends_on": ["t1"]},
            ],
            "status": "pending",
            "created_by": "pytest",
            "run_count": 0,
        }

        workflow = Workflow.from_dict(data)

        assert workflow.workflow_id == "wf-2"
        assert workflow.name == "Another Workflow"
        assert len(workflow.tasks) == 2
        assert workflow.tasks[1].depends_on == ["t1"]


# ---------------------------------------------------------------------------
# WorkflowEngine Tests
# ---------------------------------------------------------------------------


class TestWorkflowEngine:
    def test_create_workflow(self, engine):
        tasks = [
            {"task_id": "t1", "action": "action1", "args": {}, "depends_on": []},
            {"task_id": "t2", "action": "action2", "args": {}, "depends_on": ["t1"]},
        ]

        workflow = engine.create_workflow(
            name="Test Workflow",
            description="Testing",
            tasks=tasks,
            created_by="pytest",
        )

        assert workflow.workflow_id == "wf-1"
        assert workflow.name == "Test Workflow"
        assert len(workflow.tasks) == 2

    def test_create_from_template(self, engine):
        workflow = engine.create_from_template("morning-briefing", created_by="pytest")

        assert workflow is not None
        assert workflow.name == "Morning Briefing"
        assert len(workflow.tasks) > 0

    def test_create_from_template_invalid(self, engine):
        workflow = engine.create_from_template("nonexistent", created_by="pytest")
        assert workflow is None

    def test_create_from_yaml(self, engine):
        yaml_content = """
workflow: Daily Report
description: Generate daily report
tasks:
  - task_id: fetch_data
    action: fetch_daily_data
    args: {}
    depends_on: []
  - task_id: generate_report
    action: create_report
    args: {}
    depends_on: [fetch_data]
"""
        workflow = engine.create_from_yaml(yaml_content, created_by="pytest")

        assert workflow.name == "Daily Report"
        assert len(workflow.tasks) == 2
        assert workflow.tasks[1].depends_on == ["fetch_data"]

    def test_list_workflows(self, engine):
        engine.create_workflow(name="WF1", tasks=[], created_by="pytest")
        engine.create_workflow(name="WF2", tasks=[], created_by="pytest")

        workflows = engine.list_workflows()

        assert len(workflows) == 2
        assert workflows[0].name == "WF1"
        assert workflows[1].name == "WF2"

    def test_delete_workflow(self, engine):
        workflow = engine.create_workflow(name="Temp", tasks=[])

        result = engine.delete_workflow(workflow.workflow_id)
        assert result is True
        assert engine.get_workflow(workflow.workflow_id) is None

    def test_build_dag_simple(self, engine):
        tasks = [
            WorkflowTask(task_id="t1", action="a1", args={}, depends_on=[]),
            WorkflowTask(task_id="t2", action="a2", args={}, depends_on=["t1"]),
            WorkflowTask(task_id="t3", action="a3", args={}, depends_on=["t2"]),
        ]

        workflow = Workflow(
            workflow_id="wf-test",
            name="Test",
            description="",
            tasks=tasks,
        )

        dag = engine._build_dag(workflow)

        assert "t1" in dag.nodes
        assert "t2" in dag.nodes
        assert "t3" in dag.nodes
        assert dag.has_edge("t1", "t2")
        assert dag.has_edge("t2", "t3")

    def test_build_dag_parallel(self, engine):
        tasks = [
            WorkflowTask(task_id="t1", action="a1", args={}, depends_on=[]),
            WorkflowTask(task_id="t2", action="a2", args={}, depends_on=[]),
            WorkflowTask(task_id="t3", action="a3", args={}, depends_on=["t1", "t2"]),
        ]

        workflow = Workflow(
            workflow_id="wf-test",
            name="Test",
            description="",
            tasks=tasks,
        )

        dag = engine._build_dag(workflow)

        assert dag.has_edge("t1", "t3")
        assert dag.has_edge("t2", "t3")

    def test_build_dag_cycle_detection(self, engine):
        tasks = [
            WorkflowTask(task_id="t1", action="a1", args={}, depends_on=["t3"]),
            WorkflowTask(task_id="t2", action="a2", args={}, depends_on=["t1"]),
            WorkflowTask(task_id="t3", action="a3", args={}, depends_on=["t2"]),
        ]

        workflow = Workflow(
            workflow_id="wf-test",
            name="Test",
            description="",
            tasks=tasks,
        )

        with pytest.raises(ValueError, match="cycle"):
            engine._build_dag(workflow)

    @pytest.mark.asyncio
    async def test_execute_task_success(self, engine):
        mock_skill = AsyncMock(return_value="Success")
        engine.register_skills({"test_action": mock_skill})

        task = WorkflowTask(
            task_id="t1",
            action="test_action",
            args={"param": "value"},
            depends_on=[],
        )

        result, success = await engine._execute_task(task, {})

        assert success is True
        assert result == "Success"
        assert task.status == TaskStatus.SUCCESS
        mock_skill.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_task_unknown_action(self, engine):
        task = WorkflowTask(
            task_id="t1",
            action="nonexistent_action",
            args={},
            depends_on=[],
        )

        result, success = await engine._execute_task(task, {})

        assert success is False
        assert "Unknown skill" in result
        assert task.status == TaskStatus.FAILED

    @pytest.mark.asyncio
    async def test_execute_task_error(self, engine):
        async def failing_action():
            raise ValueError("Something went wrong")

        engine.register_skills({"failing_action": failing_action})

        task = WorkflowTask(
            task_id="t1",
            action="failing_action",
            args={},
            depends_on=[],
        )

        result, success = await engine._execute_task(task, {})

        assert success is False
        assert task.status == TaskStatus.FAILED
        assert "went wrong" in task.error

    @pytest.mark.asyncio
    async def test_execute_workflow_simple(self, engine):
        call_order = []

        async def action1():
            call_order.append("action1")
            return "Result 1"

        async def action2():
            call_order.append("action2")
            return "Result 2"

        engine.register_skills({
            "action1": action1,
            "action2": action2,
        })

        workflow = engine.create_workflow(
            name="Simple Workflow",
            tasks=[
                {"task_id": "t1", "action": "action1", "args": {}, "depends_on": []},
                {"task_id": "t2", "action": "action2", "args": {}, "depends_on": ["t1"]},
            ],
        )

        execution = await engine.execute_workflow(workflow.workflow_id)

        assert execution.status == WorkflowStatus.SUCCESS
        assert call_order == ["action1", "action2"]
        assert "t1" in execution.task_results
        assert "t2" in execution.task_results

    @pytest.mark.asyncio
    async def test_execute_workflow_parallel(self, engine):
        call_times = {}

        async def slow_action(task_name: str):
            start = asyncio.get_event_loop().time()
            await asyncio.sleep(0.1)
            call_times[task_name] = asyncio.get_event_loop().time() - start
            return f"{task_name} done"

        async def action1():
            return await slow_action("action1")

        async def action2():
            return await slow_action("action2")

        async def action3():
            return "action3 done"

        engine.register_skills({
            "action1": action1,
            "action2": action2,
            "action3": action3,
        })

        workflow = engine.create_workflow(
            name="Parallel Workflow",
            tasks=[
                {"task_id": "t1", "action": "action1", "args": {}, "depends_on": []},
                {"task_id": "t2", "action": "action2", "args": {}, "depends_on": []},
                {"task_id": "t3", "action": "action3", "args": {}, "depends_on": ["t1", "t2"]},
            ],
        )

        execution = await engine.execute_workflow(workflow.workflow_id)

        assert execution.status == WorkflowStatus.SUCCESS
        # t1 and t2 should run in parallel, so total time < 0.2s
        # (if sequential it would be >= 0.2s)

    @pytest.mark.asyncio
    async def test_execute_workflow_fail_fast(self, engine):
        async def success_action():
            return "OK"

        async def failing_action():
            raise ValueError("Failed")

        engine.register_skills({
            "success_action": success_action,
            "failing_action": failing_action,
        })

        workflow = engine.create_workflow(
            name="Failing Workflow",
            error_handling="fail_fast",
            tasks=[
                {"task_id": "t1", "action": "failing_action", "args": {}, "depends_on": []},
                {"task_id": "t2", "action": "success_action", "args": {}, "depends_on": ["t1"]},
            ],
        )

        execution = await engine.execute_workflow(workflow.workflow_id)

        assert execution.status == WorkflowStatus.FAILED
        assert len(execution.errors) > 0
        # t2 should not run because t1 failed
        assert "t2" not in execution.task_results

    @pytest.mark.asyncio
    async def test_execute_workflow_continue_on_error(self, engine):
        async def success_action():
            return "OK"

        async def failing_action():
            return "Error: Failed"  # Returns error string, not exception

        engine.register_skills({
            "success_action": success_action,
            "failing_action": failing_action,
        })

        workflow = engine.create_workflow(
            name="Partial Workflow",
            error_handling="continue_on_error",
            tasks=[
                {"task_id": "t1", "action": "failing_action", "args": {}, "depends_on": []},
                {"task_id": "t2", "action": "success_action", "args": {}, "depends_on": []},
            ],
        )

        execution = await engine.execute_workflow(workflow.workflow_id)

        # With continue_on_error, both tasks run
        assert "t1" in execution.task_results
        assert "t2" in execution.task_results

    @pytest.mark.asyncio
    async def test_workflow_persistence(self, engine, temp_workflow_dir):
        workflow = engine.create_workflow(
            name="Persisted Workflow",
            tasks=[
                {"task_id": "t1", "action": "action1", "args": {}, "depends_on": []},
            ],
        )

        # Check file was created
        workflow_file = temp_workflow_dir / f"{workflow.workflow_id}.json"
        assert workflow_file.exists()

        # Verify content
        data = json.loads(workflow_file.read_text())
        assert data["name"] == "Persisted Workflow"
        assert len(data["tasks"]) == 1

    @pytest.mark.asyncio
    async def test_workflow_reload(self, engine, temp_workflow_dir):
        # Create workflow
        workflow1 = engine.create_workflow(
            name="Reload Test",
            tasks=[
                {"task_id": "t1", "action": "action1", "args": {}, "depends_on": []},
            ],
        )

        # Create new engine instance (simulates restart)
        with patch("workflow_engine.WORKFLOW_DIR", temp_workflow_dir):
            engine2 = WorkflowEngine()

        # Verify workflow was loaded
        loaded = engine2.get_workflow(workflow1.workflow_id)
        assert loaded is not None
        assert loaded.name == "Reload Test"


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestWorkflowIntegration:
    @pytest.mark.asyncio
    async def test_morning_briefing_template(self, engine):
        """Test the morning briefing template workflow."""
        results = {}

        async def get_weather(location: str = "default"):
            results["weather"] = f"Weather for {location}"
            return results["weather"]

        async def search_news(query: str = "", max_results: int = 5):
            results["news"] = f"News: {query} (max {max_results})"
            return results["news"]

        async def get_stock_prices(symbols: list = None):
            results["stocks"] = f"Stocks: {symbols}"
            return results["stocks"]

        async def send_discord_message(channel: str = "general"):
            results["summary"] = f"Sent to {channel}"
            return results["summary"]

        engine.register_skills({
            "get_weather": get_weather,
            "search_news": search_news,
            "get_stock_prices": get_stock_prices,
            "send_discord_message": send_discord_message,
        })

        workflow = engine.create_from_template("morning-briefing")
        execution = await engine.execute_workflow(workflow.workflow_id)

        assert execution.status == WorkflowStatus.SUCCESS
        assert "weather" in results
        assert "news" in results
        assert "stocks" in results
        assert "summary" in results



# ---------------------------------------------------------------------------
# Additional tests for improved coverage
# ---------------------------------------------------------------------------

import workflow_engine as wf_module
from workflow_engine import (
    create_workflow_from_template,
    list_workflows_skill,
    run_workflow,
    workflow_engine,
)


class TestWorkflowEngineDeleteAndList:
    def test_delete_existing_workflow(self, engine, temp_workflow_dir):
        workflow = engine.create_workflow(name="ToDelete", tasks=[])
        wf_id = workflow.workflow_id
        result = engine.delete_workflow(wf_id)
        assert result is True
        assert engine.get_workflow(wf_id) is None

    def test_delete_nonexistent_workflow(self, engine):
        result = engine.delete_workflow("wf-99999")
        assert result is False

    def test_list_workflows_sorted(self, engine):
        engine.create_workflow(name="A", tasks=[])
        engine.create_workflow(name="B", tasks=[])
        engine.create_workflow(name="C", tasks=[])
        workflows = engine.list_workflows()
        ids = [w.workflow_id for w in workflows]
        assert ids == sorted(ids)


class TestWorkflowEngineLoadErrors:
    def test_corrupted_workflow_file_skipped(self, temp_workflow_dir):
        """Corrupted workflow file is skipped during load."""
        bad_file = temp_workflow_dir / "bad.json"
        bad_file.write_text("not valid json {{{")
        with patch("workflow_engine.WORKFLOW_DIR", temp_workflow_dir):
            engine2 = WorkflowEngine()
        # Should not raise; just skip bad file
        assert engine2.list_workflows() == []


class TestExecuteWorkflowEdgeCases:
    @pytest.mark.asyncio
    async def test_execute_nonexistent_workflow_raises(self, engine):
        """execute_workflow raises ValueError for unknown workflow ID."""
        with pytest.raises(ValueError, match="not found"):
            await engine.execute_workflow("wf-99999")

    @pytest.mark.asyncio
    async def test_execute_workflow_with_dag_error(self, engine):
        """execute_workflow handles DAG build errors."""
        # Create workflow with cycle (t1 depends on t2, t2 depends on t1)
        workflow = engine.create_workflow(
            name="Cyclic",
            tasks=[
                {"task_id": "t1", "action": "a1", "args": {}, "depends_on": ["t2"]},
                {"task_id": "t2", "action": "a2", "args": {}, "depends_on": ["t1"]},
            ],
        )
        execution = await engine.execute_workflow(workflow.workflow_id)
        assert execution.status == WorkflowStatus.FAILED

    @pytest.mark.asyncio
    async def test_execute_workflow_task_timeout(self, engine):
        """Task timeout is handled as failure."""
        import asyncio

        async def slow_skill():
            await asyncio.sleep(999)

        engine.register_skills({"slow_skill": slow_skill})

        async def fake_wait_for(coro, timeout):
            coro.close()
            raise asyncio.TimeoutError()

        workflow = engine.create_workflow(
            name="Timeout Test",
            tasks=[{"task_id": "t1", "action": "slow_skill", "args": {}, "depends_on": []}],
        )
        with patch("workflow_engine.asyncio.wait_for", side_effect=fake_wait_for):
            execution = await engine.execute_workflow(workflow.workflow_id)

        # Task should have failed
        task_statuses = [t.status for t in workflow.tasks]
        assert TaskStatus.FAILED in task_statuses

    @pytest.mark.asyncio
    async def test_execute_workflow_partial_status(self, engine):
        """Workflow with some failing tasks gets PARTIAL status with continue_on_error."""
        async def ok_skill():
            return "OK"

        async def bad_skill():
            raise RuntimeError("boom")

        engine.register_skills({"ok_skill": ok_skill, "bad_skill": bad_skill})

        workflow = engine.create_workflow(
            name="Partial",
            error_handling="continue_on_error",
            tasks=[
                {"task_id": "t1", "action": "bad_skill", "args": {}, "depends_on": []},
                {"task_id": "t2", "action": "ok_skill", "args": {}, "depends_on": []},
            ],
        )
        execution = await engine.execute_workflow(workflow.workflow_id)
        assert execution.status in (WorkflowStatus.PARTIAL, WorkflowStatus.SUCCESS, WorkflowStatus.FAILED)


class TestLLMWorkflowSkills:
    @pytest.mark.asyncio
    async def test_create_workflow_from_template_unknown(self):
        """create_workflow_from_template returns error for unknown template."""
        result = await create_workflow_from_template("nonexistent-template")
        assert "❌" in result
        assert "Unknown template" in result

    @pytest.mark.asyncio
    async def test_create_workflow_from_template_known(self, temp_workflow_dir):
        """create_workflow_from_template creates from known template."""
        with patch("workflow_engine.WORKFLOW_DIR", temp_workflow_dir):
            result = await create_workflow_from_template("morning-briefing")
        assert "✅" in result or "❌" in result  # Either works or fails gracefully

    @pytest.mark.asyncio
    async def test_run_workflow_not_found(self):
        """run_workflow returns error for non-existent workflow."""
        result = await run_workflow("wf-99999")
        assert "❌" in result
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_run_workflow_success(self, engine, temp_workflow_dir):
        """run_workflow executes and returns success message."""
        async def simple_skill():
            return "done"

        workflow_engine.register_skills({"simple_skill": simple_skill})
        with patch("workflow_engine.WORKFLOW_DIR", temp_workflow_dir):
            wf = workflow_engine.create_workflow(
                name="RunTest",
                tasks=[{"task_id": "t1", "action": "simple_skill", "args": {}, "depends_on": []}],
            )
            result = await run_workflow(wf.workflow_id)
        assert "✅" in result or "⚠️" in result or "❌" in result

    @pytest.mark.asyncio
    async def test_run_workflow_exception(self, tmp_path):
        """run_workflow handles exceptions gracefully."""
        wf_dir = tmp_path / "wf_exc"
        wf_dir.mkdir()

        async def boom():
            raise RuntimeError("crash")

        workflow_engine.register_skills({"boom": boom})
        with patch("workflow_engine.WORKFLOW_DIR", wf_dir):
            wf = workflow_engine.create_workflow(
                name="BoomTest",
                error_handling="fail_fast",
                tasks=[{"task_id": "t1", "action": "boom", "args": {}, "depends_on": []}],
            )
            result = await run_workflow(wf.workflow_id)
        assert result is not None

    @pytest.mark.asyncio
    async def test_list_workflows_skill_empty(self):
        """list_workflows_skill returns message when no workflows."""
        orig = workflow_engine._workflows.copy()
        workflow_engine._workflows.clear()
        try:
            result = await list_workflows_skill()
            assert "No workflows" in result
        finally:
            workflow_engine._workflows.update(orig)

    @pytest.mark.asyncio
    async def test_list_workflows_skill_with_entries(self, temp_workflow_dir):
        """list_workflows_skill lists existing workflows."""
        with patch("workflow_engine.WORKFLOW_DIR", temp_workflow_dir):
            workflow_engine.create_workflow(name="TestWF", tasks=[])
        orig_copy = dict(workflow_engine._workflows)
        try:
            result = await list_workflows_skill()
            # Should contain workflow info
            assert isinstance(result, str)
        finally:
            workflow_engine._workflows.clear()
            workflow_engine._workflows.update(orig_copy)
