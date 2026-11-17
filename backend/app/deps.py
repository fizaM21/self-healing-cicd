from dataclasses import dataclass
from typing import Any, Callable

from .config import Settings


@dataclass
class Deps:
    settings: Settings
    session_factory: Callable[[], Any]
    github: Any
    analyzer: Any
    sandbox: Any
