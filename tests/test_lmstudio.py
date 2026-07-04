import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from source_scout import catalog, lmstudio, pipeline, profiler


def _response_message_json(content: str) -> dict[str, Any]:
    return {
        "id": "resp-1",
        "object": "response",
        "created_at": 0,
        "model": "test-model",
        "output": [
            {
                "id": "msg-1",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": content, "annotations": []}],
            }
        ],
        "parallel_tool_calls": False,
        "status": "completed",
        "text": {"format": {"type": "text"}},
    }


def _response_tool_call_json(
    *,
    name: str = "Read",
    arguments: str = '{"path":"src/app.py","offset":3,"limit":20}',
    call_id: str = "call-1",
) -> dict[str, Any]:
    return {
        "id": "resp-1",
        "object": "response",
        "created_at": 0,
        "model": "test-model",
        "output": [
            {
                "id": "fc-1",
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
                "status": "completed",
            }
        ],
        "parallel_tool_calls": True,
        "status": "completed",
        "text": {"format": {"type": "text"}},
    }


def test_lmstudio_config_defaults(monkeypatch) -> None:
    monkeypatch.delenv("LM_STUDIO_BASE_URL", raising=False)
    monkeypatch.delenv("SOURCE_SCOUT_GEMMA_MODEL", raising=False)
    monkeypatch.delenv("SOURCE_SCOUT_FASTCONTEXT_MODEL", raising=False)
    monkeypatch.delenv("SOURCE_SCOUT_LMSTUDIO_TIMEOUT", raising=False)

    config = lmstudio.get_config()
    assert config.base_url == "http://127.0.0.1:1234/v1"
    assert config.gemma_model == "google/gemma-4-12b-qat"
    assert config.fastcontext_model == "fastcontext-1.0-4b-rl"
    assert config.timeout_seconds == 120.0


def test_lmstudio_config_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("LM_STUDIO_BASE_URL", "http://localhost:9999/v1/")
    monkeypatch.setenv("SOURCE_SCOUT_GEMMA_MODEL", "gemma-local")
    monkeypatch.setenv("SOURCE_SCOUT_FASTCONTEXT_MODEL", "fastcontext-local")
    monkeypatch.setenv("SOURCE_SCOUT_LMSTUDIO_TIMEOUT", "7")

    config = lmstudio.get_config()
    assert config.base_url == "http://localhost:9999/v1"
    assert config.gemma_model == "gemma-local"
    assert config.fastcontext_model == "fastcontext-local"
    assert config.timeout_seconds == 7.0


