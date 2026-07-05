import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from openai import APIError, APIStatusError, AsyncOpenAI

DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_GEMMA_MODEL = "google/gemma-4-12b-qat"
DEFAULT_FASTCONTEXT_MODEL = "fastcontext-1.0-4b-rl"
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_GEMMA_CONTEXT_LENGTH = 32_768
DEFAULT_GEMMA_GPU = "max"
DEFAULT_FASTCONTEXT_CONTEXT_LENGTH = 65_536
DEFAULT_FASTCONTEXT_GPU = "max"
LMS_EXE = r"C:\Users\Nikla\.lmstudio\bin\lms.exe"
ModelRole = Literal["gemma", "fastcontext"]


class LMStudioError(RuntimeError):
    pass


@dataclass(frozen=True)
class LMStudioConfig:
    base_url: str = DEFAULT_BASE_URL
    gemma_model: str = DEFAULT_GEMMA_MODEL
    fastcontext_model: str = DEFAULT_FASTCONTEXT_MODEL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class LMStudioToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw: dict[str, Any]
    arguments_error: str | None = None


@dataclass(frozen=True)
class LMStudioChatCompletion:
    content: str
    tool_calls: list[LMStudioToolCall]
    finish_reason: str | None
    message: dict[str, Any]
    raw: dict[str, Any]
    output_items: list[dict[str, Any]] = field(default_factory=list)
    response_id: str | None = None


