import asyncio

from langgraph_sdk import get_client


async def run_turn(
    client,
    thread_id: str,
    message: str,
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
        stream_mode="updates",
    ):
        print(event.event, event.data)


async def main() -> None:
    client = get_client(url="http://127.0.0.1:2024")

    thread = await client.threads.create()
    thread_id = thread["thread_id"]

    print("thread:", thread_id)

    await run_turn(
        client,
        thread_id,
        "请使用工具计算 123 加 456，并记住结果。",
    )

    first_state = await client.threads.get_state(thread_id)
    first_count = len(first_state["values"]["messages"])

    await run_turn(
        client,
        thread_id,
        "请把刚才的计算结果再加 1。",
    )

    second_state = await client.threads.get_state(thread_id)
    second_count = len(second_state["values"]["messages"])

    assert second_count > first_count
    print("multi-turn checkpoint: ok")


if __name__ == "__main__":
    asyncio.run(main())
