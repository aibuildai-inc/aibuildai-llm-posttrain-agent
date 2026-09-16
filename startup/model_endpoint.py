"""Set the model endpoint for one scoped call: the one owner of the bundled binary's endpoint environment."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urlparse

from config import LlmConfig, endpoint_disallowed_tools
from infra.model_catalog import endpoint_is_anthropic

# The credential is not in this table. Which variable carries it depends on
# the endpoint, so the endpoint resolution below chooses that variable.
_USER_VAR_TO_INTERNAL = {
    "AIBUILDAI_SMALL_FAST_MODEL": "ANTHROPIC_SMALL_FAST_MODEL",
    "AIBUILDAI_HAIKU_MODEL": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "AIBUILDAI_SONNET_MODEL": "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "AIBUILDAI_OPUS_MODEL": "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "AIBUILDAI_SUBAGENT_MODEL": "CLAUDE_CODE_SUBAGENT_MODEL",
}
# Every endpoint variable the bundled binary reads. Each is set for the call
# and put back after it; one this endpoint does not use is set to "", so a
# stale host export never rides beside the chosen endpoint or credential.
_PROVIDER_ENV = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK",
)
_SCOPED_ENV = frozenset({*_PROVIDER_ENV, *_USER_VAR_TO_INTERNAL.values()})


def _endpoint_env(
    llm: LlmConfig, *, base_url: str | None, auth_token: str | None
) -> dict[str, str]:
    """Resolve the run's primary model to the binary's endpoint environment.

    ``llm.endpoint_provider`` is the configured model's provider; an explicit ``base_url`` overrides the provider's own endpoint. The credential variable follows the endpoint: the binary reads its two credential variables two different ways, an Anthropic endpoint takes an API KEY (x-api-key), every other endpoint takes a bearer token. Measured with deliberately invalid values, so each answer names the header the value went out in:
      ANTHROPIC_API_KEY=<anything>        -> 401 API key is invalid
      ANTHROPIC_AUTH_TOKEN=sk-ant-oat01-* -> 401 OAuth access token is invalid
      ANTHROPIC_AUTH_TOKEN=<other>        -> 401 Invalid bearer token
      ANTHROPIC_AUTH_TOKEN=sk-ant-api03-* -> Not logged in - Please run /login
    The last line is why an Anthropic endpoint cannot use the bearer variable: every Anthropic API key is sk-ant-api03 shaped, and the binary refuses one offered as a bearer token in silence, reporting a machine that holds a good key as not signed in."""
    provider = llm.endpoint_provider
    env = {name: "" for name in _PROVIDER_ENV}
    credential = auth_token.strip() if auth_token else None
    if base_url is not None:
        normalized_url = base_url.strip().rstrip("/")
        if not normalized_url:
            raise ValueError("base_url must not be empty")
        if not credential:
            raise ValueError("custom endpoint requires an auth token")
        env["ANTHROPIC_BASE_URL"] = normalized_url
    elif provider == "openai":
        raise ValueError(
            "a bare gpt-* model needs an Anthropic-compatible endpoint that "
            "serves it: export AIBUILDAI_BASE_URL with that endpoint and "
            "AIBUILDAI_API_KEY with its key"
        )
    elif provider == "openrouter":
        if not credential:
            raise ValueError("OpenRouter endpoint requires an auth token")
        env["ANTHROPIC_BASE_URL"] = "https://openrouter.ai/api"
    elif provider == "deepseek":
        if not credential:
            raise ValueError("DeepSeek endpoint requires an auth token")
        env["ANTHROPIC_BASE_URL"] = "https://api.deepseek.com/anthropic"
    # Anthropic direct: no base URL, the SDK default. No credential there
    # means the binary's own Claude Code login.
    anthropic = endpoint_is_anthropic(env["ANTHROPIC_BASE_URL"])
    if credential:
        env["ANTHROPIC_API_KEY" if anthropic else "ANTHROPIC_AUTH_TOKEN"] = credential
    if env["ANTHROPIC_BASE_URL"] and not anthropic:
        env["CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK"] = "1"
    return env


@contextmanager
def model_endpoint(llm: LlmConfig) -> Iterator[None]:
    """Set the Claude-side endpoint for one call, then put the old values back.

    Everything here is for the bundled Claude binary: the base URL it reads, the ``ANTHROPIC_*`` model and credential names, and the checks on the resulting endpoint. This scope is the whole lifecycle of that endpoint: it chooses it, applies it, and restores the environment.
    """
    old_env = {name: os.environ.get(name) for name in _SCOPED_ENV}
    try:
        provider = llm.endpoint_provider
        base = os.environ.get("AIBUILDAI_BASE_URL", "").strip()
        if not base and provider != "openai":
            base = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
        configured_token = (
            os.environ.get("AIBUILDAI_API_KEY", "").strip()
            or os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
            or os.environ.get("ANTHROPIC_API_KEY", "").strip()
            or None
        )
        env = _endpoint_env(llm, base_url=base or None, auth_token=configured_token)
        os.environ.update(env)

        for user_var, internal_var in _USER_VAR_TO_INTERNAL.items():
            if value := os.environ.get(user_var):
                os.environ[internal_var] = value
        if provider == "openai":
            for name in (
                "ANTHROPIC_SMALL_FAST_MODEL",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                "ANTHROPIC_DEFAULT_SONNET_MODEL",
                "ANTHROPIC_DEFAULT_OPUS_MODEL",
                "CLAUDE_CODE_SUBAGENT_MODEL",
            ):
                os.environ.setdefault(name, llm.default.model)
        if provider == "deepseek" and not os.environ.get("ANTHROPIC_SMALL_FAST_MODEL"):
            os.environ["ANTHROPIC_SMALL_FAST_MODEL"] = "deepseek-v4-flash"

        base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
        if provider != "anthropic" and not base_url:
            # Nothing above had a branch for this provider, so the endpoint was
            # never chosen. An empty base means the SDK default, Anthropic, so
            # without this the run would quietly send this provider's model ids
            # to Anthropic and fail one call at a time.
            raise ValueError(
                f"the configured model targets {provider!r}, which needs its own "
                "endpoint, but none is set. Export AIBUILDAI_BASE_URL with that "
                "provider's Anthropic-compatible base URL."
            )
        host = (urlparse(base_url).hostname or "").lower() if base_url else ""
        if provider == "anthropic" and any(
            host == known or host.endswith("." + known)
            for known in ("deepseek.com", "openrouter.ai")
        ):
            print(
                "[endpoint] warning: models are claude ids but "
                f"ANTHROPIC_BASE_URL points at {host!r} (a stale export from a "
                "previous deepseek/openrouter run?). Every session will hit "
                "that endpoint and fail on claude model ids; "
                "`unset ANTHROPIC_BASE_URL` if unintended.",
                file=sys.stderr,
            )
        if denied := endpoint_disallowed_tools(llm):
            print(
                f"[endpoint] warning: this endpoint does not serve "
                f"{sorted(denied)}, so no role gets them; agents reach the web "
                "through Bash instead. Set AIBUILDAI_BASE_URL to an endpoint "
                "that serves them to keep them.",
                file=sys.stderr,
            )
        yield
    finally:
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
