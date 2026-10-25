from __future__ import annotations

import asyncio
import platform
import sys
from pathlib import Path
from typing import Annotated, Literal

import typer
from rich.console import Console
from rich.table import Table

from issue_rca.config import Settings, load_settings
from issue_rca.integrations.composio import ComposioGitHubGateway
from issue_rca.integrations.composio_auth import (
    ComposioGitHubAuthorizer,
    save_connected_account_id,
)
from issue_rca.integrations.deepseek import DeepSeekLLM
from issue_rca.integrations.github import GitHubClient
from issue_rca.models import DoctorCheck
from issue_rca.runtime import analyze_issue

app = typer.Typer(
    name="issue-rca",
    help="GitHub Issue 多 Agent 根因诊断与代码定位 CLI。",
    no_args_is_help=True,
)
console = Console()
composio_app = typer.Typer(help="管理 Composio 连接。", no_args_is_help=True)
composio_connect_app = typer.Typer(help="创建 Composio toolkit 授权。", no_args_is_help=True)
app.add_typer(composio_app, name="composio")
composio_app.add_typer(composio_connect_app, name="connect")
DEMO_ISSUE_URL = "https://github.com/example/checkout-service/issues/42"


async def _doctor_async(settings: Settings) -> list[DoctorCheck]:
    checks = [
        DoctorCheck(
            name="Python",
            ok=(3, 11) <= sys.version_info[:2] < (3, 14),
            detail=f"{platform.python_version()} (recommended: 3.11 or 3.12)",
        )
    ]

    github_token = settings.secret("github_token")
    if not github_token:
        checks.append(DoctorCheck(name="GitHub", ok=False, detail="GITHUB_TOKEN 未配置"))
    else:
        client = GitHubClient(
            github_token,
            api_version=settings.github_api_version,
            timeout=settings.request_timeout_seconds,
        )
        try:
            checks.append(
                DoctorCheck(name="GitHub", ok=True, detail=await client.check_connection())
            )
        except Exception as exc:
            checks.append(DoctorCheck(name="GitHub", ok=False, detail=str(exc)))
        finally:
            await client.close()

    deepseek_key = settings.secret("deepseek_api_key")
    if not deepseek_key:
        checks.append(DoctorCheck(name="DeepSeek", ok=False, detail="DEEPSEEK_API_KEY 未配置"))
    else:
        try:
            llm = DeepSeekLLM(
                api_key=deepseek_key,
                base_url=settings.deepseek_base_url,
                model=settings.deepseek_model,
                max_tokens=16,
            )
            checks.append(
                DoctorCheck(name="DeepSeek", ok=True, detail=await llm.check_connection())
            )
        except Exception as exc:
            checks.append(DoctorCheck(name="DeepSeek", ok=False, detail=str(exc)))

    composio_key = settings.secret("composio_api_key")
    if not composio_key:
        checks.append(DoctorCheck(name="Composio", ok=False, detail="COMPOSIO_API_KEY 未配置"))
    else:
        try:
            gateway = await asyncio.to_thread(
                ComposioGitHubGateway,
                composio_key,
                settings.composio_user_id,
                settings.composio_github_connected_account_id,
            )
            checks.append(
                DoctorCheck(name="Composio", ok=True, detail=await gateway.check_connection())
            )
        except Exception as exc:
            checks.append(DoctorCheck(name="Composio", ok=False, detail=str(exc)))
    return checks


@app.command()
def doctor() -> None:
    """检查 Python、GitHub、DeepSeek 和 Composio 的配置与连通性。"""
    settings = load_settings()
    checks = asyncio.run(_doctor_async(settings))
    table = Table(title="IssueRCA-Agent Doctor")
    table.add_column("检查项")
    table.add_column("状态")
    table.add_column("详情")
    for check in checks:
        table.add_row(
            check.name, "[green]PASS[/green]" if check.ok else "[red]FAIL[/red]", check.detail
        )
    console.print(table)
    if not all(check.ok for check in checks):
        raise typer.Exit(code=1)


@app.command()
def demo(
    output: Annotated[Path | None, typer.Option("--output", help="运行产物根目录。")] = None,
) -> None:
    """运行零密钥、零 API 成本的端到端 Mock Demo。"""
    settings = load_settings(issue_rca_mode="mock")
    result = asyncio.run(analyze_issue(settings, DEMO_ISSUE_URL, "mock", output))
    console.print("[bold green]Mock RCA 完成[/bold green]")
    console.print(f"Top-1: [cyan]{result.report.suspected_locations[0].path}[/cyan]")
    console.print(f"置信度: {result.report.confidence:.0%}")
    console.print(f"报告: {result.artifacts.report_markdown}")


