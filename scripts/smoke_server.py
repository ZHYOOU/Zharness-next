import asyncio
from pathlib import Path

from langgraph_sdk import get_client


async def run_turn(
    client,
    thread_id: str,
    message: str,
    workspace_path: str,
) -> None:
    async for event in client.runs.stream(
        thread_id,
        "lead_agent",
        input={
            "messages": [
                {
                    "role": "user",
                    "content": message,
                }
            ]
        },
        context={
            "workspace_path": workspace_path,
        },
        stream_mode="updates",
    ):
        print(event.event, event.data)


async def main() -> None:
    client = get_client(url="http://127.0.0.1:2024")

    thread = await client.threads.create()
    thread_id = thread["thread_id"]

    print("thread:", thread_id)

    workspace = Path(".zharness/workspaces") / thread_id
    workspace.mkdir(parents=True, exist_ok=True)
    expected_content = "hello from the runtime-scoped workspace"
    (workspace / "hello.txt").write_text(expected_content, encoding="utf-8")
    workspace_path = str(workspace.resolve())

    await run_turn(
        client,
        thread_id,
        "请使用 list_workspace 工具列出当前工作区文件，并记住文件名。",
        workspace_path,
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
        workspace_path,
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


if __name__ == "__main__":
    asyncio.run(main())
