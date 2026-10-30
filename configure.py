"""Create local DSH provider configuration from environment variables."""
from __future__ import annotations

import os
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent
HOME = Path(os.environ.get("DSH_HOME", ROOT / "data")).resolve()


def main() -> None:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise SystemExit("Set DEEPSEEK_API_KEY in the current shell before running configure.py.")

    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("DEEPSEEK_DEFAULT_MODEL", "deepseek-chat")
    provider_id = os.environ.get("DSH_PROVIDER", "default")
    api_key_env = f"DSH_{provider_id.upper().replace('-', '_')}_API_KEY"
    max_tokens = int(os.environ.get("DEEPSEEK_MAX_TOKENS", "8192"))

    provider = {
        "displayName": os.environ.get("DSH_PROVIDER_NAME", "DeepSeek"),
        "api": "openai-completions",
        "apiKeyEnv": api_key_env,
        "baseURL": base_url,
        "reasoning": "low",
        "compat": {
            "thinkingFormat": "deepseek",
            "supportsReasoningEffort": True,
            "requiresReasoningContentOnAssistantMessages": True,
            "maxTokensField": "max_tokens",
        },
        "models": [{
            "id": model,
            "name": model,
            "contextWindow": int(os.environ.get("DEEPSEEK_CONTEXT_WINDOW", "128000")),
            "maxTokens": max_tokens,
            "input": ["text", "image"],
            "reasoningEfforts": {"low": "low", "high": "high", "max": "max"},
        }],
    }
    settings = {
        "llm-pi-ai": {"providers": {provider_id: provider}},
        "agent-default-model": {
            "provider": provider_id,
            "model": model,
            "reasoningEffort": os.environ.get("DEEPSEEK_REASONING_EFFORT", "low"),
        },
        "compaction-basic": {
            "thresholdRatio": 0.9,
            "headroomTokens": 8192,
            "maxTokens": max_tokens,
        },
    }
    credentials = {"version": 1, "refs": {api_key_env: api_key}, "records": {}}
    patch = [
        {"id": "llm-pi-ai", "config": settings["llm-pi-ai"]},
        {"id": "agent-default-model", "config": settings["agent-default-model"]},
        {"id": "compaction-basic", "config": settings["compaction-basic"]},
    ]

    HOME.mkdir(parents=True, exist_ok=True)
    (HOME / ".credentials.yaml").write_text(yaml.safe_dump(credentials, sort_keys=False), encoding="utf-8")
    (HOME / "cordis.patch.yml").write_text(yaml.safe_dump(patch, sort_keys=False), encoding="utf-8")
    (HOME / "xhs-providers.yaml").write_text(yaml.safe_dump({
        "providers": {provider_id: {
            "type": "openai_compatible",
            "base_url": base_url,
            "api_key": "${DEEPSEEK_API_KEY}",
            "default_model": model,
        }}
    }, sort_keys=False), encoding="utf-8")
    print(f"Configured provider {provider_id!r} in {HOME}. Credentials remain local and are ignored by Git.")


if __name__ == "__main__":
    main()