def test_start_server_uses_non_blocking_lms_command(monkeypatch) -> None:
    calls: list[list[str]] = []

    class FakeProcess:
        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            raise AssertionError("reachable server should not terminate process")

    def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
        calls.append(command)
        assert kwargs["stdin"] is lmstudio.subprocess.DEVNULL
        assert kwargs["stdout"] is lmstudio.subprocess.DEVNULL
        assert kwargs["stderr"] is lmstudio.subprocess.DEVNULL
        return FakeProcess()

    monkeypatch.setattr(lmstudio.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(lmstudio, "_server_reachable", lambda config: True)

    assert lmstudio.start_server(
        lmstudio.LMStudioConfig(base_url="http://127.0.0.1:1234/v1")
    )
    assert calls == [
        [
            lmstudio.LMS_EXE,
            "server",
            "start",
            "--port",
            "1234",
            "--bind",
            "127.0.0.1",
        ]
    ]


def test_start_server_cleans_up_when_api_never_becomes_reachable(monkeypatch) -> None:
    terminated = False

    class FakeProcess:
        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            nonlocal terminated
            terminated = True

        def wait(self, timeout: float) -> None:
            return None

    monkeypatch.setattr(lmstudio.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(lmstudio, "_server_reachable", lambda config: False)

    with pytest.raises(lmstudio.LMStudioError, match="within 0 seconds"):
        lmstudio.start_server(startup_timeout_seconds=0)

    assert terminated is True


@pytest.mark.asyncio
async def test_list_models_parses_openai_compatible_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": "model-a"}, {"id": "model-b"}]})

    transport = httpx.MockTransport(handler)
    models = await lmstudio.list_models(transport=transport)
    assert models == ["model-a", "model-b"]


def test_model_inventory_distinguishes_downloaded_and_loaded(monkeypatch) -> None:
    def fake_run(command: list[str], **kwargs: Any) -> object:
        assert kwargs["check"] is True
        assert kwargs["capture_output"] is True
        if command[1:] == ["ls", "--json"]:
            stdout = json.dumps(
                [
                    {"modelKey": "google/gemma-4-12b-qat"},
                    {"modelKey": "fastcontext-1.0-4b-rl"},
                ]
            )
        elif command[1:] == ["ps", "--json"]:
            stdout = json.dumps(
                [
                    {
                        "modelKey": "fastcontext-1.0-4b-rl",
                        "identifier": "fastcontext-1.0-4b-rl",
                        "contextLength": 65536,
                        "status": "idle",
                        "parallel": 1,
                    }
                ]
            )
        else:
            raise AssertionError(command)
        return type("Completed", (), {"stdout": stdout, "stderr": ""})()

    monkeypatch.setattr(lmstudio.subprocess, "run", fake_run)
    inventory = lmstudio.model_inventory()

    configured = inventory["configured_models"]
    assert configured["gemma"]["downloaded"] is True
    assert configured["gemma"]["loaded"] is False
    assert configured["fastcontext"]["downloaded"] is True
    assert configured["fastcontext"]["loaded"] is True
    assert configured["fastcontext"]["loaded_detail"]["contextLength"] == 65536


def test_load_fastcontext_model_uses_expected_lms_flags(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> object:
        calls.append(command)
        assert kwargs["check"] is True
        if command[1:] == ["ls", "--json"]:
            stdout = json.dumps([{"modelKey": lmstudio.DEFAULT_FASTCONTEXT_MODEL}])
        elif command[1:] == ["ps", "--json"]:
            stdout = "[]"
        else:
            stdout = "loaded"
        return type("Completed", (), {"stdout": stdout, "stderr": ""})()

    monkeypatch.setattr(lmstudio.subprocess, "run", fake_run)

    result = lmstudio.load_fastcontext_model()

    command = calls[-1]
    assert command[1:] == [
        "load",
        lmstudio.DEFAULT_FASTCONTEXT_MODEL,
        "--context-length",
        "65536",
        "--gpu",
        "max",
        "--identifier",
        lmstudio.DEFAULT_FASTCONTEXT_MODEL,
    ]
    assert result["model_id"] == lmstudio.DEFAULT_FASTCONTEXT_MODEL
    assert result["context_length"] == 65536
    assert result["gpu"] == "max"


def test_load_fastcontext_model_reloads_existing_model(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> object:
        calls.append(command)
        assert kwargs["check"] is True
        if command[1:] == ["ls", "--json"]:
            stdout = json.dumps([{"modelKey": lmstudio.DEFAULT_FASTCONTEXT_MODEL}])
        elif command[1:] == ["ps", "--json"]:
            stdout = json.dumps(
                [
                    {
                        "modelKey": lmstudio.DEFAULT_FASTCONTEXT_MODEL,
                        "identifier": lmstudio.DEFAULT_FASTCONTEXT_MODEL,
                        "contextLength": 262144,
                    }
                ]
            )
        else:
            stdout = "ok"
        return type("Completed", (), {"stdout": stdout, "stderr": ""})()

    monkeypatch.setattr(lmstudio.subprocess, "run", fake_run)

    lmstudio.load_fastcontext_model()

    assert calls == [
        [lmstudio.LMS_EXE, "ls", "--json"],
        [lmstudio.LMS_EXE, "ps", "--json"],
        [lmstudio.LMS_EXE, "unload", lmstudio.DEFAULT_FASTCONTEXT_MODEL],
        [
            lmstudio.LMS_EXE,
            "load",
            lmstudio.DEFAULT_FASTCONTEXT_MODEL,
            "--context-length",
            "65536",
            "--gpu",
            "max",
            "--identifier",
            lmstudio.DEFAULT_FASTCONTEXT_MODEL,
        ],
    ]


def test_load_gemma_model_uses_expected_context_and_reloads_existing_model(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> object:
        calls.append(command)
        assert kwargs["check"] is True
        if command[1:] == ["ls", "--json"]:
            stdout = json.dumps([{"modelKey": lmstudio.DEFAULT_GEMMA_MODEL}])
        elif command[1:] == ["ps", "--json"]:
            stdout = json.dumps(
                [
                    {
                        "modelKey": lmstudio.DEFAULT_GEMMA_MODEL,
                        "identifier": lmstudio.DEFAULT_GEMMA_MODEL,
                        "contextLength": 8192,
                    }
                ]
            )
        else:
            stdout = "ok"
        return type("Completed", (), {"stdout": stdout, "stderr": ""})()

    monkeypatch.setattr(lmstudio.subprocess, "run", fake_run)

    result = lmstudio.load_gemma_model()

    assert calls == [
        [lmstudio.LMS_EXE, "ls", "--json"],
        [lmstudio.LMS_EXE, "ps", "--json"],
        [lmstudio.LMS_EXE, "unload", lmstudio.DEFAULT_GEMMA_MODEL],
        [
            lmstudio.LMS_EXE,
            "load",
            lmstudio.DEFAULT_GEMMA_MODEL,
            "--context-length",
            "32768",
            "--gpu",
            "max",
            "--identifier",
            lmstudio.DEFAULT_GEMMA_MODEL,
        ],
    ]
    assert result["model_id"] == lmstudio.DEFAULT_GEMMA_MODEL
    assert result["context_length"] == 32768
    assert result["gpu"] == "max"


@pytest.mark.asyncio
async def test_chat_json_posts_response_and_parses_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        payload = json.loads(request.content)
        assert payload["model"] == "gemma"
        assert payload["input"] == [{"role": "user", "content": "return json"}]
        assert payload["max_output_tokens"] == 1600
        return httpx.Response(200, json=_response_message_json('```json\n{"ok": true}\n```'))

    transport = httpx.MockTransport(handler)
    result = await lmstudio.chat_json(
        "gemma",
        [{"role": "user", "content": "return json"}],
        transport=transport,
    )
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_chat_json_passes_response_format() -> None:
    response_format = {"type": "json_schema", "json_schema": {"name": "x", "schema": {"type": "object"}}}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["text"]["format"] == {
            "type": "json_schema",
            "name": "x",
            "schema": {"type": "object"},
        }
        return httpx.Response(200, json=_response_message_json('{"ok": true}'))

    transport = httpx.MockTransport(handler)
    result = await lmstudio.chat_json(
        "gemma",
        [{"role": "user", "content": "return json"}],
        transport=transport,
        response_format=response_format,
    )
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_chat_completion_passes_seed_when_configured() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["seed"] == 123
        return httpx.Response(200, json=_response_message_json("ok"))

    transport = httpx.MockTransport(handler)
    result = await lmstudio.chat_completion(
        "gemma",
        [{"role": "user", "content": "hello"}],
        transport=transport,
        seed=123,
    )

    assert result.content == "ok"


@pytest.mark.asyncio
async def test_chat_completion_posts_tools_and_parses_tool_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        payload = json.loads(request.content)
        assert payload["tools"][0]["name"] == "Read"
        assert payload["tools"][0]["parameters"] == {"type": "object"}
        assert payload["tool_choice"] == "auto"
        return httpx.Response(200, json=_response_tool_call_json())

    transport = httpx.MockTransport(handler)
    result = await lmstudio.chat_completion(
        "fastcontext",
        [{"role": "user", "content": "find code"}],
        tools=[
            {
                "type": "function",
                "function": {"name": "Read", "parameters": {"type": "object"}},
            }
        ],
        tool_choice="auto",
        transport=transport,
    )

    assert result.content == ""
    assert result.finish_reason == "tool_calls"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call-1"
    assert result.tool_calls[0].name == "Read"
    assert result.tool_calls[0].arguments == {"path": "src/app.py", "offset": 3, "limit": 20}
    assert result.output_items[0]["type"] == "function_call"


@pytest.mark.asyncio
async def test_chat_completion_keeps_malformed_tool_arguments_nonfatal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_response_tool_call_json(name="Grep", arguments="{bad"))

    result = await lmstudio.chat_completion(
        "fastcontext",
        [{"role": "user", "content": "find code"}],
        transport=httpx.MockTransport(handler),
    )

    assert result.tool_calls[0].arguments == {}
    assert "Invalid tool arguments JSON" in str(result.tool_calls[0].arguments_error)


@pytest.mark.asyncio
async def test_chat_text_still_requires_text_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_response_message_json(""))

    with pytest.raises(lmstudio.LMStudioError, match="empty response"):
        await lmstudio.chat_text(
            "fastcontext",
            [{"role": "user", "content": "find code"}],
            transport=httpx.MockTransport(handler),
        )


def test_parse_json_content_handles_embedded_json() -> None:
    assert lmstudio.parse_json_content('Here is JSON: {"ok": true}') == {"ok": True}


def _write_card_fixture(root: Path) -> None:
    (root / "app").mkdir()
    (root / "app" / "page.tsx").write_text("export default function Page() { return <main /> }")
    (root / "package.json").write_text(
        json.dumps({"dependencies": {"next": "15", "react": "19"}}),
        encoding="utf-8",
    )


def _create_repository_card(tmp_path: Path) -> str:
    snapshot_root = tmp_path / "snapshot"
    snapshot_root.mkdir()
    _write_card_fixture(snapshot_root)
    repo_id = catalog.upsert_repository(
        {
            "owner": {"login": "owner"},
            "name": "repo",
            "full_name": "owner/repo",
            "html_url": "https://github.com/owner/repo",
            "private": False,
            "archived": False,
            "language": "TypeScript",
            "topics": ["nextjs"],
        },
        "test",
    )
    snapshot_id = catalog.upsert_snapshot(repo_id, "abc123", "main", snapshot_root)
    return catalog.upsert_repository_card(snapshot_id, pipeline.build_repository_card(snapshot_root))


def _create_profile_candidate(
    tmp_path: Path,
    owner: str,
    *,
    stars: int,
    asset_score: float,
    profile: dict[str, Any] | None = None,
) -> str:
    snapshot_root = tmp_path / owner
    snapshot_root.mkdir()
    _write_card_fixture(snapshot_root)
    repo_id = catalog.upsert_repository(
        {
            "owner": {"login": owner},
            "name": "repo",
            "full_name": f"{owner}/repo",
            "html_url": f"https://github.com/{owner}/repo",
            "private": False,
            "archived": False,
            "mirror_url": None,
            "fork": False,
            "is_template": False,
            "language": "TypeScript",
            "size": 100,
            "created_at": "2026-01-01T00:00:00Z",
            "pushed_at": "2026-06-20T12:00:00Z",
            "topics": ["nextjs"],
            "stargazers_count": stars,
            "forks_count": 1,
        },
        "test",
    )
    snapshot_id = catalog.upsert_snapshot(repo_id, f"{owner}sha", "main", snapshot_root)
    card = pipeline.build_repository_card(snapshot_root)
    if profile is not None:
        card["gemma_profile"] = profile
    card_id = catalog.upsert_repository_card(snapshot_id, card)
    catalog.upsert_asset(
        snapshot_id,
        repo_id,
        "data-table",
        {
            "entry_paths": ["app/page.tsx"],
            "dependency_paths": ["package.json"],
            "external_dependencies": ["@tanstack/react-table"],
            "evidence_paths": ["app/page.tsx:1-1"],
            "synthesis": {"noise_penalty": 0.0},
            "reuse_score": asset_score,
        },
    )
    return card_id


@pytest.mark.asyncio
async def test_profile_repository_cards_stores_gemma_profile(tmp_path, monkeypatch) -> None:
    card_id = _create_repository_card(tmp_path)

    async def fake_validate_models(config: lmstudio.LMStudioConfig) -> dict[str, Any]:
        return {
            "models": [config.gemma_model],
            "gemma_available": True,
            "fastcontext_available": False,
        }

    async def fake_chat_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["response_format"] == profiler.PROFILE_RESPONSE_FORMAT
        return {
            "repository_type": "reference_application",
            "capabilities": [{"name": "dashboard", "confidence": 0.8, "evidence": ["app/page.tsx"]}],
            "likely_usefulness": 0.7,
            "extractability": 0.6,
            "maintenance_quality": 0.5,
            "needs_fastcontext": True,
            "concerns": [],
        }

    monkeypatch.setattr(profiler.lmstudio, "validate_models", fake_validate_models)
    monkeypatch.setattr(profiler.lmstudio, "chat_json", fake_chat_json)

    result = await profiler.profile_repository_cards(limit=5)
    assert result == {"profiled_cards": 1, "failed_cards": 0, "available_cards": 1}

    row = catalog.get_connection().execute(
        "SELECT gemma_profile FROM repository_cards WHERE card_id = ?",
        [card_id],
    ).fetchone()
    assert row is not None
    stored = json.loads(row[0])
    assert stored["schema_version"] == profiler.PROFILE_SCHEMA_VERSION
    assert stored["repository_type"] == "reference_application"

    runs = catalog.get_connection().execute(
        "SELECT stage_name, status, model_id FROM analysis_runs WHERE stage_name = 'profile'"
    ).fetchall()
    assert runs == [("profile", "completed", lmstudio.DEFAULT_GEMMA_MODEL)]


@pytest.mark.asyncio
async def test_profile_repository_cards_rejects_uninformative_profile(tmp_path, monkeypatch) -> None:
    card_id = _create_repository_card(tmp_path)

    async def fake_validate_models(config: lmstudio.LMStudioConfig) -> dict[str, Any]:
        return {
            "models": [config.gemma_model],
            "gemma_available": True,
            "fastcontext_available": False,
        }

    async def fake_chat_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "repository_type": "examples",
            "capabilities": [],
            "likely_usefulness": 0,
            "extractability": 0,
            "maintenance_quality": 0,
            "needs_fastcontext": True,
            "concerns": [],
        }

    monkeypatch.setattr(profiler.lmstudio, "validate_models", fake_validate_models)
    monkeypatch.setattr(profiler.lmstudio, "chat_json", fake_chat_json)

    result = await profiler.profile_repository_cards(limit=5)
    assert result == {"profiled_cards": 0, "failed_cards": 1, "available_cards": 1}

    row = catalog.get_connection().execute(
        "SELECT gemma_profile FROM repository_cards WHERE card_id = ?",
        [card_id],
    ).fetchone()
    assert row is not None
    assert row[0] is None
    runs = catalog.get_connection().execute(
        "SELECT status FROM analysis_runs WHERE stage_name = 'profile'"
    ).fetchall()
    assert runs == [("failed",)]


@pytest.mark.asyncio
async def test_profile_repository_cards_reprofiles_old_schema(tmp_path, monkeypatch) -> None:
    card_id = _create_repository_card(tmp_path)
    catalog.update_repository_card_gemma_profile(
        card_id,
        {
            "schema_version": "gemma-profile-v1",
            "repository_type": "reference_application",
            "capabilities": [],
            "likely_usefulness": 0.5,
            "extractability": 0.5,
            "maintenance_quality": 0.5,
            "needs_fastcontext": True,
            "concerns": ["old schema"],
        },
    )

    async def fake_validate_models(config: lmstudio.LMStudioConfig) -> dict[str, Any]:
        return {
            "models": [config.gemma_model],
            "gemma_available": True,
            "fastcontext_available": False,
        }

    async def fake_chat_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "repository_type": "reference_application",
            "capabilities": [],
            "likely_usefulness": 0.7,
            "extractability": 0.6,
            "maintenance_quality": 0.5,
            "needs_fastcontext": False,
            "concerns": ["new schema"],
        }

    monkeypatch.setattr(profiler.lmstudio, "validate_models", fake_validate_models)
    monkeypatch.setattr(profiler.lmstudio, "chat_json", fake_chat_json)

    result = await profiler.profile_repository_cards(limit=5)
    assert result == {"profiled_cards": 1, "failed_cards": 0, "available_cards": 1}

    row = catalog.get_connection().execute(
        "SELECT gemma_profile FROM repository_cards WHERE card_id = ?",
        [card_id],
    ).fetchone()
    assert row is not None
    stored = json.loads(row[0])
    assert stored["schema_version"] == profiler.PROFILE_SCHEMA_VERSION


@pytest.mark.asyncio
async def test_profile_repository_cards_audit_priority_selects_best_downloaded_target(
    tmp_path,
    monkeypatch,
) -> None:
    high_card_id = _create_profile_candidate(tmp_path, "high", stars=1, asset_score=0.95)
    low_card_id = _create_profile_candidate(tmp_path, "low", stars=100, asset_score=0.5)

    async def fake_validate_models(config: lmstudio.LMStudioConfig) -> dict[str, Any]:
        return {
            "models": [config.gemma_model],
            "gemma_available": True,
            "fastcontext_available": False,
        }

    async def fake_chat_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "repository_type": "reference_application",
            "capabilities": [],
            "likely_usefulness": 0.8,
            "extractability": 0.8,
            "maintenance_quality": 0.8,
            "needs_fastcontext": False,
            "concerns": [],
        }

    monkeypatch.setattr(profiler.lmstudio, "validate_models", fake_validate_models)
    monkeypatch.setattr(profiler.lmstudio, "chat_json", fake_chat_json)

    result = await profiler.profile_repository_cards(limit=1, priority="audit", scope="downloaded")

    assert result == {"profiled_cards": 1, "failed_cards": 0, "available_cards": 1}
    rows = catalog.get_connection().execute(
        """
        SELECT card_id, gemma_profile
        FROM repository_cards
        WHERE card_id IN (?, ?)
        """,
        [high_card_id, low_card_id],
    ).fetchall()
    profiles = {str(card_id): profile for card_id, profile in rows}
    assert profiles[high_card_id] is not None
    assert profiles[low_card_id] is None


