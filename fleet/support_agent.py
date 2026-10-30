# Support agent: answers customer questions with retrieval over the knowledge base (RAG).
from fleet.base_agent import BaseAgent, Tool
from fleet.hr_agent import search_knowledge_base


class SupportAgent(BaseAgent):
    name = "support"
    tools = {
        "search_knowledge_base": Tool(search_knowledge_base, {
            "type": "function", "function": {
                "name": "search_knowledge_base",
                "description": "Search the company knowledge base (English and Arabic): passwords, refunds, policies.",
                "parameters": {"type": "object", "properties": {
                    "query": {"type": "string"}}, "required": ["query"]}}}),
    }
