import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from rigpilot.live_adapters import CandidateOpenDialogError, WindowsPcControlAdapter


def window(hwnd: int, title: str) -> dict[str, object]:
    return {"hwnd": hwnd, "title": title, "pid": 100, "process": "java.exe"}


class FakeCandidateOpenClient:
    def __init__(self, window_lists: list[list[dict[str, object]]]) -> None:
        self.window_lists = iter(window_lists)
        self.required: list[set[str]] = []
        self.data_calls: list[str] = []
        self.json_calls: list[tuple[str, dict[str, object] | None]] = []

    async def require_tools(self, names: set[str]) -> None:
        self.required.append(names)

    async def call_data(self, name: str, _arguments: object = None) -> object:
        self.data_calls.append(name)
        return next(self.window_lists)

    async def call_json(self, name: str, arguments: dict[str, object] | None = None) -> dict[str, object]:
        self.json_calls.append((name, arguments))
        return {"ok": True}


class CandidateOpenTests(unittest.TestCase):
    def adapter(self, client: FakeCandidateOpenClient) -> WindowsPcControlAdapter:
        adapter = WindowsPcControlAdapter(client)  # type: ignore[arg-type]
        adapter._CANDIDATE_OPEN_MAX_POLLS = 2
        adapter._CANDIDATE_OPEN_POLL_SECONDS = 0
        return adapter

    def test_gui_candidate_open_uses_cubism_dialog_and_absolute_path(self) -> None:
        cubism = window(1, "Live2D Cubism Editor - working.cmo3")
        dialog = window(2, "開く")
        client = FakeCandidateOpenClient([[cubism], [cubism], [cubism, dialog], [cubism]])
        candidate = Path(r"C:\workspace\candidates\candidate-1\model.cmo3")

        asyncio.run(self.adapter(client).open_candidate_in_cubism(candidate))

        self.assertEqual(client.data_calls, ["list_windows"] * 4)
        self.assertEqual(
            client.json_calls,
            [
                ("activate_window", {"title_contains": "Live2D Cubism Editor - working.cmo3"}),
                ("hotkey", {"keys": ["ctrl", "o"]}),
                ("type_text", {"text": str(candidate), "use_clipboard": False}),
                ("press_key", {"key": "enter"}),
            ],
        )

    def test_missing_file_dialog_stops_without_path_input(self) -> None:
        cubism = window(1, "Live2D Cubism Editor - working.cmo3")
        client = FakeCandidateOpenClient([[cubism], [cubism], [cubism], [cubism]])
        candidate = Path(r"C:\workspace\candidates\candidate-1\model.cmo3")

        with patch("rigpilot.live_adapters.asyncio.sleep", new=AsyncMock()), self.assertRaisesRegex(
            CandidateOpenDialogError, "ファイル選択ダイアログ"
        ):
            asyncio.run(self.adapter(client).open_candidate_in_cubism(candidate))

        self.assertEqual([name for name, _arguments in client.json_calls], ["activate_window", "hotkey"])

    def test_unexpected_dialog_after_enter_stops_without_more_input(self) -> None:
        cubism = window(1, "Live2D Cubism Editor - working.cmo3")
        dialog = window(2, "開く")
        warning = window(3, "互換性の警告")
        client = FakeCandidateOpenClient([[cubism], [cubism], [cubism, dialog], [cubism, warning]])
        candidate = Path(r"C:\workspace\candidates\candidate-1\model.cmo3")

        with self.assertRaisesRegex(CandidateOpenDialogError, "想定外"):
            asyncio.run(self.adapter(client).open_candidate_in_cubism(candidate))

        self.assertEqual([name for name, _arguments in client.json_calls], ["activate_window", "hotkey", "type_text", "press_key"])
