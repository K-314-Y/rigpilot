"""Strict local configuration for the two approved MCP child processes."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from mcp import StdioServerParameters


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class McpConfiguration:
    pc_control_mcp_root: Path
    cubism_port: int = 22033

    @classmethod
    def load(cls, path: Path) -> McpConfiguration:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise ConfigurationError(f"設定ファイルを読み取れません: {path}") from error
        except json.JSONDecodeError as error:
            raise ConfigurationError("設定ファイルのJSON形式が正しくありません") from error
        root = data.get("pc_control_mcp_root")
        port = data.get("cubism_port", 22033)
        if not isinstance(root, str) or not root.strip():
            raise ConfigurationError("pc_control_mcp_root を設定してください")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ConfigurationError("cubism_port は1から65535の整数にしてください")
        return cls(Path(os.path.expandvars(root)).expanduser(), port)

    def cubism_server(self) -> StdioServerParameters:
        if shutil.which("uvx") is None:
            raise ConfigurationError("uvx が見つかりません。CubismExternalEditMCPの公式手順で準備してください")
        environment = dict(os.environ)
        environment["NO_PROXY"] = "localhost,127.0.0.1"
        environment["CUBISM_PORT"] = str(self.cubism_port)
        return StdioServerParameters(command="uvx", args=["cubism-mcp"], env=environment)

    def pc_control_server(self) -> StdioServerParameters:
        root = self.pc_control_mcp_root.resolve()
        python = root / ".venv" / "Scripts" / "python.exe"
        config = root / "config.json"
        if not python.is_file() or not config.is_file():
            raise ConfigurationError("Windows PC Control MCPのroot、.venv、config.jsonを確認してください")
        environment = dict(os.environ)
        environment["PC_MCP_CONFIG"] = str(config)
        return StdioServerParameters(
            command=str(python),
            args=["-m", "pc_control_mcp.server"],
            cwd=str(root),
            env=environment,
        )