def get_config() -> LMStudioConfig:
    return LMStudioConfig(
        base_url=os.environ.get("LM_STUDIO_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        gemma_model=os.environ.get("SOURCE_SCOUT_GEMMA_MODEL", DEFAULT_GEMMA_MODEL),
        fastcontext_model=os.environ.get("SOURCE_SCOUT_FASTCONTEXT_MODEL", DEFAULT_FASTCONTEXT_MODEL),
        timeout_seconds=_get_timeout(),
    )


def _get_timeout() -> float:
    raw = os.environ.get("SOURCE_SCOUT_LMSTUDIO_TIMEOUT")
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return max(1.0, float(raw))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def list_downloaded_models() -> list[dict[str, Any]]:
    return _run_lms_json(["ls", "--json"])


def list_loaded_models() -> list[dict[str, Any]]:
    return _run_lms_json(["ps", "--json"])


def load_fastcontext_model(
    config: LMStudioConfig | None = None,
    context_length: int = DEFAULT_FASTCONTEXT_CONTEXT_LENGTH,
    gpu: str = DEFAULT_FASTCONTEXT_GPU,
) -> dict[str, Any]:
    return load_configured_model(
        "fastcontext",
        config=config,
        context_length=context_length,
        gpu=gpu,
    )


def load_gemma_model(
    config: LMStudioConfig | None = None,
    context_length: int = DEFAULT_GEMMA_CONTEXT_LENGTH,
    gpu: str = DEFAULT_GEMMA_GPU,
) -> dict[str, Any]:
    return load_configured_model(
        "gemma",
        config=config,
        context_length=context_length,
        gpu=gpu,
    )


def load_configured_model(
    role: ModelRole,
    config: LMStudioConfig | None = None,
    context_length: int | None = None,
    gpu: str | None = None,
) -> dict[str, Any]:
    active = config or get_config()
    model_id = model_id_for_role(active, role)
    effective_context_length = (
        context_length if context_length is not None else default_context_length_for_role(role)
    )
    effective_gpu = gpu if gpu is not None else default_gpu_for_role(role)
    state = model_inventory(active)["configured_models"].get(role, {})
    if state.get("loaded"):
        _run_lms(["unload", model_id], {"model_id": model_id})
    return _run_lms(
        [
            "load",
            model_id,
            "--context-length",
            str(effective_context_length),
            "--gpu",
            effective_gpu,
            "--identifier",
            model_id,
        ],
        {
            "model_id": model_id,
            "context_length": effective_context_length,
            "gpu": effective_gpu,
        },
    )


def model_id_for_role(config: LMStudioConfig, role: ModelRole) -> str:
    if role == "gemma":
        return config.gemma_model
    return config.fastcontext_model


def default_context_length_for_role(role: ModelRole) -> int:
    if role == "gemma":
        return DEFAULT_GEMMA_CONTEXT_LENGTH
    return DEFAULT_FASTCONTEXT_CONTEXT_LENGTH


def default_gpu_for_role(role: ModelRole) -> str:
    if role == "gemma":
        return DEFAULT_GEMMA_GPU
    return DEFAULT_FASTCONTEXT_GPU


def model_inventory(config: LMStudioConfig | None = None) -> dict[str, Any]:
    active = config or get_config()
    downloaded_models = list_downloaded_models()
    loaded_models = list_loaded_models()
    downloaded_ids = _model_id_set(downloaded_models)
    loaded_ids = _model_id_set(loaded_models)
    return {
        "downloaded_models": sorted(downloaded_ids),
        "loaded_models": sorted(loaded_ids),
        "configured_models": {
            "gemma": _model_state(active.gemma_model, downloaded_ids, loaded_ids, loaded_models),
            "fastcontext": _model_state(
                active.fastcontext_model,
                downloaded_ids,
                loaded_ids,
                loaded_models,
            ),
        },
    }


def _run_lms_json(args: list[str]) -> list[dict[str, Any]]:
    result = _run_lms(args, {})
    try:
        parsed = json.loads(str(result["stdout"] or "[]"))
    except json.JSONDecodeError as exc:
        raise LMStudioError(f"lms {' '.join(args)} returned invalid JSON.") from exc
    if not isinstance(parsed, list):
        raise LMStudioError(f"lms {' '.join(args)} returned an unexpected JSON shape.")
    return [item for item in parsed if isinstance(item, dict)]


def _run_lms(args: list[str], extra: dict[str, Any]) -> dict[str, Any]:
    command = [LMS_EXE, *args]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LMStudioError(f"Failed to run {' '.join(command)}.") from exc
    return {
        "command": " ".join(command),
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        **extra,
    }


def _model_id_set(models: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for model in models:
        for key in (
            "modelKey",
            "identifier",
            "indexedModelIdentifier",
            "path",
            "selectedVariant",
        ):
            value = model.get(key)
            if isinstance(value, str) and value:
                ids.add(value)
    return ids


def _model_state(
    model_id: str,
    downloaded_ids: set[str],
    loaded_ids: set[str],
    loaded_models: list[dict[str, Any]],
) -> dict[str, Any]:
    loaded_model = _find_model(model_id, loaded_models)
    detail = _loaded_model_detail(loaded_model) if loaded_model else None
    return {
        "model_id": model_id,
        "downloaded": model_id in downloaded_ids,
        "loaded": model_id in loaded_ids,
        "loaded_detail": detail,
    }


def _find_model(model_id: str, models: list[dict[str, Any]]) -> dict[str, Any] | None:
    for model in models:
        if model_id in _model_id_set([model]):
            return model
    return None


def _loaded_model_detail(model: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "modelKey",
        "identifier",
        "displayName",
        "path",
        "contextLength",
        "maxContextLength",
        "status",
        "parallel",
        "queued",
        "ttlMs",
        "architecture",
        "paramsString",
    ]
    return {key: model[key] for key in keys if key in model}


async def list_models(
    config: LMStudioConfig | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[str]:
    active = config or get_config()
    try:
        async with httpx.AsyncClient(timeout=active.timeout_seconds, transport=transport) as client:
            response = await client.get(f"{active.base_url}/models")
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise LMStudioError(
            f"LM Studio is unreachable at {active.base_url}. "
            "Start it with 'lms server start' or the LM Studio Local Server UI."
        ) from exc

    data = response.json()
    models = data.get("data", [])
    if not isinstance(models, list):
        raise LMStudioError("LM Studio returned an unexpected /v1/models response.")
    return [str(model["id"]) for model in models if isinstance(model, dict) and model.get("id")]


async def validate_models(
    config: LMStudioConfig | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    active = config or get_config()
    models = await list_models(active, transport=transport)
    return {
        "base_url": active.base_url,
        "models": models,
        "gemma_model": active.gemma_model,
        "fastcontext_model": active.fastcontext_model,
        "gemma_available": active.gemma_model in models,
        "fastcontext_available": active.fastcontext_model in models,
    }


async def chat_json(
    model_id: str,
    messages: list[dict[str, Any]],
    config: LMStudioConfig | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    max_tokens: int = 1600,
    temperature: float = 0.1,
    attempts: int = 2,
    response_format: dict[str, Any] | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    last_error: LMStudioError | None = None
    for _attempt in range(max(1, attempts)):
        try:
            content = await chat_text(
                model_id=model_id,
                messages=messages,
                config=config,
                transport=transport,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
                seed=seed,
            )
            return parse_json_content(content)
        except LMStudioError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise LMStudioError("LM Studio response failed.")


async def chat_text(
    model_id: str,
    messages: list[dict[str, Any]],
    config: LMStudioConfig | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    max_tokens: int = 1600,
    temperature: float = 0.1,
    response_format: dict[str, Any] | None = None,
    seed: int | None = None,
) -> str:
    completion = await chat_completion(
        model_id=model_id,
        messages=messages,
        config=config,
        transport=transport,
        max_tokens=max_tokens,
        temperature=temperature,
        response_format=response_format,
        seed=seed,
    )
    if not completion.content.strip():
        raise LMStudioError("LM Studio returned an empty response.")
    return completion.content


async def chat_completion(
    model_id: str,
    messages: list[dict[str, Any]],
    config: LMStudioConfig | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    max_tokens: int = 1600,
    temperature: float = 0.1,
    response_format: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    extra_body: dict[str, Any] | None = None,
    seed: int | None = None,
) -> LMStudioChatCompletion:
    active = config or get_config()
    payload: dict[str, Any] = {
        "model": model_id,
        "input": messages,
        "temperature": temperature,
        "max_output_tokens": max_tokens,
    }
    if response_format is not None:
        payload["text"] = {"format": _responses_text_format(response_format)}
    if tools is not None:
        payload["tools"] = _responses_tools(tools)
    if tool_choice is not None:
        payload["tool_choice"] = _responses_tool_choice(tool_choice)
    request_extra_body = dict(extra_body or {})
    if seed is not None:
        request_extra_body["seed"] = seed

    try:
        async with _openai_client(active, transport) as client:
            response = await client.responses.create(
                **payload,
                extra_body=request_extra_body or None,
            )
    except APIStatusError as exc:
        detail = exc.response.text.strip()
        suffix = f" Response: {detail[:500]}" if detail else ""
        raise LMStudioError(
            f"LM Studio response failed for model '{model_id}'.{suffix}"
        ) from exc
    except APIError as exc:
        raise LMStudioError(f"LM Studio response failed for model '{model_id}'.") from exc

    return _extract_response_completion(response.model_dump(mode="json"))


def _openai_client(
    config: LMStudioConfig,
    transport: httpx.AsyncBaseTransport | None,
) -> AsyncOpenAI:
    http_client = httpx.AsyncClient(timeout=config.timeout_seconds, transport=transport)
    return AsyncOpenAI(
        base_url=config.base_url,
        api_key=os.environ.get("LM_STUDIO_API_KEY", "lm-studio"),
        timeout=config.timeout_seconds,
        max_retries=0,
        http_client=http_client,
    )


def _responses_text_format(response_format: dict[str, Any]) -> dict[str, Any]:
    if response_format.get("type") != "json_schema":
        return dict(response_format)
    json_schema = response_format.get("json_schema")
    if not isinstance(json_schema, dict):
        return dict(response_format)
    return {"type": "json_schema", **json_schema}


def _responses_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function")
        if tool.get("type") == "function" and isinstance(function, dict):
            converted_tool: dict[str, Any] = {
                "type": "function",
                "name": str(function.get("name", "")),
            }
            for key in ("description", "parameters", "strict"):
                if key in function:
                    converted_tool[key] = function[key]
            converted.append(converted_tool)
        else:
            converted.append(dict(tool))
    return converted


def _responses_tool_choice(tool_choice: str | dict[str, Any]) -> str | dict[str, Any]:
    if not isinstance(tool_choice, dict):
        return tool_choice
    function = tool_choice.get("function")
    if tool_choice.get("type") == "function" and isinstance(function, dict):
        name = function.get("name")
        if isinstance(name, str) and name:
            return {"type": "function", "name": name}
    return dict(tool_choice)


def _extract_response_completion(data: dict[str, Any]) -> LMStudioChatCompletion:
    if "choices" in data and not data.get("output"):
        return _extract_chat_completion(data)
    output_items = _response_output_items(data)
    content_text = _response_output_text(output_items)
    tool_calls = _extract_response_tool_calls(output_items)
    finish_reason = "tool_calls" if tool_calls else _response_finish_reason(data)
    message = {
        "role": "assistant",
        "content": content_text or None,
        "tool_calls": [call.raw for call in tool_calls],
    }
    return LMStudioChatCompletion(
        content=content_text,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        message=message,
        raw=data,
        output_items=output_items,
        response_id=str(data["id"]) if data.get("id") else None,
    )


def _response_output_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    output = data.get("output")
    if not isinstance(output, list):
        raise LMStudioError("LM Studio returned no response output items.")
    return [item for item in output if isinstance(item, dict)]


def _response_output_text(output_items: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in output_items:
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if isinstance(content, str):
            parts.append(content)
            continue
        if not isinstance(content, list):
            continue
        for content_item in content:
            if not isinstance(content_item, dict):
                continue
            text = content_item.get("text")
            if content_item.get("type") in {"output_text", "text"} and isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _extract_response_tool_calls(output_items: list[dict[str, Any]]) -> list[LMStudioToolCall]:
    calls: list[LMStudioToolCall] = []
    for index, item in enumerate(output_items):
        if item.get("type") != "function_call":
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        arguments, arguments_error = _parse_tool_arguments(item.get("arguments"))
        call_id = item.get("call_id") or item.get("id")
        calls.append(
            LMStudioToolCall(
                id=str(call_id or f"tool-call-{index + 1}"),
                name=name,
                arguments=arguments,
                raw=item,
                arguments_error=arguments_error,
            )
        )
    return calls


def _response_finish_reason(data: dict[str, Any]) -> str | None:
    status = data.get("status")
    if isinstance(status, str) and status:
        return status
    incomplete_details = data.get("incomplete_details")
    if isinstance(incomplete_details, dict):
        reason = incomplete_details.get("reason")
        if isinstance(reason, str) and reason:
            return reason
    return None


def _extract_chat_completion(data: dict[str, Any]) -> LMStudioChatCompletion:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LMStudioError("LM Studio returned no legacy chat-compatible choices.")
    first = choices[0]
    if not isinstance(first, dict):
        raise LMStudioError("LM Studio returned an invalid legacy chat-compatible choice.")
    message = first.get("message")
    if not isinstance(message, dict):
        raise LMStudioError("LM Studio returned an invalid legacy chat-compatible message.")
    content = message.get("content")
    if content is None:
        content_text = ""
    elif isinstance(content, str):
        content_text = content
    else:
        raise LMStudioError("LM Studio returned non-text legacy chat-compatible content.")
    return LMStudioChatCompletion(
        content=content_text,
        tool_calls=_extract_chat_tool_calls(message),
        finish_reason=str(first["finish_reason"]) if first.get("finish_reason") is not None else None,
        message=message,
        raw=data,
    )


def _extract_chat_tool_calls(message: dict[str, Any]) -> list[LMStudioToolCall]:
    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []
    calls: list[LMStudioToolCall] = []
    for index, raw_call in enumerate(raw_calls):
        if not isinstance(raw_call, dict):
            continue
        function = raw_call.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        arguments, arguments_error = _parse_tool_arguments(function.get("arguments"))
        call_id = raw_call.get("id")
        calls.append(
            LMStudioToolCall(
                id=str(call_id or f"tool-call-{index + 1}"),
                name=name,
                arguments=arguments,
                raw=raw_call,
                arguments_error=arguments_error,
            )
        )
    return calls


def _parse_tool_arguments(raw_arguments: Any) -> tuple[dict[str, Any], str | None]:
    if raw_arguments is None:
        return {}, None
    if isinstance(raw_arguments, dict):
        return raw_arguments, None
    if not isinstance(raw_arguments, str):
        return {}, f"Unexpected tool arguments type: {type(raw_arguments).__name__}"
    if not raw_arguments.strip():
        return {}, None
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        return {}, f"Invalid tool arguments JSON: {exc.msg}"
    if not isinstance(parsed, dict):
        return {}, "Tool arguments JSON must be an object."
    return parsed, None


def parse_json_content(content: str) -> dict[str, Any]:
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = _parse_embedded_json(text)

    if not isinstance(parsed, dict):
        raise LMStudioError("Expected LM Studio to return a JSON object.")
    return parsed


def _parse_embedded_json(text: str) -> Any:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(text[index:])
            return parsed
        except json.JSONDecodeError:
            continue
    raise LMStudioError("Could not parse JSON object from LM Studio response.")


def start_server(
    config: LMStudioConfig | None = None,
    startup_timeout_seconds: float = 30.0,
) -> bool:
    active = config or get_config()
    command = _server_start_command(active)
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
    except OSError as exc:
        raise LMStudioError(f"Failed to start LM Studio server via {LMS_EXE}.") from exc

    deadline = time.monotonic() + startup_timeout_seconds
    while time.monotonic() < deadline:
        if _server_reachable(active):
            return True
        time.sleep(0.5)

    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    raise LMStudioError(
        f"Failed to start LM Studio server via {LMS_EXE} within "
        f"{startup_timeout_seconds:.0f} seconds."
    )


def _server_start_command(config: LMStudioConfig) -> list[str]:
    parsed = urlparse(config.base_url)
    command = [LMS_EXE, "server", "start"]
    if parsed.port is not None:
        command.extend(["--port", str(parsed.port)])
    if parsed.hostname:
        command.extend(["--bind", parsed.hostname])
    return command


def _server_reachable(config: LMStudioConfig) -> bool:
    try:
        with httpx.Client(timeout=2.0) as client:
            response = client.get(f"{config.base_url}/models")
            response.raise_for_status()
    except httpx.HTTPError:
        return False
    return True
