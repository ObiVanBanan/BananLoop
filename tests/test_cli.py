from bananloop.cli import _agent_config, parser


def test_pi_cli_builds_agent_config():
    args = parser().parse_args([
        "init",
        "--goal", "test",
        "--eval-cmd", "python evaluator.py",
        "--metric", "quality",
        "--target", "1",
        "--min-improvement", "0.1",
        "--agent", "pi",
        "--provider", "ollama-cloud",
        "--model", "nemotron-3-nano:30b",
        "--thinking", "off",
        "--pi-arg=--exclude-tools",
        "--pi-arg=web_search",
    ])

    config = _agent_config(args)

    assert config.kind == "pi"
    assert config.model == "nemotron-3-nano:30b"
    assert config.extra_args == [
        "--provider", "ollama-cloud",
        "--thinking", "off",
        "--exclude-tools", "web_search",
    ]