@app.command()
def analyze(
    issue_url: Annotated[str, typer.Option("--issue-url", help="GitHub Issue URL。")],
    mode: Annotated[
        Literal["mock", "live"] | None,
        typer.Option("--mode", help="mock 不调用外部服务；live 使用真实密钥。"),
    ] = None,
    output: Annotated[Path | None, typer.Option("--output", help="运行产物根目录。")] = None,
    max_files: Annotated[
        int | None,
        typer.Option("--max-files", min=1, max=50, help="代码候选文件上限。"),
    ] = None,
) -> None:
    """分析一个 GitHub Issue 并输出 RCA JSON、Markdown 和 trace。"""
    settings = load_settings()
    selected_mode: Literal["mock", "live"] = mode or settings.issue_rca_mode
    if max_files is not None:
        settings = settings.model_copy(update={"max_candidate_files": max_files})
    try:
        result = asyncio.run(analyze_issue(settings, issue_url, selected_mode, output))
    except Exception as exc:
        console.print(f"[bold red]分析失败：[/bold red]{type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from exc

    console.print("[bold green]RCA 分析完成[/bold green]")
    console.print(f"根因: {result.report.root_cause}")
    if result.report.suspected_locations:
        console.print(f"Top-1: [cyan]{result.report.suspected_locations[0].path}[/cyan]")
    console.print(f"耗时: {result.report.metrics.total_elapsed_ms} ms")
    console.print(f"报告目录: {result.artifacts.run_dir}")


def _resolve_env_path() -> Path:
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        return cwd_env
    project_env = Path(__file__).resolve().parents[2] / ".env"
    return project_env if project_env.exists() else cwd_env


@composio_connect_app.command("github")
def composio_connect_github(
    wait_seconds: Annotated[
        int,
        typer.Option(
            "--wait-seconds",
            min=10,
            max=900,
            help="等待 GitHub OAuth 完成的秒数。",
        ),
    ] = 180,
    wait: Annotated[
        bool,
        typer.Option("--wait/--no-wait", help="是否等待授权完成并验证连接。"),
    ] = True,
    save_id: Annotated[
        bool,
        typer.Option(
            "--save-id/--no-save-id",
            help="成功后是否只把 connected account ID 写入当前 .env。",
        ),
    ] = False,
    callback_url: Annotated[
        str | None,
        typer.Option("--callback-url", help="OAuth 完成后的可选回调地址。"),
    ] = None,
    alias: Annotated[
        str | None,
        typer.Option("--alias", help="该 GitHub 连接的可选别名。"),
    ] = None,
) -> None:
    """为 COMPOSIO_USER_ID 创建 GitHub OAuth 连接。"""
    settings = load_settings()
    composio_key = settings.secret("composio_api_key")
    if not composio_key:
        console.print("[bold red]COMPOSIO_API_KEY 未配置。[/bold red]")
        raise typer.Exit(code=1)

    try:
        authorizer = ComposioGitHubAuthorizer(composio_key, settings.composio_user_id)
        request = authorizer.authorize(callback_url=callback_url, alias=alias)
    except Exception as exc:
        console.print(f"[bold red]创建 GitHub 授权请求失败：[/bold red]{exc}")
        raise typer.Exit(code=1) from exc

    redirect_url = getattr(request, "redirect_url", None)
    if not redirect_url:
        console.print("[bold red]Composio 未返回 GitHub 授权链接。[/bold red]")
        raise typer.Exit(code=1)

    console.print("\n[bold cyan]请在浏览器中打开以下 GitHub 授权链接：[/bold cyan]")
    console.print(redirect_url)
    console.print(f"\n授权必须归属于 COMPOSIO_USER_ID=[bold]{settings.composio_user_id}[/bold]。")

    if not wait:
        console.print("\n已生成授权链接，但尚未确认授权成功。")
        console.print("完成浏览器授权后运行：issue-rca composio status github")
        console.print("最后再运行：issue-rca doctor")
        return

    console.print(f"\n正在等待授权完成，最长 {wait_seconds} 秒……")
    try:
        summary = authorizer.wait_for_active(request, wait_seconds)
    except Exception as exc:
        console.print(f"[bold red]授权未确认成功：[/bold red]{exc}")
        console.print("如果浏览器授权已完成，请运行：issue-rca composio status github")
        raise typer.Exit(code=1) from exc

    try:
        gateway = ComposioGitHubGateway(
            composio_key,
            settings.composio_user_id,
            summary.id,
        )
        native_check = asyncio.run(gateway.check_connection())
    except Exception as exc:
        console.print(
            "[bold yellow]Connected account 已是 ACTIVE，但原生 GitHub 工具验证失败：[/bold yellow]"
            f"{exc}"
        )
        raise typer.Exit(code=1) from exc

    if save_id:
        env_path = _resolve_env_path()
        save_connected_account_id(env_path, summary.id)
        console.print(f"Connected account ID 已写入：{env_path.resolve()}")

    console.print("\n[bold green]Composio GitHub 授权成功并已验证。[/bold green]")
    console.print(f"Connected account ID: {summary.id}")
    console.print(f"Status: {summary.status}")
    console.print(f"Toolkit: {summary.toolkit}")
    console.print(f"User ID: {summary.user_id}")
    console.print(f"Native tool check: {native_check}")
    console.print("可继续运行：issue-rca doctor")


@composio_app.command("status")
def composio_connection_status(
    toolkit: Annotated[
        Literal["github"],
        typer.Argument(help="要检查的 Composio toolkit。"),
    ] = "github",
) -> None:
    """检查当前用户的 ACTIVE Composio connected accounts。"""
    settings = load_settings()
    composio_key = settings.secret("composio_api_key")
    if not composio_key:
        console.print("[bold red]COMPOSIO_API_KEY 未配置。[/bold red]")
        raise typer.Exit(code=1)

    try:
        authorizer = ComposioGitHubAuthorizer(composio_key, settings.composio_user_id)
        accounts = authorizer.list_active()
    except Exception as exc:
        console.print(f"[bold red]读取 Composio 连接状态失败：[/bold red]{exc}")
        raise typer.Exit(code=1) from exc

    if not accounts:
        console.print(
            "[bold red]没有找到与当前 COMPOSIO_USER_ID 匹配的 ACTIVE GitHub connection。[/bold red]"
        )
        console.print("运行：issue-rca composio connect github")
        raise typer.Exit(code=1)

    table = Table(title=f"Composio {toolkit} ACTIVE connections")
    table.add_column("Connected account ID")
    table.add_column("Status")
    table.add_column("Toolkit")
    table.add_column("User ID")
    for account in accounts:
        table.add_row(account.id, account.status, account.toolkit, account.user_id)
    console.print(table)


if __name__ == "__main__":
    app()
