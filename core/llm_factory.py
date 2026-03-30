"""
Model-agnostic LLM factory.

Creates LangChain ChatModel instances from configuration,
supporting OpenAI, Anthropic, Ollama, and Google providers.
"""

import os
from typing import Optional

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel

from core.config import LLMConfig

# Ensure .env is loaded for API keys
load_dotenv()


def create_chat_model(config: LLMConfig) -> BaseChatModel:
    """
    Create a LangChain ChatModel from provider-agnostic configuration.

    Args:
        config: LLM configuration with provider, model, temperature, etc.

    Returns:
        BaseChatModel instance ready for use with LangGraph agents.

    Raises:
        ImportError: If the required provider package is not installed.
        ValueError: If the provider is not supported.
    """
    api_key = os.getenv(config.api_key_env)

    if config.provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError("Install langchain-openai: pip install langchain-openai")
        kwargs = {"model": config.model, "temperature": config.temperature}
        if api_key:
            kwargs["api_key"] = api_key
        if config.base_url:
            kwargs["base_url"] = config.base_url
        return ChatOpenAI(**kwargs)

    elif config.provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ImportError("Install langchain-anthropic: pip install langchain-anthropic")
        kwargs = {"model": config.model, "temperature": config.temperature}
        if api_key:
            kwargs["api_key"] = api_key
        return ChatAnthropic(**kwargs)

    elif config.provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError:
            raise ImportError("Install langchain-ollama: pip install langchain-ollama")
        kwargs = {"model": config.model, "temperature": config.temperature}
        if config.base_url:
            kwargs["base_url"] = config.base_url
        return ChatOllama(**kwargs)

    elif config.provider == "google":
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError:
            raise ImportError("Install langchain-google-genai: pip install langchain-google-genai")
        kwargs = {"model": config.model, "temperature": config.temperature}
        if api_key:
            kwargs["google_api_key"] = api_key
        return ChatGoogleGenerativeAI(**kwargs)

    else:
        raise ValueError(f"Unsupported LLM provider: {config.provider}")