def test_lmstudio_status_cli_prints_json(monkeypatch, capsys) -> None:
    import source_scout.__main__ as main_module

    async def fake_status(
        start_server: bool,
        smoke_test: bool,
        *,
        load_gemma: bool,
        gemma_context_length: int,
        gemma_gpu: str,
    ) -> dict[str, object]:
        assert start_server is True
        assert smoke_test is True
        assert load_gemma is True
        assert gemma_context_length == 32768
        assert gemma_gpu == "max"
        return {"reachable": True}

    monkeypatch.setattr(main_module, "_lmstudio_status", fake_status)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "source_scout",
            "lmstudio-status",
            "--start-server",
            "--smoke-test",
            "--load-gemma",
        ],
    )
    main_module.main()
    captured = capsys.readouterr()
    assert '"reachable": true' in captured.out


@pytest.mark.asyncio
async def test_lmstudio_status_reports_api_downloaded_and_loaded(monkeypatch) -> None:
    import source_scout.__main__ as main_module

    async def fake_validate_models(config: lmstudio.LMStudioConfig) -> dict[str, object]:
        return {
            "base_url": config.base_url,
            "models": [config.gemma_model],
            "gemma_model": config.gemma_model,
            "fastcontext_model": config.fastcontext_model,
            "gemma_available": True,
            "fastcontext_available": False,
        }

    def fake_model_inventory(config: lmstudio.LMStudioConfig) -> dict[str, object]:
        return {
            "downloaded_models": [config.gemma_model, config.fastcontext_model],
            "loaded_models": [config.fastcontext_model],
            "configured_models": {
                "gemma": {
                    "model_id": config.gemma_model,
                    "downloaded": True,
                    "loaded": False,
                    "loaded_detail": None,
                },
                "fastcontext": {
                    "model_id": config.fastcontext_model,
                    "downloaded": True,
                    "loaded": True,
                    "loaded_detail": {"contextLength": 65536},
                },
            },
        }

    monkeypatch.setattr(lmstudio, "validate_models", fake_validate_models)
    monkeypatch.setattr(lmstudio, "model_inventory", fake_model_inventory)

    result = await main_module._lmstudio_status(start_server=False, smoke_test=False)

    configured = result["configured_models"]
    assert configured["gemma"]["downloaded"] is True
    assert configured["gemma"]["loaded"] is False
    assert configured["gemma"]["api_listed"] is True
    assert configured["fastcontext"]["downloaded"] is True
    assert configured["fastcontext"]["loaded"] is True
    assert configured["fastcontext"]["api_listed"] is False


