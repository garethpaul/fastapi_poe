# fastapi_poe

An implementation of the
[Poe protocol](https://creator.poe.com/docs/poe-protocol-specification) using FastAPI.

### Installation

Install the package from PyPI:

```bash
pip install fastapi-poe
```

### Write your own bot

This package can also be used as a base to write your own bot. You can inherit from
`PoeBot` to make a bot:

```python
import fastapi_poe as fp

class EchoBot(fp.PoeBot):
    async def get_response(self, request: fp.QueryRequest):
        last_message = request.query[-1].content
        yield fp.PartialResponse(text=last_message)

if __name__ == "__main__":
    fp.run(EchoBot(), allow_without_key=True)
```

Now, run your bot using `python <filename.py>`.

- In a different terminal, run [ngrok](https://ngrok.com/) to make it publicly
  accessible.
- Use the publicly accessible url to integrate your bot with
  [Poe](https://poe.com/create_bot?server=1)

### Enable authentication

Poe servers send requests containing Authorization HTTP header in the format "Bearer
<access_key>"; the access key is configured in the bot settings page.

To validate that the request is from the Poe servers, you can either set the environment
variable POE_ACCESS_KEY or pass the parameter access_key in the run function like:

```python
if __name__ == "__main__":
    fp.run(EchoBot(), access_key=<key>)
```

## Samples

Check out our starter code
[repository](https://github.com/poe-platform/server-bot-quick-start) for some examples
you can use to get started with bot development.

### Generate the public OpenAPI contract

Install the package in editable mode, then run the generator from any working directory:

```bash
pip install -e .
python docs/generate_openapi_spec.py
```

The command writes `docs/openapi.json` and `docs/openapi_endpoints.md`. The schema
documents an authenticated bot at `/bot`, including the five Poe protocol request
variants, the HTML status response, JSON acknowledgements, and query SSE responses. Use
`--json-output` and `--markdown-output` to select other existing directories.

Generation uses a local fixture only. It does not contact Poe, OpenAI, or any other
provider.
