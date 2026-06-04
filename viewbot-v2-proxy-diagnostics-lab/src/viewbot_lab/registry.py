from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

CommandHandler = Callable[[object], int]


@dataclass
class CommandRegistry:
    """Small command registry mapping stable command names to handlers."""

    _handlers: dict[str, CommandHandler] = field(default_factory=dict)

    def register(self, name: str, handler: CommandHandler) -> None:
        cleaned = name.strip().lower()
        if not cleaned:
            raise ValueError("command name must not be empty")
        if cleaned in self._handlers:
            raise ValueError(f"command already registered: {cleaned}")
        self._handlers[cleaned] = handler

    def get(self, name: str) -> CommandHandler:
        cleaned = name.strip().lower()
        try:
            return self._handlers[cleaned]
        except KeyError as exc:
            known = ", ".join(sorted(self._handlers)) or "<none>"
            raise KeyError(f"unknown command {name!r}; known commands: {known}") from exc

    def names(self) -> Iterable[str]:
        return tuple(sorted(self._handlers))
