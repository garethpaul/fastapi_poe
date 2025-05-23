"""
Generate an OpenAPI specification for a simple ``fastapi_poe`` application.

Usage::

    .venv/bin/python docs/generate_openapi_spec.py

The specification will be written to ``openapi_spec.json`` in the current
working directory.
"""

from __future__ import annotations

import json
import sys

sys.path.append("../src")

import fastapi_poe as fp


class EchoBot(fp.PoeBot):
    async def get_response(
        self, request: fp.QueryRequest
    ) -> fp.AsyncIterable[fp.PartialResponse]:
        yield fp.PartialResponse(text="hello")


def main() -> None:
    app = fp.make_app(EchoBot(path="/bot", access_key="dummy"), allow_without_key=True)
    spec = app.openapi()
    with open("openapi_spec.json", "w") as f:
        json.dump(spec, f, indent=2)


if __name__ == "__main__":
    main()
