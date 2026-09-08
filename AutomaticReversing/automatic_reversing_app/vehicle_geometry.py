"""Shared real-vehicle geometry model used by the reversing UI."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from automatic_reversing.game_geometry import GameGeometryResolver
from automatic_reversing_visualizer.app import VehicleGeometryVisualizer


class ReversingGeometryModel(VehicleGeometryVisualizer):
    """Run the visualizer geometry pipeline without creating a second window."""

    def __init__(self) -> None:
        self.game_geometry = GameGeometryResolver()
        self.dimensions = self._load_dimensions()
        self.geometry_overrides = self._load_geometry_overrides()
        self.data = {}
        self.last_units = []
        self.configuration_signature = None
        self.pending_configuration_signature = None
        self.pending_configuration_frames = 0
        self.configuration_generation = 0
        self.resource_sync_attempts = set()
        self.compact_labels = True
        self.root: tk.Tk | None = None
        self.canvas: tk.Canvas | None = None
        self.status_var: tk.StringVar | None = None
        self._refresh_callback: Callable[[], None] | None = None

    def attach_ui(
        self,
        root: tk.Tk,
        canvas: tk.Canvas,
        status_var: tk.StringVar,
        refresh_callback: Callable[[], None],
    ) -> None:
        self.root = root
        self.canvas = canvas
        self.status_var = status_var
        self._refresh_callback = refresh_callback

    def update_snapshot(self, snapshot: dict) -> bool:
        accepted = self._accept_configuration_snapshot(snapshot)
        if not accepted:
            return False
        if self.root is not None and self.status_var is not None:
            self._auto_import_current_resources()
        self.last_units = self._build_layouts()
        return True

    def force_refresh(self, show_errors: bool = True) -> None:
        if self._refresh_callback is not None:
            self._refresh_callback()

    def reload_dimensions(self) -> None:
        self.dimensions = self._load_dimensions()
        self.geometry_overrides = self._load_geometry_overrides()
        self.game_geometry.clear_cache()
        self.force_refresh(show_errors=False)
        if self.status_var is not None:
            self.status_var.set("轮廓：已重新读取游戏碰撞模型")
