"""Generate deterministic OpenAPI and endpoint-overview artifacts for Poe bots."""

import argparse
import copy
import json
import os
import stat
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from fastapi_poe import (  # noqa: E402
    PoeBot,
    QueryRequest,
    ReportErrorRequest,
    ReportFeedbackRequest,
    ReportReactionRequest,
    SettingsRequest,
    SettingsResponse,
    make_app,
)
from fastapi_poe.client import PROTOCOL_VERSION  # noqa: E402

PUBLIC_BOT_PATH = "/bot"
BEARER_SCHEME_NAME = "PoeBearerAuth"
FIXTURE_ACCESS_KEY = "0" * 32
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_MARKDOWN_BYTES = 512 * 1024
MAX_PUBLIC_OPERATIONS = 8
MAX_COMPONENT_SCHEMAS = 256

REQUEST_MODELS: tuple[tuple[type[BaseModel], str], ...] = (
    (QueryRequest, "query"),
    (ReportErrorRequest, "report_error"),
    (ReportFeedbackRequest, "report_feedback"),
    (ReportReactionRequest, "report_reaction"),
    (SettingsRequest, "settings"),
)


class GeneratorError(Exception):
    """Base class for controlled generator failures."""


class SchemaValidationError(GeneratorError):
    """Raised when the generated public contract is incomplete or inconsistent."""


class OutputPathError(GeneratorError):
    """Raised when an output path is unsafe."""


class OutputSizeError(GeneratorError):
    """Raised when a generated artifact exceeds its documented bound."""


class OutputWriteError(GeneratorError):
    """Raised when an output cannot be durably replaced."""


