from langchain_core.language_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek


def create_chat_model(model_name: str, *, temperature: float = 0) -> BaseChatModel:
    return ChatDeepSeek(
        model=model_name,
        temperature=temperature,
        timeout=60,
        max_retries=3,
    )
