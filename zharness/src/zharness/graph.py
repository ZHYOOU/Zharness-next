import os

from zharness.agents.lead import create_lead_agent
from zharness.models.factory import create_chat_model

model = create_chat_model(
    os.environ["ZHARNESS_MODEL"],
)

graph = create_lead_agent(model)
