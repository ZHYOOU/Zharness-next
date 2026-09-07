"""Resolve the configured LangSmith project's trace URL. / 解析配置的 LangSmith 项目 trace 地址。"""

import logging
from pathlib import Path

from dotenv import load_dotenv
from langsmith import Client
from langsmith.utils import get_tracer_project


def main() -> None:
    """Look up the project without creating remote resources. / 查询项目且不创建远程资源。"""
    load_dotenv(Path(__file__).resolve().parents[1] / "zharness" / ".env")
    logging.disable(logging.CRITICAL)
    client = Client(timeout_ms=1500)
    try:
        project = client.read_project(project_name=get_tracer_project())
        if project.url:
            print(project.url)
    finally:
        client.close()


if __name__ == "__main__":
    main()
