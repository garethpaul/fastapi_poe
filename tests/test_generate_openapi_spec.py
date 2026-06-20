import importlib.util
import os
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "docs" / "generate_openapi_spec.py"
MODULE_SPEC = importlib.util.spec_from_file_location(
    "openapi_spec_generator", MODULE_PATH
)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
generator = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = generator
EXEC_MODULE = getattr(MODULE_SPEC.loader, "exec_module", None)
assert callable(EXEC_MODULE)
EXEC_MODULE(generator)


def test_public_schema_matches_the_poe_protocol_boundary() -> None:
    specification = generator.build_openapi_document()

    assert list(specification["paths"]) == [generator.PUBLIC_BOT_PATH]
    operations = specification["paths"][generator.PUBLIC_BOT_PATH]
    assert list(operations) == ["get", "post"]

    get_operation = operations["get"]
    assert get_operation["operationId"] == "getPoeBotStatus"
    assert "security" not in get_operation
    assert list(get_operation["responses"]["200"]["content"]) == ["text/html"]

    post_operation = operations["post"]
    assert post_operation["operationId"] == "postPoeProtocolRequest"
    assert post_operation["security"] == [{generator.BEARER_SCHEME_NAME: []}]
    assert list(post_operation["responses"]["200"]["content"]) == [
        "application/json",
        "text/event-stream",
    ]

    request_schema = post_operation["requestBody"]["content"]["application/json"][
        "schema"
    ]
    assert request_schema["discriminator"] == {"propertyName": "type"}
    assert [item["$ref"].rsplit("/", 1)[-1] for item in request_schema["oneOf"]] == [
        "QueryRequest",
        "ReportErrorRequest",
        "ReportFeedbackRequest",
        "ReportReactionRequest",
        "SettingsRequest",
    ]

    components = specification["components"]["schemas"]
    expected_types = {
        "QueryRequest": "query",
        "ReportErrorRequest": "report_error",
        "ReportFeedbackRequest": "report_feedback",
        "ReportReactionRequest": "report_reaction",
        "SettingsRequest": "settings",
    }
    for schema_name, request_type in expected_types.items():
        assert components[schema_name]["properties"]["type"] == {
            "const": request_type,
            "title": "Type",
            "type": "string",
        }

    query_properties = components["QueryRequest"]["properties"]
    assert "access_key" not in query_properties
    assert "api_key" not in query_properties
    assert specification["components"]["securitySchemes"][
        generator.BEARER_SCHEME_NAME
    ] == {
        "bearerFormat": "Poe access key",
        "description": "Poe server bot access key.",
        "scheme": "bearer",
        "type": "http",
    }


def test_hidden_fastapi_routes_do_not_leak_into_the_public_schema() -> None:
    app = generator.build_fixture_app()

    @app.get("/internal/health")
    async def internal_health() -> dict[str, bool]:
        return {"ok": True}

    specification = generator.build_public_openapi(app, generator.PUBLIC_BOT_PATH)

    assert list(specification["paths"]) == [generator.PUBLIC_BOT_PATH]
    assert "/internal/health" not in specification["paths"]
    serialized = generator.render_json(specification)
    assert b"/docs" not in serialized
    assert b"/redoc" not in serialized
    assert b"/openapi.json" not in serialized


def test_rendering_is_deterministic_and_endpoint_overview_is_complete() -> None:
    specification = generator.build_openapi_document()

    first_json = generator.render_json(specification)
    second_json = generator.render_json(generator.build_openapi_document())
    first_markdown = generator.render_endpoint_overview(specification)
    second_markdown = generator.render_endpoint_overview(
        generator.build_openapi_document()
    )

    assert first_json == second_json
    assert first_json.endswith(b"\n")
    assert first_markdown == second_markdown
    assert first_markdown.endswith(b"\n")
    overview = first_markdown.decode("utf-8")
    assert "## `GET /bot`" in overview
    assert "Operation ID: `getPoeBotStatus`" in overview
    assert "## `POST /bot`" in overview
    assert "Authentication: bearer access key" in overview
    expected_variants = (
        "Request variants: `query`, `report_error`, `report_feedback`, "
        "`report_reaction`, `settings`"
    )
    assert expected_variants in overview
    assert "Response media types: `application/json`, `text/event-stream`" in overview


