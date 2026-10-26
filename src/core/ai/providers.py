import json
import logging
import os
from abc import ABC, abstractmethod

import anthropic
from google import genai
from google.genai import types
import openai

from src.core.config import CONFIGS_DIR, INPUTS_DIR, load_env_keys

load_api_keys = load_env_keys


logger = logging.getLogger(__name__)

DEFAULT_AI_MODELS_CONFIG = {
    "active_provider": "gemini",
    "models": {
        "gemini": "gemini-4-argon",
        "chatgpt": "gpt-6-astra",
        "claude": "claude-fable-5-1",
    },
    "reasoning_effort": {
        "chatgpt": "max",
    },
}

PROVIDER_KEY_MAP = {
    "gemini": ("GEMINI_API_KEY", "gemini", "gemini-4-argon"),
    "chatgpt": ("OPENAI_API_KEY", "chatgpt", "gpt-6-astra"),
    "openai": ("OPENAI_API_KEY", "chatgpt", "gpt-6-astra"),
    "claude": ("ANTHROPIC_API_KEY", "claude", "claude-fable-5-1"),
    "anthropic": ("ANTHROPIC_API_KEY", "claude", "claude-fable-5-1"),
}


class BaseAIProvider(ABC):
    """Abstract base class for AI providers generating OpenSCAD JSON responses."""

    provider_name: str = "ai"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    @abstractmethod
    def generate_content(self, prompt: str, system_instructions: str = "") -> str:
        """Send prompt and system instructions to the AI provider and return raw text."""
        raise NotImplementedError


class GeminiProvider(BaseAIProvider):
    """Google Gemini provider implementation via google-genai."""

    provider_name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-4-argon"):
        super().__init__(api_key=api_key, model=model)
        if api_key:
            os.environ["GEMINI_API_KEY"] = api_key
        try:
            self.client = genai.Client(api_key=api_key) if api_key else genai.Client()
        except TypeError:
            self.client = genai.Client()

    def generate_content(self, prompt: str, system_instructions: str = "") -> str:
        config = types.GenerateContentConfig(
            system_instruction=system_instructions,
            response_mime_type="application/json",
        )
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )
        return (response.text or "").strip()


class OpenAIProvider(BaseAIProvider):
    """OpenAI ChatGPT provider implementation via openai-python."""

    provider_name = "chatgpt"

    def __init__(
        self, api_key: str, model: str = "gpt-6-astra", reasoning_effort: str | None = "max"
    ):
        super().__init__(api_key=api_key, model=model)
        self.reasoning_effort = reasoning_effort
        self.client = openai.OpenAI(api_key=api_key)

    def generate_content(self, prompt: str, system_instructions: str = "") -> str:
        messages = []
        if system_instructions:
            messages.append({"role": "system", "content": system_instructions})
        user_content = prompt
        if not system_instructions and "json" not in user_content.lower():
            user_content = f"{user_content}\n\nRespond with valid JSON."
        messages.append({"role": "user", "content": user_content})

        kwargs = {
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort

        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception:
            if "reasoning_effort" in kwargs:
                kwargs.pop("reasoning_effort", None)
                response = self.client.chat.completions.create(**kwargs)
            else:
                raise

        choice = response.choices[0]
        content = choice.message.content or ""
        return content.strip()


class AnthropicProvider(BaseAIProvider):
    """Anthropic Claude provider implementation via anthropic-python."""

    provider_name = "claude"

    def __init__(self, api_key: str, model: str = "claude-fable-5-1"):
        super().__init__(api_key=api_key, model=model)
        self.client = anthropic.Anthropic(api_key=api_key)

    def generate_content(self, prompt: str, system_instructions: str = "") -> str:
        kwargs = {
            "model": self.model,
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_instructions:
            kwargs["system"] = system_instructions

        response = self.client.messages.create(**kwargs)
        blocks = [str(block.text) for block in response.content if hasattr(block, "text")]
        if blocks:
            return "\n".join(blocks).strip()
        return ""


def load_provider_config(config_path: str | None = None) -> dict:
    """Load AI provider and model configuration from inputs/configs/ai_models.json."""
    default_cfg = os.path.join(CONFIGS_DIR, "ai_models.json")
    legacy_cfg = os.path.join(INPUTS_DIR, "ai_models.json")
    path = config_path or (default_cfg if os.path.exists(default_cfg) else legacy_cfg)

    config = {
        "active_provider": DEFAULT_AI_MODELS_CONFIG["active_provider"],
        "models": dict(DEFAULT_AI_MODELS_CONFIG["models"]),
        "reasoning_effort": dict(DEFAULT_AI_MODELS_CONFIG["reasoning_effort"]),
    }

    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "active_provider" in data and isinstance(data["active_provider"], str):
                    config["active_provider"] = data["active_provider"].strip().lower()
                if "models" in data and isinstance(data["models"], dict):
                    for k, v in data["models"].items():
                        if isinstance(v, str) and v.strip():
                            config["models"][k.strip().lower()] = v.strip()
                if "reasoning_effort" in data and isinstance(data["reasoning_effort"], dict):
                    for k, v in data["reasoning_effort"].items():
                        if isinstance(v, str) and v.strip():
                            config["reasoning_effort"][k.strip().lower()] = v.strip()
        except Exception as e:
            logger.warning(f"Failed to parse ai_models.json ({e}), using defaults.")

    return config


def get_active_ai_provider(
    config_path: str | None = None, env_path: str | None = None
) -> BaseAIProvider:
    """Instantiate and return the configured active AI provider after validating its API key."""
    cfg = load_provider_config(config_path=config_path)
    active_provider = cfg["active_provider"].lower()

    if active_provider not in PROVIDER_KEY_MAP:
        raise ValueError(
            f"Unsupported AI provider '{active_provider}' in ai_models.json. "
            "Supported providers: gemini, chatgpt, claude."
        )

    key_name, canonical_provider, default_model = PROVIDER_KEY_MAP[active_provider]
    models_dict = cfg.get("models", {})
    model_name = (
        models_dict.get(active_provider) or models_dict.get(canonical_provider) or default_model
    )

    keys = load_api_keys(env_path=env_path)
    api_key = keys.get(key_name, "").strip()
    if not api_key:
        raise ValueError(
            f"Active AI provider is '{active_provider}', but {key_name} is not set in .env."
        )

    if canonical_provider == "gemini":
        return GeminiProvider(api_key=api_key, model=model_name)
    elif canonical_provider == "chatgpt":
        effort_dict = cfg.get("reasoning_effort", {})
        effort = effort_dict.get(active_provider) or effort_dict.get(canonical_provider)
        return OpenAIProvider(api_key=api_key, model=model_name, reasoning_effort=effort)
    elif canonical_provider == "claude":
        return AnthropicProvider(api_key=api_key, model=model_name)

    raise ValueError(f"Unsupported AI provider '{active_provider}'.")
