import copy
import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

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


def _remove_security(specification: dict[str, Any]) -> None:
    del specification["paths"][generator.PUBLIC_BOT_PATH]["post"]["security"]


def _leak_hidden_path(specification: dict[str, Any]) -> None:
    specification["paths"]["/internal"] = {
        "get": {"operationId": "internal", "responses": {"200": {}}}
    }


def _change_operation_id(specification: dict[str, Any]) -> None:
    specification["paths"][generator.PUBLIC_BOT_PATH]["post"][
        "operationId"
    ] = "poe_post_bot_post"


def _remove_request_variant(specification: dict[str, Any]) -> None:
    variants = specification["paths"][generator.PUBLIC_BOT_PATH]["post"]["requestBody"][
        "content"
    ]["application/json"]["schema"]["oneOf"]
    variants.pop()


def _remove_streaming_response(specification: dict[str, Any]) -> None:
    del specification["paths"][generator.PUBLIC_BOT_PATH]["post"]["responses"]["200"][
        "content"
    ]["text/event-stream"]


def _add_unresolved_reference(specification: dict[str, Any]) -> None:
    specification["paths"][generator.PUBLIC_BOT_PATH]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]["oneOf"][0] = {"$ref": "#/components/schemas/MissingRequest"}


@pytest.mark.parametrize(
    "mutation",
    [
        _remove_security,
        _leak_hidden_path,
        _change_operation_id,
        _remove_request_variant,
        _remove_streaming_response,
        _add_unresolved_reference,
    ],
)
def test_hostile_schema_mutations_are_rejected(
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    specification = copy.deepcopy(generator.build_openapi_document())
    mutation(specification)

    with pytest.raises(generator.SchemaValidationError):
        generator.validate_public_spec(specification)
