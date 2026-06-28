# skillberry-praxis-filters

> ⚠️ **Work in Progress** — This repository is actively evolving. Features, APIs, and configuration may change at any time. Please monitor it frequently for updates.

External [Praxis](https://github.com/praxis-proxy/praxis) filters for the Skillberry ecosystem.

## Filters

| Filter | Description |
|--------|-------------|
| `context_extractor` | Extracts request headers into filter metadata for downstream filters |
| `skill_resolver` | Resolves skill UUIDs from environment variables or via skillberry-store API lookup |
| `vmcp_manager` | Creates Virtual MCP (VMCP) servers and fetches available MCP tools |
| `mcp_tools_enricher` | Injects MCP tools into OpenAI-compatible chat completion request bodies |

## Quickstart

### 1. Check out Praxis at the pinned commit

```console
git clone https://github.com/praxis-proxy/praxis.git praxis
cd praxis && git checkout 0bc9534e922a8be313331dd9f317356e5097d109
```

### 2. Add this crate as a dependency

Three edits inside the Praxis checkout are required:

**`Cargo.toml`** (workspace root) — declare the dependency:

```toml
[workspace.dependencies]
skillberry-praxis-filters = { git = "https://github.com/skillberry-ai/skillberry-praxis-filters.git", branch = "main" }
```

**`Cargo.toml`** (workspace root, bottom) — patch `praxis-proxy-filter` to use the local path so both this crate and Praxis resolve to the same copy (without this the build fails with a type mismatch):

```toml
[patch."https://github.com/praxis-proxy/praxis.git"]
praxis-proxy-filter = { path = "filter" }
```

**`server/Cargo.toml`** — add it to the server crate's `[dependencies]` so it is compiled in:

```toml
[dependencies]
skillberry-praxis-filters = { workspace = true }
```

### 3. Build Praxis

```console
cargo update && cargo build --package praxis-proxy
```

> Use `--release` instead for a production deployment.

### 4. Run Skillberry services

The filter chain calls the Skillberry Store API to resolve skills and manage VMCP servers. Ensure [`skillberry-store`](https://github.com/skillberry-ai/skillberry-store) is running and reachable at the URL configured in `pipeline/skillberry.yaml` (default: `http://localhost:8000`).

### 5. Run Praxis with the pipeline config

Set at minimum a skill name or UUID, then start the server:

```console
export SKILL_NAME="my-skill-name"  # replace with a skill loaded into the store
./target/debug/praxis -c /path/to/skillberry-praxis-filters/pipeline/skillberry.yaml
```

See [Environment Variables](#environment-variables) for the full list of tuneable settings.

Validate config without starting the server:

```console
./target/debug/praxis -t -c /path/to/skillberry-praxis-filters/pipeline/skillberry.yaml
```

### 6. Run the client emulation script

Install the required dependency:

```console
pip install litellm
```

Then run:

```console
export OPENAI_API_KEY=<your-key>
python pipeline/emulate_client.py
```


## Environment Variables

| Env var | Default | Filter | Description |
|---------|---------|--------|-------------|
| `SKILL_UUID` | — | `skill_resolver` | Direct skill UUID (highest priority) |
| `SKILL_NAME` | — | `skill_resolver` | Skill name resolved via store API (priority 2) |

## Pipeline Folder

| File | Purpose |
|------|---------|
| `pipeline/skillberry.yaml` | Praxis server config driving the full Skillberry filter chain |
| `pipeline/emulate_client.py` | Client emulation script (LiteLLM → Praxis proxy) |

## Filter Chain

These filters are designed to work together in sequence:

1. **`context_extractor`** — Reads configured request headers, validates them, stores values in `filter_metadata` (e.g. `env_id`)
2. **`skill_resolver`** — Reads `SKILL_UUID` or `SKILL_NAME` env vars, resolves to a UUID, stores in `filter_metadata["skill_uuid"]`
3. **`vmcp_manager`** — Creates a VMCP server (using skill UUID + env ID from metadata), fetches MCP tools via SSE, stores in `filter_metadata["mcp_tools"]`
4. **`mcp_tools_enricher`** — Reads tools from metadata, injects them into the request body's `tools` array

## Configuration

Full Praxis configuration (`pipeline/skillberry.yaml`):

```yaml
listeners:
  - name: skillberry_proxy
    address: 0.0.0.0:8080
    filter_chains:
      - skillberry_chain

filter_chains:
  - name: skillberry_chain
    filters:
      - filter: context_extractor
        headers:
          - name: x-skillberry-env-id
            metadata_key: env_id
            default: "default-env"
            required: true
            pattern: "^[a-zA-Z0-9_-]+$"
            max_length: 64
          - name: x-skillberry-user-id
            metadata_key: user_id
            default: "anonymous"
            required: false
            pattern: "^[a-zA-Z0-9_-]+$"
            max_length: 64
          - name: x-skillberry-session-id
            metadata_key: session_id
            required: false
            pattern: "^[a-zA-Z0-9_-]+$"
            max_length: 128

      - filter: skill_resolver
        store_base_url: "http://localhost:8000"
        skill_uuid_env: "SKILL_UUID"
        skill_name_env: "SKILL_NAME"
        timeout_ms: 5000

      - filter: vmcp_manager
        store_base_url: "http://localhost:8000"
        vmcp_name_template: "vmcp-{env_id}"
        timeout_ms: 10000
        always_create: true
        cleanup_on_error: false

      - filter: mcp_tools_enricher
        timeout_ms: 5000
        tool_choice: auto
        max_body_bytes: 10485760
        on_invalid: continue

      - filter: router
        routes:
          - path_prefix: "/"
            cluster: llm_backend

      - filter: load_balancer
        clusters:
          - name: llm_backend
            endpoints:
                - "localhost:4000"  # replace with your LiteLLM / OpenAI-compatible proxy address
            connection_timeout_ms: 5000
            read_timeout_ms: 60000
            write_timeout_ms: 60000

runtime:
  threads: 4
  max_connections: 10000
```
