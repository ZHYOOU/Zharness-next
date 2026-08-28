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
    (workspace / "hello.txt").write_text("hello", encoding="utf-8")
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
        "刚才工作区中看到的文件名是什么？",
        workspace_path,
    )

    second_state = await client.threads.get_state(thread_id)
    second_count = len(second_state["values"]["messages"])

    assert second_count > first_count
    print("runtime workspace context: ok")


if __name__ == "__main__":
    asyncio.run(main())
