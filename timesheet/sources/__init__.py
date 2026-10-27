"""Evidence sources. Each exposes collect(cfg, sites, timeline) -> Report."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Report:
    name: str
    summary: str  # one line for the listing header
    details: list[str] = field(default_factory=list)  # lines shown with --all
    needs_naming: list[str] = field(default_factory=list)  # lines always shown: evidence with an unnamed client
    available: bool = True
    extensions: dict[str, bool] = field(default_factory=dict)  # optional add-ons the source found (check command)
