from zharness.agents.lead import create_lead_agent
from zharness.config.loader import get_settings
from zharness.models.factory import create_chat_model

model_name = get_settings().model.name
model = create_chat_model(model_name)

graph = create_lead_agent(model, model_name=model_name)