@pytest.mark.asyncio
async def test_lmstudio_status_loads_gemma_when_context_is_too_small(monkeypatch) -> None:
    import source_scout.__main__ as main_module

    validate_calls = 0
    load_calls: list[tuple[int, str]] = []

    async def fake_validate_models(config: lmstudio.LMStudioConfig) -> dict[str, object]:
        nonlocal validate_calls
        validate_calls += 1
        return {
            "base_url": config.base_url,
            "models": [config.gemma_model],
            "gemma_model": config.gemma_model,
            "fastcontext_model": config.fastcontext_model,
            "gemma_available": True,
            "fastcontext_available": False,
        }

    def fake_model_inventory(config: lmstudio.LMStudioConfig) -> dict[str, object]:
        loaded_detail = {"contextLength": 8192 if not load_calls else 32768}
        return {
            "downloaded_models": [config.gemma_model],
            "loaded_models": [config.gemma_model],
            "configured_models": {
                "gemma": {
                    "model_id": config.gemma_model,
                    "downloaded": True,
                    "loaded": True,
                    "loaded_detail": loaded_detail,
                },
                "fastcontext": {
                    "model_id": config.fastcontext_model,
                    "downloaded": False,
                    "loaded": False,
                    "loaded_detail": None,
                },
            },
        }

    def fake_load_gemma_model(
        config: lmstudio.LMStudioConfig,
        *,
        context_length: int,
        gpu: str,
    ) -> dict[str, object]:
        load_calls.append((context_length, gpu))
        return {
            "model_id": config.gemma_model,
            "context_length": context_length,
            "gpu": gpu,
        }

    monkeypatch.setattr(lmstudio, "validate_models", fake_validate_models)
    monkeypatch.setattr(lmstudio, "model_inventory", fake_model_inventory)
    monkeypatch.setattr(lmstudio, "load_gemma_model", fake_load_gemma_model)

    result = await main_module._lmstudio_status(
        start_server=False,
        smoke_test=False,
        load_gemma=True,
        gemma_context_length=32768,
        gemma_gpu="max",
    )

    assert validate_calls == 2
    assert load_calls == [(32768, "max")]
    assert result["load_gemma"] == {
        "model_id": lmstudio.DEFAULT_GEMMA_MODEL,
        "context_length": 32768,
        "gpu": "max",
    }
    assert result["configured_models"]["gemma"]["loaded_detail"]["contextLength"] == 32768


