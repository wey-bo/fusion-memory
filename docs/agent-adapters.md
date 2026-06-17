# Agent Adapters

Fusion Memory uses one local service for all Agent integrations.

Install all adapters:

```bash
fusion-memory install-agent --target all
```

Install one adapter:

```bash
fusion-memory install-agent --target openclaw
fusion-memory install-agent --target hermes
fusion-memory install-agent --target fusion-agent
```

OpenClaw and Hermes are installed as external plugins. Their source checkouts
are not modified in stage one.

Fusion-Agent uses its in-repo adapter. Start a session with memory enabled and:

```bash
export PSI_MEMORY_BASE_URL=http://127.0.0.1:8765
```

For test model configuration, pass `/public/home/wwb/test_key/key.txt` as a file
path to test commands that accept a model config file. Do not paste key content
into logs or docs.
