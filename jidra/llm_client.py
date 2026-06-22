from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import litellm
import yaml

DEFAULT_CONFIG = {
    "llm": {
        "provider": "litellm",
        "profile": "local",
        "profiles": {
            "local": {
                "api_base": "http://localhost:4000",
                "api_key_env": "LITELLM_PROXY_API_KEY",
                "default_model": "ollama/qwen2.5-coder:7b-instruct-q4_K_M",
                "timeout_seconds": 120,
                "temperature": 0.2,
                "max_tokens": 1200,
            },
            "enterprise": {
                "api_base": "https://your-enterprise-litellm.example.com",
                "api_key_env": "ENTERPRISE_LITELLM_API_KEY",
                "default_model": "gpt-4o-mini",
                "timeout_seconds": 120,
                "temperature": 0.2,
                "max_tokens": 2000,
            },
        },
    }
}


@dataclass
class JidraLLMClient:
    provider: str
    profile: str
    api_base: str | None
    api_key: str | None
    default_model: str
    timeout_seconds: float
    temperature: float
    max_tokens: int

    @classmethod
    def from_config(cls, profile: str | None = None, config_path: str | None = None):
        config = cls._load_config(config_path)
        llm_cfg = config.get("llm", {})
        provider = str(llm_cfg.get("provider", "litellm"))
        profiles = llm_cfg.get("profiles", {})
        selected_profile = profile or llm_cfg.get("profile", "local")

        selected_cfg = profiles.get(selected_profile, {})
        api_key = selected_cfg.get("api_key")
        api_key_env = selected_cfg.get("api_key_env")
        if not api_key and api_key_env:
            api_key = os.getenv(api_key_env)

        # Helpful fallback for local LiteLLM proxy setups.
        if not api_key:
            api_key = os.getenv("LITELLM_PROXY_API_KEY") or os.getenv("LITELLM_API_KEY")

        return cls(
            provider=provider,
            profile=str(selected_profile),
            api_base=selected_cfg.get("api_base"),
            api_key=api_key,
            default_model=str(
                selected_cfg.get(
                    "default_model", "ollama/qwen2.5-coder:7b-instruct-q4_K_M"
                )
            ),
            timeout_seconds=float(selected_cfg.get("timeout_seconds", 120)),
            temperature=float(selected_cfg.get("temperature", 0.2)),
            max_tokens=int(selected_cfg.get("max_tokens", 1200)),
        )

    @staticmethod
    def _load_config(config_path: str | None) -> dict[str, Any]:
        path = (
            Path(config_path).resolve()
            if config_path
            else Path(__file__).resolve().parent / "config.yaml"
        )
        if not path.exists():
            return DEFAULT_CONFIG
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                return DEFAULT_CONFIG
            return data
        except Exception:
            return DEFAULT_CONFIG

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(0, len(text) // 4)

    @staticmethod
    def _get_field(obj: Any, key: str, default=None):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def generate_diagnosis(self, prompt: str, model: str | None = None) -> dict:
        selected_model = model or self.default_model
        started = time.monotonic()

        kwargs = {
            "model": selected_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are JIDRA, a Java diagnostic reasoning assistant. "
                        "Use only the provided graph-grounded context. "
                        "Be explicit about uncertainty. "
                        "Do not invent missing call edges."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout": self.timeout_seconds,
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base
            kwargs["custom_llm_provider"] = "openai"
        if self.api_key:
            kwargs["api_key"] = self.api_key

        response = litellm.completion(**kwargs)
        latency_seconds = time.monotonic() - started

        choices = self._get_field(response, "choices", [])
        first_choice = choices[0] if isinstance(choices, list) and choices else {}
        text = self._get_field(
            self._get_field(first_choice, "message", {}), "content", ""
        )

        usage = self._get_field(response, "usage")
        prompt_tokens = self._get_field(usage, "prompt_tokens")
        completion_tokens = self._get_field(usage, "completion_tokens")
        total_tokens = self._get_field(usage, "total_tokens")
        completion_details = self._get_field(usage, "completion_tokens_details", {})
        reasoning_tokens = (
            self._get_field(completion_details, "reasoning_tokens", 0) or 0
        )

        usage_out = {
            "input_tokens": int(prompt_tokens or 0),
            "output_tokens": int(completion_tokens or 0),
            "total_tokens": int(total_tokens or 0),
            "reasoning_tokens": int(reasoning_tokens),
        }

        if not usage or (
            usage_out["input_tokens"] == 0
            and usage_out["output_tokens"] == 0
            and usage_out["total_tokens"] == 0
        ):
            in_est = self._estimate_tokens(prompt)
            out_est = self._estimate_tokens(text or "")
            usage_out = {
                "input_tokens": in_est,
                "output_tokens": out_est,
                "total_tokens": in_est + out_est,
                "reasoning_tokens": 0,
                "estimated": True,
            }

        return {
            "text": text,
            "model": selected_model,
            "provider": self.provider,
            "profile": self.profile,
            "usage": usage_out,
            "latency_seconds": round(latency_seconds, 3),
        }
