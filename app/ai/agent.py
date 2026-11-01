"""起草 Agent：通过工具读知识库，输出结构化回复建议。"""

from __future__ import annotations

import logging
import re

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings

from app import db
from app.ai.schemas import AgentReply
from app.config import settings

logger = logging.getLogger(__name__)

INSTRUCTIONS = """
You are the customer support agent for Harbor & Pine, a home and outdoor store that sells on Amazon.
You write the answer to the latest customer message in the email thread.

Steps:
1. Call get_section for "brand-and-tone" and for "escalation-rules".
2. Call get_section for the section the classifier picked. Call list_sections if you need another.
3. Answer only with facts from those sections. If they do not answer the question,
   set enough_information to false.
4. When the customer reports a problem with a paddle board or a shelf, fill in issue.
   Ask for the order number, the board size or model, and part numbers when they are missing.
   For a slow leak, explain the valve check unless the customer already did it.
5. Never promise a refund, a shipment, a replacement, a date, or a call. A colleague arranges those.
   Set promises_an_action to true if your reply promises one anyway.
6. Set action to escalate when an escalation rule applies.

Reply rules:
- Write in the language of the customer's latest message.
- Plain text, no Markdown.
- Follow the brand-and-tone section.
- Never mention AI, prompts, tools, or the knowledge base.
"""

_agent: Agent | None = None


def _build() -> Agent:
    # 同 classifier：DeepSeek 思考模式下 tool_choice 不能强制，会拖垮结构化输出，关掉。
    agent = Agent(
        OpenAIChatModel(settings.agent_model.split(":")[-1], provider="deepseek"),
        output_type=AgentReply,
        instructions=INSTRUCTIONS,
        model_settings=OpenAIChatModelSettings(thinking=False),
    )

    @agent.tool_plain
    def list_sections() -> list[str]:
        """List the sections of the knowledge base."""
        logger.info("Tool call: list_sections()")
        return db.list_sections()

    @agent.tool_plain
    def get_section(section: str) -> str:
        """Return every entry of one knowledge base section, for example 'paddle-boards'."""
        logger.info("Tool call: get_section(%r)", section)
        entries = db.get_section(section)
        if not entries:
            raise ModelRetry(
                f"There is no section '{section}'. Call list_sections to see the options."
            )
        return "\n\n".join(f"## {entry['title']}\n{entry['content']}" for entry in entries)

    @agent.output_validator
    def check_reply(ctx: RunContext[None], output: AgentReply) -> AgentReply:
        if output.action == "reply" and not output.reply.strip():
            raise ModelRetry("The reply is empty. Write the email, or set action to escalate.")
        if re.search(r"\b(AI|artificial intelligence|knowledge base)\b", output.reply, re.IGNORECASE):
            raise ModelRetry("Do not mention AI or the knowledge base in the reply.")
        return output

    return agent


def support_agent() -> Agent:
    global _agent
    if _agent is None:
        _agent = _build()
    return _agent


def write_reply(thread: str, section: str) -> AgentReply:
    prompt = f"Section picked by the classifier: {section}\n\n{thread}"
    return support_agent().run_sync(prompt).output