def build_fixture_app() -> FastAPI:
    """Build a local authenticated app without documentation or provider calls."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    bot = PoeBot(path=PUBLIC_BOT_PATH, access_key=FIXTURE_ACCESS_KEY)
    return make_app(bot, app=app)


def _add_model_schema(
    schemas: dict[str, Any], model: type[BaseModel]
) -> dict[str, Any]:
    model_schema = copy.deepcopy(
        model.model_json_schema(ref_template="#/components/schemas/{model}")
    )
    definitions = model_schema.pop("$defs", {})
    if not isinstance(definitions, dict):
        raise SchemaValidationError("Model definitions must be an object")
    for name, definition in definitions.items():
        existing = schemas.get(name)
        if existing is not None and existing != definition:
            raise SchemaValidationError("Conflicting component schema definitions")
        schemas[name] = definition
    schemas[model.__name__] = model_schema
    return model_schema


def _request_components() -> dict[str, Any]:
    schemas: dict[str, Any] = {}
    for model, request_type in REQUEST_MODELS:
        model_schema = _add_model_schema(schemas, model)
        properties = model_schema.get("properties")
        if not isinstance(properties, dict):
            raise SchemaValidationError("Request schema properties must be an object")
        properties["type"] = {"const": request_type, "title": "Type", "type": "string"}
        if model is QueryRequest:
            properties.pop("access_key", None)
            properties.pop("api_key", None)
            required = model_schema.get("required", [])
            if isinstance(required, list):
                model_schema["required"] = [
                    name for name in required if name not in {"access_key", "api_key"}
                ]

    _add_model_schema(schemas, SettingsResponse)
    return dict(sorted(schemas.items()))


def _get_operation() -> dict[str, Any]:
    return {
        "description": "Confirms that the local FastAPI Poe bot server is running.",
        "operationId": "getPoeBotStatus",
        "responses": {
            "200": {
                "content": {"text/html": {"schema": {"type": "string"}}},
                "description": "HTML setup guidance.",
            }
        },
        "summary": "Get Poe bot server status",
    }


def _post_operation() -> dict[str, Any]:
    request_references = [
        {"$ref": f"#/components/schemas/{model.__name__}"}
        for model, _ in REQUEST_MODELS
    ]
    return {
        "description": (
            "Handles Poe protocol query, settings, feedback, reaction, and error "
            "requests. Query responses use server-sent events; other request types "
            "return JSON."
        ),
        "operationId": "postPoeProtocolRequest",
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "discriminator": {"propertyName": "type"},
                        "oneOf": request_references,
                    }
                }
            },
            "required": True,
        },
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {
                            "oneOf": [
                                {"$ref": "#/components/schemas/SettingsResponse"},
                                {
                                    "additionalProperties": False,
                                    "description": "Empty acknowledgement object.",
                                    "maxProperties": 0,
                                    "type": "object",
                                },
                            ]
                        }
                    },
                    "text/event-stream": {
                        "schema": {
                            "description": "Poe protocol server-sent event stream.",
                            "type": "string",
                        }
                    },
                },
                "description": "Protocol response.",
            },
            "401": {"description": "Missing or invalid Poe access key."},
            "500": {"description": "Malformed or failed protocol request."},
            "501": {"description": "Unsupported Poe protocol request type."},
        },
        "security": [{BEARER_SCHEME_NAME: []}],
        "summary": "Handle a Poe protocol request",
    }


def build_public_openapi(app: FastAPI, public_path: str) -> dict[str, Any]:
    """Build a public contract from the real route while excluding unrelated paths."""
    raw_specification = app.openapi()
    raw_paths = raw_specification.get("paths")
    if not isinstance(raw_paths, dict) or public_path not in raw_paths:
        raise SchemaValidationError("The public bot path is missing from the app")
    raw_operations = raw_paths[public_path]
    if not isinstance(raw_operations, dict) or not {"get", "post"}.issubset(
        raw_operations
    ):
        raise SchemaValidationError("The public bot path must expose GET and POST")

    specification = {
        "components": {
            "schemas": _request_components(),
            "securitySchemes": {
                BEARER_SCHEME_NAME: {
                    "bearerFormat": "Poe access key",
                    "description": "Poe server bot access key.",
                    "scheme": "bearer",
                    "type": "http",
                }
            },
        },
        "info": {
            "description": "Public HTTP contract for a FastAPI Poe server bot.",
            "title": "FastAPI Poe Protocol API",
            "version": PROTOCOL_VERSION,
        },
        "openapi": raw_specification.get("openapi", "3.1.0"),
        "paths": {public_path: {"get": _get_operation(), "post": _post_operation()}},
    }
    validate_public_spec(specification, public_path=public_path)
    return specification


def build_openapi_document() -> dict[str, Any]:
    """Generate and validate the canonical public OpenAPI document."""
    return build_public_openapi(build_fixture_app(), PUBLIC_BOT_PATH)


def _component_references(value: object) -> set[str]:
    references: set[str] = set()
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            reference = current.get("$ref")
            if isinstance(reference, str):
                references.add(reference)
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return references


def validate_public_spec(
    specification: dict[str, Any], public_path: str = PUBLIC_BOT_PATH
) -> None:
    """Fail closed when a public schema invariant is missing or mutated."""
    paths = specification.get("paths")
    if not isinstance(paths, dict) or list(paths) != [public_path]:
        raise SchemaValidationError(
            "Only the configured public bot path may be exported"
        )
    operations = paths[public_path]
    if not isinstance(operations, dict) or list(operations) != ["get", "post"]:
        raise SchemaValidationError("The public bot path must export GET and POST")
    if len(operations) > MAX_PUBLIC_OPERATIONS:
        raise SchemaValidationError("The public operation limit was exceeded")

    get_operation = operations["get"]
    post_operation = operations["post"]
    if get_operation.get("operationId") != "getPoeBotStatus":
        raise SchemaValidationError("The GET operation ID is not stable")
    if "security" in get_operation:
        raise SchemaValidationError("The status page must remain unauthenticated")
    if post_operation.get("operationId") != "postPoeProtocolRequest":
        raise SchemaValidationError("The POST operation ID is not stable")
    if post_operation.get("security") != [{BEARER_SCHEME_NAME: []}]:
        raise SchemaValidationError("The POST operation must require Poe bearer auth")

    try:
        request_schema = post_operation["requestBody"]["content"]["application/json"][
            "schema"
        ]
        response_content = post_operation["responses"]["200"]["content"]
    except (KeyError, TypeError) as exc:
        raise SchemaValidationError(
            "The POST request or response schema is incomplete"
        ) from exc
    if request_schema.get("discriminator") != {"propertyName": "type"}:
        raise SchemaValidationError("The request union must be discriminated by type")
    expected_references = [
        f"#/components/schemas/{model.__name__}" for model, _ in REQUEST_MODELS
    ]
    actual_references = [item.get("$ref") for item in request_schema.get("oneOf", [])]
    if actual_references != expected_references:
        raise SchemaValidationError("The request union does not match protocol models")
    if list(response_content) != ["application/json", "text/event-stream"]:
        raise SchemaValidationError("The POST response media types are incomplete")

    components = specification.get("components")
    if not isinstance(components, dict):
        raise SchemaValidationError("OpenAPI components are missing")
    schemas = components.get("schemas")
    if not isinstance(schemas, dict) or len(schemas) > MAX_COMPONENT_SCHEMAS:
        raise SchemaValidationError(
            "OpenAPI component schemas are invalid or excessive"
        )
    security_schemes = components.get("securitySchemes")
    expected_security_scheme = {
        "bearerFormat": "Poe access key",
        "description": "Poe server bot access key.",
        "scheme": "bearer",
        "type": "http",
    }
    if (
        not isinstance(security_schemes, dict)
        or security_schemes.get(BEARER_SCHEME_NAME) != expected_security_scheme
    ):
        raise SchemaValidationError("The Poe bearer security scheme is inaccurate")

    for model, request_type in REQUEST_MODELS:
        schema = schemas.get(model.__name__)
        if not isinstance(schema, dict):
            raise SchemaValidationError("A Poe request component is missing")
        properties = schema.get("properties")
        if not isinstance(properties, dict) or properties.get("type") != {
            "const": request_type,
            "title": "Type",
            "type": "string",
        }:
            raise SchemaValidationError("A Poe request discriminator is inaccurate")
    query_properties = schemas[QueryRequest.__name__]["properties"]
    if "access_key" in query_properties or "api_key" in query_properties:
        raise SchemaValidationError(
            "Internal access-key fields leaked into the request schema"
        )

    for reference in _component_references(specification):
        prefix = "#/components/schemas/"
        if not reference.startswith(prefix) or reference[len(prefix) :] not in schemas:
            raise SchemaValidationError(
                "The OpenAPI document has an unresolved reference"
            )

    encoded = json.dumps(
        specification, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if len(encoded) > MAX_JSON_BYTES:
        raise OutputSizeError("JSON output exceeds the configured size limit")
    if FIXTURE_ACCESS_KEY.encode("ascii") in encoded:
        raise SchemaValidationError("The fixture access key leaked into the document")


def render_json(specification: dict[str, Any]) -> bytes:
    """Render canonical UTF-8 JSON with stable key ordering."""
    validate_public_spec(specification)
    content = (
        json.dumps(specification, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if len(content) > MAX_JSON_BYTES:
        raise OutputSizeError("JSON output exceeds the configured size limit")
    return content


def render_endpoint_overview(specification: dict[str, Any]) -> bytes:
    """Render a deterministic Markdown summary of the exported operations."""
    validate_public_spec(specification)
    lines = ["# FastAPI Poe Endpoints", ""]
    paths = specification["paths"]
    for path in sorted(paths):
        for method in ("get", "post"):
            operation = paths[path][method]
            lines.extend(
                [
                    f"## `{method.upper()} {path}`",
                    "",
                    f"- Summary: {operation['summary']}",
                    f"- Operation ID: `{operation['operationId']}`",
                ]
            )
            if operation.get("security"):
                lines.append("- Authentication: bearer access key")
            else:
                lines.append("- Authentication: none")
            request_body = operation.get("requestBody")
            if isinstance(request_body, dict):
                references = request_body["content"]["application/json"]["schema"][
                    "oneOf"
                ]
                schemas = specification["components"]["schemas"]
                variants = sorted(
                    schemas[item["$ref"].rsplit("/", 1)[-1]]["properties"]["type"][
                        "const"
                    ]
                    for item in references
                )
                lines.append(
                    "- Request variants: "
                    + ", ".join(f"`{variant}`" for variant in variants)
                )
            response_types = sorted(
                operation["responses"]["200"].get("content", {}).keys()
            )
            lines.append(
                "- Response media types: "
                + ", ".join(f"`{media_type}`" for media_type in response_types)
            )
            lines.append("")
    content = ("\n".join(lines).rstrip() + "\n").encode("utf-8")
    if len(content) > MAX_MARKDOWN_BYTES:
        raise OutputSizeError("Markdown output exceeds the configured size limit")
    return content


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:-1]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise OutputPathError("Output paths may not contain symlink components")


def _validate_output_path(path: Path) -> Path:
    absolute_path = _absolute_without_resolving(path)
    _reject_symlink_components(absolute_path)
    parent = absolute_path.parent
    try:
        parent_mode = parent.lstat().st_mode
    except FileNotFoundError as exc:
        raise OutputPathError("Output parent directory does not exist") from exc
    if stat.S_ISLNK(parent_mode):
        raise OutputPathError("Output parent directory may not be a symlink")
    if not stat.S_ISDIR(parent_mode):
        raise OutputPathError("Output parent must be a directory")
    try:
        target_mode = absolute_path.lstat().st_mode
    except FileNotFoundError:
        return absolute_path
    if stat.S_ISLNK(target_mode):
        raise OutputPathError("Output target may not be a symlink")
    if not stat.S_ISREG(target_mode):
        raise OutputPathError("Output target must be a regular file")
    return absolute_path


def _stage_content(path: Path, content: bytes) -> Path:
    descriptor = -1
    temporary_path: Optional[Path] = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=".openapi-generator-", suffix=".tmp"
        )
        temporary_path = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output_file:
            descriptor = -1
            output_file.write(content)
            output_file.flush()
            os.fsync(output_file.fileno())
        return temporary_path
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise OutputWriteError("Unable to stage output file") from exc


def _sync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_outputs(
    *,
    json_path: Path,
    markdown_path: Path,
    json_content: bytes,
    markdown_content: bytes,
) -> None:
    """Write both bounded artifacts through private same-directory temporary files."""
    if len(json_content) > MAX_JSON_BYTES:
        raise OutputSizeError("JSON output exceeds the configured size limit")
    if len(markdown_content) > MAX_MARKDOWN_BYTES:
        raise OutputSizeError("Markdown output exceeds the configured size limit")
    safe_json_path = _validate_output_path(json_path)
    safe_markdown_path = _validate_output_path(markdown_path)
    if safe_json_path == safe_markdown_path:
        raise OutputPathError("JSON and Markdown output paths must be different")

    staged_json: Optional[Path] = None
    staged_markdown: Optional[Path] = None
    try:
        staged_json = _stage_content(safe_json_path, json_content)
        staged_markdown = _stage_content(safe_markdown_path, markdown_content)
        try:
            os.replace(staged_json, safe_json_path)
            staged_json = None
            os.replace(staged_markdown, safe_markdown_path)
            staged_markdown = None
            _sync_directory(safe_json_path.parent)
            if safe_markdown_path.parent != safe_json_path.parent:
                _sync_directory(safe_markdown_path.parent)
        except OSError as exc:
            raise OutputWriteError("Unable to replace output file") from exc
    finally:
        if staged_json is not None:
            staged_json.unlink(missing_ok=True)
        if staged_markdown is not None:
            staged_markdown.unlink(missing_ok=True)


def generate_files(*, json_path: Path, markdown_path: Path) -> None:
    """Generate, render, and write both documentation artifacts."""
    specification = build_openapi_document()
    write_outputs(
        json_path=json_path,
        markdown_path=markdown_path,
        json_content=render_json(specification),
        markdown_content=render_endpoint_overview(specification),
    )


def _parse_arguments(arguments: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the public FastAPI Poe OpenAPI artifacts."
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path(__file__).resolve().parent / "openapi.json",
        help="OpenAPI JSON output path (default: docs/openapi.json).",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=Path(__file__).resolve().parent / "openapi_endpoints.md",
        help="Endpoint overview output path (default: docs/openapi_endpoints.md).",
    )
    return parser.parse_args(arguments)


def main(arguments: Optional[Sequence[str]] = None) -> int:
    """CLI entry point with controlled, secret-safe failures."""
    parsed = _parse_arguments(arguments)
    try:
        generate_files(
            json_path=parsed.json_output, markdown_path=parsed.markdown_output
        )
    except GeneratorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("error: OpenAPI generation failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
