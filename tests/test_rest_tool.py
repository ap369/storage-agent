from contextlib import AsyncExitStack

import httpx
import respx

from agent.tools.rest import build_rest_tools


def make_config(**overrides):
    config = {
        "name": "weather_api",
        "description": "Weather lookups",
        "base_url": "https://weather.example.com",
        "auth_type": "none",
        "auth_value": None,
        "auth_header_name": None,
        "operations": [
            {
                "name": "get_forecast",
                "method": "GET",
                "path": "/forecast/{city}",
                "description": "Get the forecast for a city",
                "params_schema": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string"},
                        "units": {"type": "string"},
                    },
                    "required": ["city"],
                },
            }
        ],
    }
    config.update(overrides)
    return config


async def get_tools(configs, stack):
    tools = await build_rest_tools(configs, stack)
    return {t.name: t for t in tools}


async def test_builds_one_tool_per_operation_namespaced_by_config_name():
    async with AsyncExitStack() as stack:
        tools = await get_tools([make_config()], stack)
        assert "weather_api_get_forecast" in tools
        assert tools["weather_api_get_forecast"].description == "Get the forecast for a city"
        assert (
            tools["weather_api_get_forecast"].parameters
            == make_config()["operations"][0]["params_schema"]
        )


@respx.mock
async def test_get_substitutes_path_param_and_sends_remaining_as_query():
    route = respx.get("https://weather.example.com/forecast/paris").mock(
        return_value=httpx.Response(200, json={"forecast": "sunny"})
    )
    async with AsyncExitStack() as stack:
        tools = await get_tools([make_config()], stack)

        result = await tools["weather_api_get_forecast"].execute({"city": "paris", "units": "metric"})

    assert route.called
    assert route.calls[0].request.url.params["units"] == "metric"
    assert "sunny" in result


@respx.mock
async def test_post_operation_sends_remaining_args_as_json_body():
    config = make_config(
        operations=[
            {
                "name": "create_alert",
                "method": "POST",
                "path": "/alerts",
                "description": "Create a weather alert",
                "params_schema": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}, "threshold": {"type": "number"}},
                },
            }
        ]
    )
    route = respx.post("https://weather.example.com/alerts").mock(
        return_value=httpx.Response(201, json={"id": "1"})
    )
    async with AsyncExitStack() as stack:
        tools = await get_tools([config], stack)

        result = await tools["weather_api_create_alert"].execute({"city": "paris", "threshold": 30})

    assert route.called
    import json as jsonlib

    assert jsonlib.loads(route.calls[0].request.content) == {"city": "paris", "threshold": 30}
    assert '"id"' in result or "1" in result


@respx.mock
async def test_bearer_auth_header_is_injected():
    config = make_config(auth_type="bearer", auth_value="secret-key")
    route = respx.get("https://weather.example.com/forecast/paris").mock(
        return_value=httpx.Response(200, text="ok")
    )
    async with AsyncExitStack() as stack:
        tools = await get_tools([config], stack)
        await tools["weather_api_get_forecast"].execute({"city": "paris"})

    assert route.calls[0].request.headers["Authorization"] == "Bearer secret-key"


@respx.mock
async def test_api_key_header_auth_is_injected():
    config = make_config(
        auth_type="api_key_header", auth_value="secret-key", auth_header_name="X-Api-Key"
    )
    route = respx.get("https://weather.example.com/forecast/paris").mock(
        return_value=httpx.Response(200, text="ok")
    )
    async with AsyncExitStack() as stack:
        tools = await get_tools([config], stack)
        await tools["weather_api_get_forecast"].execute({"city": "paris"})

    assert route.calls[0].request.headers["X-Api-Key"] == "secret-key"


@respx.mock
async def test_non_2xx_response_returns_error_string_without_raising():
    respx.get("https://weather.example.com/forecast/paris").mock(
        return_value=httpx.Response(500, text="boom")
    )
    async with AsyncExitStack() as stack:
        tools = await get_tools([make_config()], stack)

        result = await tools["weather_api_get_forecast"].execute({"city": "paris"})

    assert result.startswith("Error:")


async def test_only_declared_operations_are_exposed_as_tools():
    async with AsyncExitStack() as stack:
        tools = await get_tools([make_config()], stack)
        assert len(tools) == 1


async def test_builds_one_http_client_per_config_shared_across_operations(monkeypatch):
    real_init = httpx.AsyncClient.__init__
    construction_count = 0

    def spy_init(self, *args, **kwargs):
        nonlocal construction_count
        construction_count += 1
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", spy_init)

    config = make_config(
        operations=[
            {**make_config()["operations"][0], "name": "get_forecast"},
            {**make_config()["operations"][0], "name": "get_forecast_alt"},
        ]
    )

    async with AsyncExitStack() as stack:
        tools = await get_tools([config], stack)
        assert len(tools) == 2

    assert construction_count == 1
