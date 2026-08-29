"""Stable contracts around external MCP implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import ModelIdentity, ParameterRange


class SafetyViolation(RuntimeError):
    """Raised when an unsafe operation must stop without retrying."""


class CubismAdapter(ABC):
    """No CubismExternalEditMCP tool names escape this adapter boundary."""

    @abstractmethod
    def get_model_identity(self) -> ModelIdentity: ...

    @abstractmethod
    def get_parameter(self, parameter_id: str) -> ParameterRange: ...

    @abstractmethod
    def set_parameter_preview(self, parameter_id: str, value: float) -> None: ...

    @abstractmethod
    def clear_parameter_preview(self) -> None: ...


class PcControlAdapter(ABC):
    """Read-only PC-control boundary used by Phase 0."""

    @abstractmethod
    def emergency_stop_active(self) -> bool: ...

    @abstractmethod
    def focus_cubism(self) -> None: ...

    @abstractmethod
    def take_screenshot(self) -> bytes: ...


class InMemoryCubismAdapter(CubismAdapter):
    """Test-only stand-in; previews stay in memory and never write a model file."""

    def __init__(self, identity: ModelIdentity, parameters: list[ParameterRange]) -> None:
        self.identity = identity
        self.parameters = {item.parameter_id: item for item in parameters}
        self.preview: tuple[str, float] | None = None

    def get_model_identity(self) -> ModelIdentity:
        return self.identity

    def get_parameter(self, parameter_id: str) -> ParameterRange:
        return self.parameters[parameter_id]

    def set_parameter_preview(self, parameter_id: str, value: float) -> None:
        parameter = self.get_parameter(parameter_id)
        if not parameter.accepts(value):
            raise ValueError(f"Preview value is outside {parameter_id}'s range")
        self.preview = (parameter_id, value)

    def clear_parameter_preview(self) -> None:
        self.preview = None


class InMemoryPcControlAdapter(PcControlAdapter):
    """Test-only stand-in; screenshot bytes are returned, never persisted."""

    def __init__(self, *, emergency_stop: bool = False, screenshot: bytes = b"preview") -> None:
        self.emergency_stop = emergency_stop
        self.screenshot = screenshot
        self.focused = False

    def emergency_stop_active(self) -> bool:
        return self.emergency_stop

    def focus_cubism(self) -> None:
        self.focused = True

    def take_screenshot(self) -> bytes:
        return self.screenshot
