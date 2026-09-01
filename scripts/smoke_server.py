import asyncio
from pathlib import Path

from dotenv import load_dotenv
from langgraph_sdk import get_client
from zharness.host.paths import ensure_thread_workspace

ZHARNESS_ENV_FILE = Path(__file__).resolve().parents[1] / "zharness" / ".env"
load_dotenv(ZHARNESS_ENV_FILE, override=False)


async def run_turn(
    client,
    thread_id: str,
    message: str | None = None,
    *,
    command: dict | None = None,
) -> None:
    if (message is None) == (command is None):
        raise ValueError("Provide exactly one of message or command")

    request = (
        {
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": message,
                    }
                ]
            }
        }
        if message is not None
        else {"command": command}
    )
    async for event in client.runs.stream(
        thread_id,
        "lead_agent",
        stream_mode="updates",
        **request,
    ):
        print(event.event, event.data)


async def main() -> None:
    client = get_client(url="http://127.0.0.1:2024")

    thread = await client.threads.create()
    thread_id = thread["thread_id"]

    print("thread:", thread_id)

    workspace = ensure_thread_workspace(thread_id)
    expected_content = "hello from the runtime-scoped workspace"
    (workspace / "hello.txt").write_text(expected_content, encoding="utf-8")

    await run_turn(
        client,
        thread_id,
        "请使用 list_workspace 工具列出当前工作区文件，并记住文件名。",
    )

    first_state = await client.threads.get_state(thread_id)
    first_messages = first_state["values"]["messages"]
    first_count = len(first_messages)

    workspace_results = [
        message
        for message in first_messages
        if message.get("type") == "tool" and message.get("name") == "list_workspace"
    ]

    assert workspace_results
    assert "hello.txt" in str(workspace_results[-1]["content"])

    await run_turn(
        client,
        thread_id,
        "请使用 read_file 工具读取刚才看到的文件，并告诉我文件内容。",
    )

    second_state = await client.threads.get_state(thread_id)
    second_messages = second_state["values"]["messages"]
    second_count = len(second_messages)

    read_results = [
        message
        for message in second_messages
        if message.get("type") == "tool" and message.get("name") == "read_file"
    ]

    assert second_count > first_count
    assert read_results
    assert expected_content in str(read_results[-1]["content"])
    print("runtime workspace read: ok")

    written_content = "smoke write success"
    await run_turn(
        client,
        thread_id,
        (
            "请使用 write_file 工具创建 result.txt，"
            f"文件内容必须完全等于：{written_content}"
        ),
    )

    third_state = await client.threads.get_state(thread_id)
    third_messages = third_state["values"]["messages"]
    write_results = [
        message
        for message in third_messages
        if message.get("type") == "tool" and message.get("name") == "write_file"
    ]

    assert len(third_messages) > second_count
    assert write_results
    assert (workspace / "result.txt").read_text(encoding="utf-8") == written_content
    print("runtime workspace write: ok")

    await run_turn(
        client,
        thread_id,
        (
            "这是一个多步骤任务。请先使用 write_todos 创建计划，然后依次完成："
            "1. 使用 read_file 读取 result.txt；"
            "2. 使用 edit_file 将 smoke 替换为 verified；"
            "每完成一步立即更新 todo 状态，最后确保所有 todo 都是 completed。"
        ),
    )

    todo_state = await client.threads.get_state(thread_id)
    todos = todo_state["values"].get("todos", [])
    todo_messages = todo_state["values"]["messages"]
    todo_results = [
        message
        for message in todo_messages
        if message.get("type") == "tool" and message.get("name") == "write_todos"
    ]

    assert todo_results
    assert todos
    assert all(todo["status"] == "completed" for todo in todos)
    assert (workspace / "result.txt").read_text(encoding="utf-8") == (
        "verified write success"
    )

    isolated_thread = await client.threads.create()
    isolated_state = await client.threads.get_state(isolated_thread["thread_id"])
    assert not isolated_state["values"].get("todos")
    print("runtime todo planning: ok")

    docker_thread = await client.threads.create()
    docker_thread_id = docker_thread["thread_id"]
    docker_workspace = ensure_thread_workspace(docker_thread_id)
    docker_content = "docker sandbox execution success"

    await run_turn(
        client,
        docker_thread_id,
        (
            "请务必使用 execute_command 工具执行下面的命令，不要使用其他工具："
            f"printf '%s' '{docker_content}' > docker-result.txt "
            "&& cat docker-result.txt"
        ),
    )

    interrupted_state = await client.threads.get_state(docker_thread_id)
    interrupts = [
        interrupt
        for task in interrupted_state.get("tasks", [])
        for interrupt in task.get("interrupts", [])
    ]

    assert len(interrupts) == 1
    action_requests = interrupts[0]["value"]["action_requests"]
    assert action_requests[0]["name"] == "execute_command"
    assert docker_content in action_requests[0]["args"]["command"]
    assert not (docker_workspace / "docker-result.txt").exists()
    print("docker sandbox execution interrupted for approval: ok")

    await run_turn(
        client,
        docker_thread_id,
        command={"resume": {"decisions": [{"type": "approve"}]}},
    )

    docker_state = await client.threads.get_state(docker_thread_id)
    execute_results = [
        message
        for message in docker_state["values"]["messages"]
        if message.get("type") == "tool" and message.get("name") == "execute_command"
    ]

    assert execute_results
    assert docker_content in str(execute_results[-1]["content"])
    assert "[exit_code=0]" in str(execute_results[-1]["content"])
    assert (docker_workspace / "docker-result.txt").read_text(
        encoding="utf-8"
    ) == docker_content
    print("docker sandbox execution and workspace mount: ok")


if __name__ == "__main__":
    asyncio.run(main())
