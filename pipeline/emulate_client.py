"""
test_client.py – Client/agent emulation script for the Skillberry Praxis filter pipeline.

Sends an OpenAI-compatible chat completion request through the Praxis proxy
(which runs the skillberry filter chain) and prints the model's response.

Usage
-----
    # 1. Export your LiteLLM key
    export OPENAI_API_KEY=<your-key>

    # 2. (Optional) override the proxy address if Praxis is not on localhost:8080
    export OPENAI_API_BASE=http://localhost:8080/v1

    # 3. Run
    python pipeline/test_client.py

Environment variables
---------------------
OPENAI_API_KEY   – Required. API key forwarded by LiteLLM to the upstream LLM.
OPENAI_API_BASE  – Optional. Base URL of the Praxis proxy. Default: http://localhost:8080/v1
"""

import os
import uuid

from litellm import completion

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# The Praxis proxy address. Override via OPENAI_API_BASE if needed.
os.environ.setdefault("OPENAI_API_BASE", "http://localhost:8080/v1")

# Ensure the API key is present – fail early with a clear message.
if not os.environ.get("OPENAI_API_KEY"):
    raise EnvironmentError(
        "OPENAI_API_KEY is not set. Export it before running this script:\n"
        "    export OPENAI_API_KEY=<your-key>"
    )

# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

# Generate a unique 6-digit environment ID for this test run.
env_id = uuid.uuid4().hex[:6]
print(f"env-id: {env_id}")

response = completion(
    model="openai/rits/openai/gpt-oss-120b",
    messages=[{"role": "user", "content": "Show me your tools"}],
    extra_headers={
        "skillberry-context-env_id": env_id
    },
)

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

print(response.choices[0].message.content)
