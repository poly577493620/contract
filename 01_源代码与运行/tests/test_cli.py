"""CLI 基础行为。"""

from click.testing import CliRunner

from smart_compliance.cli import main


def test_cli_help() -> None:
    """帮助页应列出核心命令。"""
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "parse" in result.output
    assert "review" in result.output
    assert "build-gold" in result.output
    assert "evaluate" in result.output
    assert "validate-gold" in result.output
    assert "build-field-template" in result.output
    assert "evaluate-fields" in result.output
    assert "build-blind-manifest" in result.output


def test_evaluate_requires_arguments() -> None:
    """缺少参数时 Click 应返回使用错误。"""
    result = CliRunner().invoke(main, ["evaluate"])
    assert result.exit_code == 2