def test_write_outputs_atomically_replaces_regular_files(tmp_path: Path) -> None:
    json_path = tmp_path / "openapi.json"
    markdown_path = tmp_path / "openapi_endpoints.md"
    json_path.write_text("old json", encoding="utf-8")
    markdown_path.write_text("old markdown", encoding="utf-8")

    generator.write_outputs(
        json_path=json_path,
        markdown_path=markdown_path,
        json_content=b'{"openapi":"3.1.0"}\n',
        markdown_content=b"# Endpoints\n",
    )

    assert json_path.read_bytes() == b'{"openapi":"3.1.0"}\n'
    assert markdown_path.read_bytes() == b"# Endpoints\n"
    assert json_path.stat().st_mode & 0o777 == 0o600
    assert markdown_path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".openapi-generator-*.tmp"))


def test_write_outputs_rejects_duplicate_paths(tmp_path: Path) -> None:
    output_path = tmp_path / "output"

    with pytest.raises(generator.OutputPathError, match="must be different"):
        generator.write_outputs(
            json_path=output_path,
            markdown_path=output_path,
            json_content=b"{}\n",
            markdown_content=b"# Endpoints\n",
        )


@pytest.mark.parametrize("target_kind", ["directory", "symlink"])
def test_write_outputs_rejects_non_regular_targets(
    tmp_path: Path, target_kind: str
) -> None:
    json_path = tmp_path / "openapi.json"
    if target_kind == "directory":
        json_path.mkdir()
    else:
        real_target = tmp_path / "real.json"
        real_target.write_text("do not replace", encoding="utf-8")
        json_path.symlink_to(real_target)

    with pytest.raises(generator.OutputPathError):
        generator.write_outputs(
            json_path=json_path,
            markdown_path=tmp_path / "openapi_endpoints.md",
            json_content=b"{}\n",
            markdown_content=b"# Endpoints\n",
        )

    if target_kind == "symlink":
        assert (tmp_path / "real.json").read_text(encoding="utf-8") == "do not replace"


def test_write_outputs_rejects_symlinked_parent(tmp_path: Path) -> None:
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    linked_directory = tmp_path / "linked"
    linked_directory.symlink_to(real_directory, target_is_directory=True)

    with pytest.raises(generator.OutputPathError, match="symlink"):
        generator.write_outputs(
            json_path=linked_directory / "openapi.json",
            markdown_path=tmp_path / "openapi_endpoints.md",
            json_content=b"{}\n",
            markdown_content=b"# Endpoints\n",
        )

    assert not (real_directory / "openapi.json").exists()


def test_write_outputs_preserves_existing_file_if_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    json_path = tmp_path / "openapi.json"
    markdown_path = tmp_path / "openapi_endpoints.md"
    json_path.write_text("old json", encoding="utf-8")
    markdown_path.write_text("old markdown", encoding="utf-8")

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(generator.OutputWriteError, match="replace"):
        generator.write_outputs(
            json_path=json_path,
            markdown_path=markdown_path,
            json_content=b"new json",
            markdown_content=b"new markdown",
        )

    assert json_path.read_text(encoding="utf-8") == "old json"
    assert markdown_path.read_text(encoding="utf-8") == "old markdown"
    assert not list(tmp_path.glob(".openapi-generator-*.tmp"))


def test_write_outputs_rejects_oversized_content(tmp_path: Path) -> None:
    with pytest.raises(generator.OutputSizeError, match="JSON output"):
        generator.write_outputs(
            json_path=tmp_path / "openapi.json",
            markdown_path=tmp_path / "openapi_endpoints.md",
            json_content=b"x" * (generator.MAX_JSON_BYTES + 1),
            markdown_content=b"# Endpoints\n",
        )


def test_cli_does_not_echo_unexpected_exception_details(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_secret = "pplx-should-never-appear-in-output"

    def fail_generation(*args: object, **kwargs: object) -> None:
        raise RuntimeError(fake_secret)

    monkeypatch.setattr(generator, "generate_files", fail_generation)

    assert generator.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert fake_secret not in captured.err
    assert captured.err == "error: OpenAPI generation failed\n"
