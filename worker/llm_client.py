"""
LLM client for the Skillberry Worker.

Routes all LLM calls through the Praxis llm-egress listener (port 8081).
No provider credentials here — Praxis credential_injection handles them.
"""
import os

from langchain_openai import ChatOpenAI


def build_llm(model: str, temperature: float = 0.0) -> ChatOpenAI:
    """
    Build a LangChain-compatible LLM client that routes all calls
    through the Praxis llm-egress listener.

    - api_key is intentionally unused: Praxis credential_injection injects the
      real provider key before the request reaches the upstream.
    - base_url points at Praxis port 8081 (loopback, worker-reachable only).
    - model and temperature come directly from the client's original request body.
      The client owns these values — the worker never has defaults for them.
    """
    return ChatOpenAI(
        model=model,
        base_url=os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8081/v1"),
        api_key="not-used",  # stripped by Praxis credential_injection
        temperature=temperature,
    )
