"""
OpenClaw Workflow API — Phase 3
REST API endpoints for workflow management and automation.
"""

import json
import logging

from aiohttp import web

from workflow_engine import workflow_engine

log = logging.getLogger("openclaw.workflow_api")


# ---------------------------------------------------------------------------
# API Handlers
# ---------------------------------------------------------------------------


async def create_workflow_handler(request: web.Request) -> web.Response:
    """POST /api/workflows - Create a new workflow."""
    try:
        data = await request.json()

        name = data.get("name")
        if not name:
            return web.json_response(
                {"error": "Missing required field: name"},
                status=400,
            )

        description = data.get("description", "")
        tasks = data.get("tasks", [])
        error_handling = data.get("error_handling", "fail_fast")
        rollback_on_error = data.get("rollback_on_error", False)
        created_by = data.get("created_by", "api")

        workflow = workflow_engine.create_workflow(
            name=name,
            description=description,
            tasks=tasks,
            error_handling=error_handling,
            rollback_on_error=rollback_on_error,
            created_by=created_by,
        )

        return web.json_response(workflow.to_dict(), status=201)

    except Exception as e:  # broad: intentional — HTTP handler must return error response; not raise
        log.error("Failed to create workflow: %s", e)
        return web.json_response(
            {"error": f"Failed to create workflow: {e}"},
            status=500,
        )


async def list_workflows_handler(request: web.Request) -> web.Response:
    """GET /api/workflows - List all workflows."""
    try:
        workflows = workflow_engine.list_workflows()

        return web.json_response({
            "workflows": [w.to_dict() for w in workflows],
            "count": len(workflows),
        })

    except Exception as e:  # broad: intentional — HTTP handler must return error response; not raise
        log.error("Failed to list workflows: %s", e)
        return web.json_response(
            {"error": f"Failed to list workflows: {e}"},
            status=500,
        )


async def get_workflow_handler(request: web.Request) -> web.Response:
    """GET /api/workflows/{id} - Get workflow details."""
    try:
        workflow_id = request.match_info["id"]
        workflow = workflow_engine.get_workflow(workflow_id)

        if not workflow:
            return web.json_response(
                {"error": f"Workflow {workflow_id} not found"},
                status=404,
            )

        return web.json_response(workflow.to_dict())

    except Exception as e:  # broad: intentional — HTTP handler must return error response; not raise
        log.error("Failed to get workflow: %s", e)
        return web.json_response(
            {"error": f"Failed to get workflow: {e}"},
            status=500,
        )


async def update_workflow_handler(request: web.Request) -> web.Response:
    """PUT /api/workflows/{id} - Update workflow."""
    try:
        workflow_id = request.match_info["id"]
        workflow = workflow_engine.get_workflow(workflow_id)

        if not workflow:
            return web.json_response(
                {"error": f"Workflow {workflow_id} not found"},
                status=404,
            )

        data = await request.json()

        # Update fields
        if "name" in data:
            workflow.name = data["name"]
        if "description" in data:
            workflow.description = data["description"]
        if "error_handling" in data:
            workflow.error_handling = data["error_handling"]
        if "rollback_on_error" in data:
            workflow.rollback_on_error = data["rollback_on_error"]

        # Save changes
        workflow_engine._save_workflow(workflow)

        return web.json_response(workflow.to_dict())

    except Exception as e:  # broad: intentional — HTTP handler must return error response; not raise
        log.error("Failed to update workflow: %s", e)
        return web.json_response(
            {"error": f"Failed to update workflow: {e}"},
            status=500,
        )


async def delete_workflow_handler(request: web.Request) -> web.Response:
    """DELETE /api/workflows/{id} - Delete workflow."""
    try:
        workflow_id = request.match_info["id"]

        result = workflow_engine.delete_workflow(workflow_id)

        if not result:
            return web.json_response(
                {"error": f"Workflow {workflow_id} not found"},
                status=404,
            )

        return web.json_response({"message": "Workflow deleted"})

    except Exception as e:  # broad: intentional — HTTP handler must return error response; not raise
        log.error("Failed to delete workflow: %s", e)
        return web.json_response(
            {"error": f"Failed to delete workflow: {e}"},
            status=500,
        )


async def execute_workflow_handler(request: web.Request) -> web.Response:
    """POST /api/workflows/{id}/execute - Execute a workflow."""
    try:
        workflow_id = request.match_info["id"]
        workflow = workflow_engine.get_workflow(workflow_id)

        if not workflow:
            return web.json_response(
                {"error": f"Workflow {workflow_id} not found"},
                status=404,
            )

        # Get optional context from request body
        try:
            data = await request.json()
            context = data.get("context", {})
        except (json.JSONDecodeError, ValueError):
            context = {}

        # Execute workflow
        execution = await workflow_engine.execute_workflow(workflow_id, context)

        return web.json_response({
            "execution_id": execution.execution_id,
            "workflow_id": execution.workflow_id,
            "status": execution.status,
            "started_at": execution.started_at,
            "completed_at": execution.completed_at,
            "task_results": execution.task_results,
            "errors": execution.errors,
        })

    except Exception as e:  # broad: intentional — HTTP handler must return error response; not raise
        log.error("Failed to execute workflow: %s", e)
        return web.json_response(
            {"error": f"Failed to execute workflow: {e}"},
            status=500,
        )


async def get_templates_handler(request: web.Request) -> web.Response:
    """GET /api/workflows/templates - List available workflow templates."""
    try:
        templates = workflow_engine.get_templates()

        return web.json_response({
            "templates": templates,
            "count": len(templates),
        })

    except Exception as e:  # broad: intentional — HTTP handler must return error response; not raise
        log.error("Failed to get templates: %s", e)
        return web.json_response(
            {"error": f"Failed to get templates: {e}"},
            status=500,
        )


async def create_from_template_handler(request: web.Request) -> web.Response:
    """POST /api/workflows/from-template - Create workflow from template."""
    try:
        data = await request.json()

        template_name = data.get("template")
        if not template_name:
            return web.json_response(
                {"error": "Missing required field: template"},
                status=400,
            )

        created_by = data.get("created_by", "api")

        workflow = workflow_engine.create_from_template(template_name, created_by)

        if not workflow:
            return web.json_response(
                {"error": f"Template '{template_name}' not found"},
                status=404,
            )

        return web.json_response(workflow.to_dict(), status=201)

    except Exception as e:  # broad: intentional — HTTP handler must return error response; not raise
        log.error("Failed to create workflow from template: %s", e)
        return web.json_response(
            {"error": f"Failed to create workflow: {e}"},
            status=500,
        )


# ---------------------------------------------------------------------------
# Routes Setup
# ---------------------------------------------------------------------------


def setup_workflow_routes(app: web.Application) -> None:
    """Register workflow API routes."""
    app.router.add_get("/api/workflows/templates", get_templates_handler)
    app.router.add_post("/api/workflows/from-template", create_from_template_handler)
    app.router.add_post("/api/workflows", create_workflow_handler)
    app.router.add_get("/api/workflows", list_workflows_handler)
    app.router.add_get("/api/workflows/{id}", get_workflow_handler)
    app.router.add_put("/api/workflows/{id}", update_workflow_handler)
    app.router.add_delete("/api/workflows/{id}", delete_workflow_handler)
    app.router.add_post("/api/workflows/{id}/execute", execute_workflow_handler)

    log.info("Workflow API routes registered")
