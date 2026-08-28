import os

from dotenv import load_dotenv

from zharness.agents.lead import create_lead_agent
from zharness.models.factory import create_chat_model


def main() -> None:
    load_dotenv()

    model = create_chat_model(
        os.environ["ZHARNESS_MODEL"],
    )

    agent = create_lead_agent(model)

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

    print(result["messages"][-1].content)


if __name__ == "__main__":
    main()
