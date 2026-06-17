# Error Guide

## Fusion Memory is not available

Run:

```bash
fusion-memory doctor
fusion-memory start
```

The Agent should continue without memory.

## Database is not ready

Run:

```bash
fusion-memory doctor
```

Check that Postgres is running and that the configured database exists.

## Model is not ready

Run:

```bash
fusion-memory doctor
```

The default models are Qwen3-Embedding-0.6B and Qwen3-Reranker-0.6B.

## Adapter is not enabled

Run:

```bash
fusion-memory install-agent --target all
```

For test model configuration, pass `/public/home/wwb/test_key/key.txt` by path.
Never paste key contents into an issue, log, or chat.
