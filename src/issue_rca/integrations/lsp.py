from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Protocol


class LspSearchProvider(Protocol):
    async def document_symbols(
        self, repository_root: Path, files: list[str]
    ) -> list[dict[str, Any]]: ...


class NullLspSearchProvider:
    async def document_symbols(
        self, repository_root: Path, files: list[str]
    ) -> list[dict[str, Any]]:
        return []


class MultilspySearchProvider:
    """Optional local LSP adapter. Network-fetched repositories still use Composio search."""

    def __init__(self, language: str = "python") -> None:
        self.language = language

    async def document_symbols(
        self, repository_root: Path, files: list[str]
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._document_symbols_sync, repository_root, files)

    @staticmethod
    def _validated_files(repository_root: Path, files: list[str]) -> tuple[Path, list[str]]:
        root = repository_root.expanduser().resolve()
        if not root.exists():
            raise FileNotFoundError(f"LSP repository root 不存在: {root}")
        if not root.is_dir():
            raise NotADirectoryError(f"LSP repository root 不是目录: {root}")

        valid: list[str] = []
        for raw_path in files:
            normalized = raw_path.replace("\\", "/").lstrip("/")
            candidate = (root / normalized).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"LSP 文件路径越出仓库根目录: {raw_path}") from exc
            if not candidate.is_file():
                raise FileNotFoundError(f"LSP 文件不存在: {candidate}")
            valid.append(normalized)
        return root, valid

    @staticmethod
    def _jedi_server_binary() -> str:
        executable = shutil.which("jedi-language-server")
        if executable:
            return executable
        suffix = ".exe" if os.name == "nt" else ""
        python_dir = Path(sys.executable).resolve().parent
        candidates = [python_dir / f"jedi-language-server{suffix}"]
        if os.name == "nt":
            candidates.insert(0, python_dir / "Scripts" / f"jedi-language-server{suffix}")
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        raise FileNotFoundError(
            "找不到 jedi-language-server 可执行文件；请在当前 Python 环境安装 lsp extras"
        )

    def _document_symbols_sync(
        self, repository_root: Path, files: list[str]
    ) -> list[dict[str, Any]]:
        root, valid_files = self._validated_files(repository_root, files)
        if not valid_files:
            return []

        try:
            from multilspy import SyncLanguageServer
            from multilspy.multilspy_config import MultilspyConfig
            from multilspy.multilspy_logger import MultilspyLogger
        except ImportError as exc:
            raise RuntimeError("未安装 multilspy，请执行 pip install -e .[lsp]") from exc

        config = MultilspyConfig.from_dict(
            {
                "code_language": self.language,
                "server_binary": self._jedi_server_binary(),
            }
        )
        logger = MultilspyLogger()
        server = SyncLanguageServer.create(config, logger, str(root))
        results: list[dict[str, Any]] = []
        with server.start_server():
            for path in valid_files[:10]:
                symbols = server.request_document_symbols(path)
                results.append({"path": path, "symbols": symbols})
        return results
