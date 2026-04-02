"""Stacked session-box routing visualizer for the submit screen."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import ceil
from typing import TYPE_CHECKING

from textual.events import Resize
from textual.timer import Timer
from textual.widgets import Static

from leopard_gecko.models.session import Session, SessionsState, SessionStatus

if TYPE_CHECKING:
    from leopard_gecko.orchestrator.pipeline import SubmissionResult

STATUS_STYLE = {
    SessionStatus.IDLE: "bold bright_green",
    SessionStatus.BUSY: "bold yellow",
    SessionStatus.BLOCKED: "bold bright_red",
    SessionStatus.DEAD: "dim",
}

STATUS_LABEL = {
    SessionStatus.IDLE: "IDLE",
    SessionStatus.BUSY: "BUSY",
    SessionStatus.BLOCKED: "BLOCK",
    SessionStatus.DEAD: "DEAD",
}

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
FOOTER_H = 7
MAX_LOG = 30
MIN_W = 70
MIN_H = 20
CARD_W = 12
CARD_H = 4
CARD_GAP_X = 4
CARD_GAP_Y = 2
STACK_OFFSET_X = 2
STACK_OFFSET_Y = 1
MAX_VISIBLE_STACK_DEPTH = 4


class _Phase(Enum):
    IDLE = "idle"
    ROUTING = "routing"
    TRAVELING = "traveling"
    ARRIVED = "arrived"


@dataclass(frozen=True, slots=True)
class _Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def x2(self) -> int:
        return self.x + self.w - 1

    @property
    def y2(self) -> int:
        return self.y + self.h - 1


@dataclass(frozen=True, slots=True)
class _FieldLayout:
    rect: _Rect
    cols: int
    rows: int
    anchor_count: int
    group_size: int
    visible_stacks: int
    start_x: int
    start_y: int


@dataclass(frozen=True, slots=True)
class _StackView:
    index: int
    start_slot: int
    end_slot: int
    x: int
    y: int
    represented_count: int
    actual_sessions: tuple[Session, ...]

    @property
    def top_session(self) -> Session | None:
        return self.actual_sessions[0] if self.actual_sessions else None

    @property
    def display_depth(self) -> int:
        return min(self.represented_count, MAX_VISIBLE_STACK_DEPTH)

    @property
    def center(self) -> tuple[int, int]:
        return (
            self.x + CARD_W // 2,
            self.y + CARD_H // 2,
        )


def _line_points(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    x1, y1 = start
    x2, y2 = end
    dx = x2 - x1
    dy = y2 - y1
    steps = max(abs(dx), abs(dy))
    if steps == 0:
        return [(x1, y1)]

    points: list[tuple[int, int]] = []
    for step in range(steps + 1):
        t = step / steps
        point = (round(x1 + dx * t), round(y1 + dy * t))
        if not points or points[-1] != point:
            points.append(point)
    return points


def _compute_field_layout(width: int, height: int, slot_count: int) -> _FieldLayout:
    field_h = max(height - FOOTER_H, 6)
    rect = _Rect(1, 1, max(width - 2, 1), max(field_h - 2, 1))

    cols = max((rect.w + CARD_GAP_X) // (CARD_W + CARD_GAP_X), 1)
    rows = max((rect.h + CARD_GAP_Y) // (CARD_H + CARD_GAP_Y), 1)
    anchor_count = max(cols * rows, 1)
    group_size = max(ceil(slot_count / anchor_count), 1) if slot_count else 1
    visible_stacks = min(ceil(slot_count / group_size), anchor_count) if slot_count else 0

    used_w = cols * CARD_W + max(cols - 1, 0) * CARD_GAP_X
    used_h = rows * CARD_H + max(rows - 1, 0) * CARD_GAP_Y
    start_x = rect.x + max((rect.w - used_w) // 2, 0)
    start_y = rect.y + max((rect.h - used_h) // 2, 0)

    return _FieldLayout(
        rect=rect,
        cols=cols,
        rows=rows,
        anchor_count=anchor_count,
        group_size=group_size,
        visible_stacks=visible_stacks,
        start_x=start_x,
        start_y=start_y,
    )


def _stack_origin(layout: _FieldLayout, stack_index: int) -> tuple[int, int]:
    row = stack_index // layout.cols
    col = stack_index % layout.cols
    x = layout.start_x + col * (CARD_W + CARD_GAP_X)
    y = layout.start_y + row * (CARD_H + CARD_GAP_Y)
    return x, y


def _stack_views(layout: _FieldLayout, sessions: list[Session], slot_count: int) -> list[_StackView]:
    views: list[_StackView] = []
    for stack_index in range(layout.visible_stacks):
        start_slot = stack_index * layout.group_size
        end_slot = min(start_slot + layout.group_size, slot_count)
        x, y = _stack_origin(layout, stack_index)
        actual_sessions = tuple(sessions[start_slot:min(end_slot, len(sessions))])
        views.append(
            _StackView(
                index=stack_index,
                start_slot=start_slot,
                end_slot=end_slot,
                x=x,
                y=y,
                represented_count=max(end_slot - start_slot, 0),
                actual_sessions=actual_sessions,
            )
        )
    return views


def _hub_rect(layout: _FieldLayout, views: list[_StackView]) -> _Rect:
    width = min(max(26, layout.rect.w // 4), max(layout.rect.w - 8, 16))
    height = 5
    x = layout.rect.x + max((layout.rect.w - width) // 2, 0)

    center_y = layout.rect.y + max((layout.rect.h - height) // 2, 0)
    if not views:
        return _Rect(x, center_y, width, height)

    stacks_bottom = max(view.y + CARD_H + (view.display_depth - 1) * STACK_OFFSET_Y for view in views)
    gap_above_footer = layout.rect.y2 - stacks_bottom
    preferred_y = stacks_bottom + max((gap_above_footer - height) // 2, 1)

    if preferred_y + height - 1 <= layout.rect.y2:
        return _Rect(x, preferred_y, width, height)

    top_row_y = min(view.y for view in views)
    if top_row_y - layout.rect.y > height + 1:
        return _Rect(x, layout.rect.y + 1, width, height)

    return _Rect(x, center_y, width, height)


class RoutingVisualizer(Static):
    """Session container field with stacked cards and laser routing."""

    def __init__(self, max_sessions: int = 4, **kwargs) -> None:
        super().__init__("", **kwargs)
        self._max = max_sessions
        self._state: SessionsState | None = None
        self._phase = _Phase.IDLE
        self._prompt = ""
        self._result: SubmissionResult | None = None
        self._tick = 0
        self._target_slot = -1
        self._timer: Timer | None = None
        self._fade_timer: Timer | None = None
        self._log_lines: list[str] = []

    def on_mount(self) -> None:
        self._redraw()

    def on_unmount(self) -> None:
        self._cancel_timers()

    def on_resize(self, event: Resize) -> None:
        self._redraw()

    @property
    def phase(self) -> str:
        return self._phase.value

    def update_sessions(self, state: SessionsState, max_sessions: int | None = None) -> None:
        if max_sessions is not None:
            self._max = max_sessions
        self._state = state
        if self._phase == _Phase.IDLE:
            self._redraw()

    def start_routing(self, prompt: str) -> None:
        self._prompt = prompt[:60]
        self._phase = _Phase.ROUTING
        self._tick = 0
        self._result = None
        self._target_slot = -1
        self._cancel_timers()
        self._timer = self.set_interval(0.10, self._on_tick)
        self._redraw()

    def show_result(self, result: SubmissionResult) -> None:
        self._result = result
        self._target_slot = self._find_target_slot(result)
        self._phase = _Phase.TRAVELING
        self._tick = 0
        self._cancel_timers()
        self._timer = self.set_interval(0.05, self._on_tick)
        self._redraw()

    def add_log(self, line: str) -> None:
        self._log_lines.append(line)
        if len(self._log_lines) > MAX_LOG:
            self._log_lines = self._log_lines[-MAX_LOG:]
        if self._phase == _Phase.IDLE:
            self._redraw()

    def _cancel_timers(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._fade_timer is not None:
            self._fade_timer.stop()
            self._fade_timer = None

    def _on_tick(self) -> None:
        self._tick += 1
        if self._phase == _Phase.TRAVELING:
            travel_ticks = max(len(self._laser_path()), 1) + 4
            if self._tick >= travel_ticks:
                self._phase = _Phase.ARRIVED
                self._cancel_timers()
                self._fade_timer = self.set_timer(4.0, self._on_fade)
        self._redraw()

    def _on_fade(self) -> None:
        self._phase = _Phase.IDLE
        self._result = None
        self._prompt = ""
        self._target_slot = -1
        self._cancel_timers()
        self._redraw()

    def _screen_size(self) -> tuple[int, int]:
        try:
            width = max(self.size.width, MIN_W)
            height = max(self.size.height, MIN_H)
        except Exception:
            width, height = 100, 28
        return width, height

    def _sessions(self) -> list[Session]:
        return self._state.sessions if self._state else []

    def _field_layout(self) -> _FieldLayout:
        width, height = self._screen_size()
        return _compute_field_layout(width, height, self._max)

    def _stack_views(self) -> list[_StackView]:
        return _stack_views(self._field_layout(), self._sessions(), self._max)

    def _footer_rect(self) -> _Rect:
        width, height = self._screen_size()
        footer_y = max(height - FOOTER_H, 0)
        return _Rect(0, footer_y, width, height - footer_y)

    def _find_target_slot(self, result: SubmissionResult) -> int:
        if result.routing_decision.value == "ENQUEUE_GLOBAL":
            return -1
        if not result.assigned_session_id or not self._state:
            return -1
        for index, session in enumerate(self._state.sessions):
            if session.session_id == result.assigned_session_id:
                return index
        return -1

    def _target_stack(self, views: list[_StackView]) -> _StackView | None:
        if self._target_slot < 0:
            return None
        for view in views:
            if view.start_slot <= self._target_slot < view.end_slot:
                return view
        return None

    def _laser_path(self) -> list[tuple[int, int]]:
        views = self._stack_views()
        target = self._target_stack(views)
        if target is None:
            return []
        hub = _hub_rect(self._field_layout(), views)
        start = (hub.x + hub.w // 2, hub.y + hub.h // 2)
        return _line_points(start, target.center)

    def _redraw(self) -> None:
        width, height = self._screen_size()
        buf = [[" "] * width for _ in range(height)]
        sty: list[list[str | None]] = [[None] * width for _ in range(height)]

        layout = self._field_layout()
        views = self._stack_views()
        hub = _hub_rect(layout, views)
        footer = self._footer_rect()

        self._draw_stacks(buf, sty, views)
        self._draw_routing_effects(buf, sty, views, hub)
        self._draw_hub(buf, sty, hub)
        self._draw_footer(buf, sty, footer, layout, views)

        self.update(self._to_rich(buf, sty, height))

    def _draw_stacks(self, buf, sty, views: list[_StackView]) -> None:
        target = self._target_stack(views)
        for view in views:
            highlight = target is not None and view.index == target.index and self._phase in {_Phase.TRAVELING, _Phase.ARRIVED}
            self._draw_stack(buf, sty, view, highlight=highlight)

    def _draw_stack(self, buf, sty, view: _StackView, *, highlight: bool) -> None:
        top_session = view.top_session
        stack_style = STATUS_STYLE.get(top_session.status, "dim") if top_session else "dim"
        if highlight and self._phase == _Phase.TRAVELING:
            stack_style = "bold bright_white"
        if highlight and self._phase == _Phase.ARRIVED:
            stack_style = "bold bright_magenta"

        for layer in range(view.display_depth - 1, -1, -1):
            x = view.x + layer * STACK_OFFSET_X
            y = view.y + layer * STACK_OFFSET_Y
            style = "dim" if layer else stack_style
            self._draw_card(buf, sty, x, y, style)
            if layer == 0:
                self._draw_card_content(buf, sty, x, y, view, stack_style)

        if view.represented_count > 1:
            badge = f"×{view.represented_count}"
            badge_style = "bold bright_blue" if not highlight else stack_style
            self._stamp(buf, sty, view.y - 1, view.x + CARD_W - len(badge), badge, badge_style)

    def _draw_card(self, buf, sty, x: int, y: int, style: str | None) -> None:
        rect = _Rect(x, y, CARD_W, CARD_H)
        self._put(buf, sty, rect.y, rect.x, "┌", style)
        self._put(buf, sty, rect.y, rect.x2, "╮", style)
        self._put(buf, sty, rect.y2, rect.x, "└", style)
        self._put(buf, sty, rect.y2, rect.x2, "┘", style)
        for col in range(rect.x + 1, rect.x2):
            self._put(buf, sty, rect.y, col, "─", style)
            self._put(buf, sty, rect.y2, col, "─", style)
        for row in range(rect.y + 1, rect.y2):
            self._put(buf, sty, row, rect.x, "│", style)
            self._put(buf, sty, row, rect.x2, "│", style)

    def _draw_card_content(self, buf, sty, x: int, y: int, view: _StackView, style: str | None) -> None:
        session = view.top_session
        if session is None:
            line1 = _trunc(f"slot {view.start_slot + 1}", CARD_W - 2)
            line2 = "EMPTY"
            inner_style = "dim"
        else:
            line1 = _trunc(session.session_id.replace("sess_", "s:"), CARD_W - 2)
            line2 = STATUS_LABEL.get(session.status, session.status.value.upper())
            inner_style = style

        self._stamp(buf, sty, y + 1, x + 1, line1.ljust(CARD_W - 2), inner_style)
        self._stamp(buf, sty, y + 2, x + 1, line2.center(CARD_W - 2), inner_style)

    def _draw_routing_effects(self, buf, sty, views: list[_StackView], hub: _Rect) -> None:
        if self._phase == _Phase.ROUTING:
            self._draw_hub_pulse(buf, sty, hub)
            return

        path = self._laser_path()
        if not path:
            return

        if self._phase == _Phase.TRAVELING:
            visible = min(len(path), max(self._tick + 1, 1))
            self._draw_laser(buf, sty, path[:visible], head=True)
            return

        self._draw_laser(buf, sty, path, head=False)
        self._draw_target_glow(buf, sty, views)

    def _draw_hub_pulse(self, buf, sty, hub: _Rect) -> None:
        pulse_row = hub.y + hub.h // 2
        for offset in range(-3, 4):
            col = hub.x + hub.w // 2 + offset + (self._tick % 5) - 2
            self._put(buf, sty, pulse_row, col, "•", "bold bright_cyan")

    def _draw_laser(self, buf, sty, points: list[tuple[int, int]], *, head: bool) -> None:
        for index, (col, row) in enumerate(points):
            remaining = len(points) - index - 1
            if head and index == len(points) - 1:
                self._put(buf, sty, row, col, "◉", "bold bright_white")
                continue
            style = "bold bright_magenta"
            if remaining > 10:
                style = "magenta"
            if remaining > 20:
                style = "bright_blue"
            self._put(buf, sty, row, col, "•", style)

    def _draw_target_glow(self, buf, sty, views: list[_StackView]) -> None:
        target = self._target_stack(views)
        if target is None:
            return
        cx, cy = target.center
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            self._put(buf, sty, cy + dy, cx + dx, "·", "bold bright_magenta")

    def _draw_hub(self, buf, sty, rect: _Rect) -> None:
        is_routing = self._phase == _Phase.ROUTING
        is_result = self._phase in {_Phase.TRAVELING, _Phase.ARRIVED}
        is_global = (
            is_result
            and self._result is not None
            and self._result.routing_decision.value == "ENQUEUE_GLOBAL"
        )

        if is_routing:
            style = "bold bright_cyan"
        elif is_global:
            style = "bold bright_magenta"
        elif is_result:
            style = "bold bright_green"
        else:
            style = "dim"

        self._draw_panel(buf, sty, rect, style)
        if is_routing:
            line2 = f"{SPINNER[self._tick % len(SPINNER)]} routing"
            line3 = _trunc(self._prompt, rect.w - 4)
        elif is_result and self._result is not None:
            sid = self._result.assigned_session_id or "GLOBAL"
            line2 = self._result.routing_decision.value
            line3 = _trunc(f"target {sid}", rect.w - 4)
        else:
            line2 = "idle"
            line3 = _trunc(f"max {self._max} sessions", rect.w - 4)

        self._stamp_center(buf, sty, rect.y + 1, rect, "ROUTER CORE", style)
        self._stamp_center(buf, sty, rect.y + 2, rect, line2, style)
        self._stamp_center(buf, sty, rect.y + 3, rect, line3, style)

    def _draw_footer(self, buf, sty, rect: _Rect, layout: _FieldLayout, views: list[_StackView]) -> None:
        panel = _Rect(0, rect.y, rect.w, rect.h)
        self._draw_panel(buf, sty, panel, "dim")

        sessions = self._sessions()
        global_queue_size = len(self._state.global_queue) if self._state else 0
        rendered_slots = sum(view.represented_count for view in views)
        hidden_sessions = max(len(sessions) - rendered_slots, 0)

        line1 = f"sessions {len(sessions)}/{self._max}  stacks {len(views)}  covered_slots {rendered_slots}  gq {global_queue_size}"
        if hidden_sessions:
            line1 += f"  hidden_sessions {hidden_sessions}"
        pulse = "laser active" if self._phase in {_Phase.TRAVELING, _Phase.ARRIVED} else "standby"
        line2 = f"mode {self._phase.value}  {pulse}  group_size {layout.group_size}"

        self._stamp(buf, sty, panel.y + 1, 2, _trunc(line1, panel.w - 4), "dim")
        self._stamp(buf, sty, panel.y + 2, 2, _trunc(line2, panel.w - 4), "dim")

        available_log_rows = max(panel.h - 4, 0)
        if available_log_rows == 0:
            return
        if not self._log_lines:
            self._stamp(buf, sty, panel.y + 3, 2, _trunc("No submissions yet.", panel.w - 4), "dim")
            return
        for index, line in enumerate(self._log_lines[-available_log_rows:]):
            self._stamp(buf, sty, panel.y + 3 + index, 2, _trunc(line, panel.w - 4))

    def _draw_panel(self, buf, sty, rect: _Rect, style: str | None) -> None:
        self._put(buf, sty, rect.y, rect.x, "┌", style)
        self._put(buf, sty, rect.y, rect.x2, "┐", style)
        self._put(buf, sty, rect.y2, rect.x, "└", style)
        self._put(buf, sty, rect.y2, rect.x2, "┘", style)
        for col in range(rect.x + 1, rect.x2):
            self._put(buf, sty, rect.y, col, "─", style)
            self._put(buf, sty, rect.y2, col, "─", style)
        for row in range(rect.y + 1, rect.y2):
            self._put(buf, sty, row, rect.x, "│", style)
            self._put(buf, sty, row, rect.x2, "│", style)

    def _stamp_center(self, buf, sty, row: int, rect: _Rect, text: str, style: str | None) -> None:
        clipped = _trunc(text, rect.w - 2)
        start = rect.x + max((rect.w - len(clipped)) // 2, 1)
        self._stamp(buf, sty, row, start, clipped, style)

    def _put(self, buf, sty, row: int, col: int, ch: str, style: str | None = None) -> None:
        if 0 <= row < len(buf) and 0 <= col < len(buf[0]):
            buf[row][col] = ch
            if style is not None:
                sty[row][col] = style

    def _stamp(self, buf, sty, row: int, col: int, text: str, style: str | None = None) -> None:
        for offset, ch in enumerate(text):
            self._put(buf, sty, row, col + offset, ch, style)

    def _to_rich(self, buf, sty, height: int) -> str:
        lines: list[str] = []
        width = len(buf[0]) if buf else 0
        for row in range(height):
            parts: list[str] = []
            current_style = sty[row][0]
            current_chars = [buf[row][0]]
            for col in range(1, width):
                if sty[row][col] == current_style:
                    current_chars.append(buf[row][col])
                    continue
                text = "".join(current_chars)
                parts.append(f"[{current_style}]{text}[/]" if current_style else text)
                current_style = sty[row][col]
                current_chars = [buf[row][col]]
            text = "".join(current_chars)
            parts.append(f"[{current_style}]{text}[/]" if current_style else text)
            lines.append("".join(parts))
        return "\n".join(lines)


def _trunc(text: str, max_len: int) -> str:
    if max_len <= 0:
        return ""
    if len(text) <= max_len:
        return text
    if max_len == 1:
        return "…"
    return text[: max_len - 1] + "…"