@pytest.mark.asyncio
async def test_lmstudio_status_reports_start_failure_as_json(monkeypatch) -> None:
    import source_scout.__main__ as main_module

    async def fake_validate_models(config: lmstudio.LMStudioConfig) -> dict[str, object]:
        raise lmstudio.LMStudioError("api unreachable")

    def fake_start_server(config: lmstudio.LMStudioConfig) -> bool:
        raise lmstudio.LMStudioError("start failed")

    def fake_inventory(config: lmstudio.LMStudioConfig) -> dict[str, object]:
        return {
            "downloaded_models": [],
            "loaded_models": [],
            "configured_models": {},
        }

    monkeypatch.setattr(lmstudio, "validate_models", fake_validate_models)
    monkeypatch.setattr(lmstudio, "start_server", fake_start_server)
    monkeypatch.setattr(lmstudio, "model_inventory", fake_inventory)

    result = await main_module._lmstudio_status(start_server=True, smoke_test=False)

    assert result["reachable"] is False
    assert result["error"] == "api unreachable"
    assert result["start_error"] == "start failed"
    assert "LM Studio UI" in str(result["hint"])


def test_profile_cli_invokes_profiler(monkeypatch, capsys) -> None:
    import source_scout.__main__ as main_module

    async def fake_profile(
        limit: int,
        force: bool = False,
        *,
        priority: str = "created-at",
        scope: str = "downloaded",
    ) -> dict[str, int]:
        assert limit == 2
        assert force is True
        assert priority == "audit"
        assert scope == "cataloged"
        return {"profiled_cards": 2}

    monkeypatch.setattr(profiler, "profile_repository_cards", fake_profile)
    monkeypatch.setattr(
        sys,
        "argv",
        ["source_scout", "profile", "--limit", "2", "--force", "--priority", "audit", "--scope", "cataloged"],
    )
    main_module.main()
    captured = capsys.readouterr()
    assert "{'profiled_cards': 2}" in captured.out
