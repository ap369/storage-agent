from agent.prompt import build_system_prompt


def test_build_system_prompt_returns_base_prompt():
    assert build_system_prompt("You are a helpful agent.") == "You are a helpful agent."
