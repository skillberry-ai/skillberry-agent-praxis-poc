"""
LLM client for the Skillberry Worker.

Routes all LLM calls through the Praxis llm-egress listener (port 8081).

Model, temperature, and provider credentials are all Praxis-owned:
- model and temperature are injected by Praxis as x-skillberry-llm-* headers
  on the client-ingress leg. The worker reads them from those headers and
  passes them here — the client-supplied values are ignored.
- api_key is a dummy placeholder: Praxis credential_injection overwrites the
  Authorization header with SPAPRAXIS_API_KEY before the request reaches the
  upstream provider.
"""
import os

from langchain_openai import ChatOpenAI


def build_llm(model: str, temperature: float) -> ChatOpenAI:
    """
    Build a LangChain-compatible LLM client that routes all calls
    through the Praxis llm-egress listener.

    Args:
        model:       Model name set by Praxis from SPAPRAXIS_MODEL.
        temperature: Temperature set by Praxis from SPAPRAXIS_TEMPERATURE.
    """
    return ChatOpenAI(
        model=model,
        base_url=os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8081/v1"),
        api_key="not-used",  # overwritten by Praxis credential_injection (SPAPRAXIS_API_KEY)
        temperature=temperature,
    )
