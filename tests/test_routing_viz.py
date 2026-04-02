from leopard_gecko.tui.widgets.routing_viz import (
    CARD_H,
    STACK_OFFSET_Y,
    _compute_field_layout,
    _hub_rect,
    _line_points,
    _stack_views,
)


def test_field_layout_spreads_stacks_across_multiple_rows() -> None:
    layout = _compute_field_layout(120, 30, 100)

    assert layout.visible_stacks > 1
    assert layout.rows > 1
    assert layout.group_size >= 1


def test_stack_views_group_sessions_when_slots_exceed_anchor_count() -> None:
    layout = _compute_field_layout(80, 24, 100)
    views = _stack_views(layout, [], 100)

    assert views
    assert any(view.represented_count > 1 for view in views)


def test_stack_origins_use_multiple_vertical_positions() -> None:
    layout = _compute_field_layout(120, 30, 100)
    views = _stack_views(layout, [], 100)

    rows = {view.y for view in views}

    assert len(rows) > 1


def test_hub_prefers_empty_space_below_stacks() -> None:
    layout = _compute_field_layout(160, 48, 100)
    views = _stack_views(layout, [], 100)

    hub = _hub_rect(layout, views)
    stacks_bottom = max(view.y + CARD_H + (view.display_depth - 1) * STACK_OFFSET_Y for view in views)

    assert hub.y > stacks_bottom


def test_line_points_include_start_and_end() -> None:
    points = _line_points((10, 10), (20, 15))

    assert points[0] == (10, 10)
    assert points[-1] == (20, 15)
    assert len(points) >= 2
