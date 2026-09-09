"""Strands Agent 配置。"""

from strands import Agent
from strands.models.bedrock import BedrockModel

model = BedrockModel(
    model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
    region_name="us-east-1",
)

agent = Agent(
    model=model,
    system_prompt="你是 TagMate，一个团队协作 AI 助手。用简洁的中文回答问题。",
)
