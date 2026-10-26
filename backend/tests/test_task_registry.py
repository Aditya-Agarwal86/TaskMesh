import pytest
from app.workers.task_registry import TaskRegistry


@pytest.mark.asyncio
async def test_task_registry_registration_and_execution():
    registry = TaskRegistry()

    @registry.register("math_add")
    async def add_handler(payload):
        return {"result": payload["a"] + payload["b"]}

    @registry.register("sync_greet")
    def sync_greet(payload):
        return f"Hello, {payload['name']}!"

    assert "math_add" in registry.list_registered_tasks()
    assert "sync_greet" in registry.list_registered_tasks()

    # Execute async handler
    res_add = await registry.execute("math_add", {"a": 10, "b": 25})
    assert res_add["result"] == 35

    # Execute sync handler
    res_greet = await registry.execute("sync_greet", {"name": "TaskMesh"})
    assert res_greet == "Hello, TaskMesh!"

    # Unregistered handler raises ValueError
    with pytest.raises(ValueError, match="No task handler registered"):
        await registry.execute("unknown_type", {})
