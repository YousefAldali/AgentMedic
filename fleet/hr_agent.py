"""HR agent: answers leave-balance and policy questions (HR DB + knowledge base)."""
from common.config import service_url
from fleet.base_agent import BaseAgent, Tool, http_get


def get_leave_balance(employee_id: str) -> dict:
    return http_get(f"{service_url('hr_db')}/employees/{employee_id}/leave")


def search_knowledge_base(query: str) -> dict:
    return http_get(f"{service_url('knowledge_base')}/search", {"q": query, "k": 3})


class HRAgent(BaseAgent):
    name = "hr"
    tools = {
        "get_leave_balance": Tool(get_leave_balance, {
            "type": "function", "function": {
                "name": "get_leave_balance",
                "description": "Get an employee's remaining annual leave days.",
                "parameters": {"type": "object", "properties": {
                    "employee_id": {"type": "string", "description": "Employee ID, e.g. E002"}},
                    "required": ["employee_id"]}}}),
        "search_knowledge_base": Tool(search_knowledge_base, {
            "type": "function", "function": {
                "name": "search_knowledge_base",
                "description": "Search company policy documents (English and Arabic).",
                "parameters": {"type": "object", "properties": {
                    "query": {"type": "string"}}, "required": ["query"]}}}),
    }
