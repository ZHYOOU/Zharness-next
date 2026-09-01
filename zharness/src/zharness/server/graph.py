from zharness.agents.lead import create_lead_agent
from zharness.config.loader import get_settings
from zharness.models.factory import create_chat_model

model = create_chat_model(get_settings().model.name)

graph = create_lead_agent(model)
