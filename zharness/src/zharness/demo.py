from zharness.agent import build_agent


def main() -> None:
    agent = build_agent()

    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "请使用工具计算 123 加 456。",
                }
            ]
        }
    )

    final_message = result["messages"][-1]
    print(final_message.content)


if __name__ == "__main__":
    main()
