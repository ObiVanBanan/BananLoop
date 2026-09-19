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



def test_init_cli_accepts_agent_isolation_controls():
    args = parser().parse_args([
        "init",
        "--agent-hide", "harness_rag/**",
        "--agent-hide", "data/**",
        "--agent-metric", "public_hard_pass_rate",
        "--agent-metric", "public_false_match_rate",
    ])

    assert args.agent_hide == ["harness_rag/**", "data/**"]
    assert args.agent_metric == [
        "public_hard_pass_rate",
        "public_false_match_rate",
    ]
