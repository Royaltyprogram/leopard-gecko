from rich.markup import render


def test_running_placeholder_markup_is_valid() -> None:
    text = "[dim][italic]Task is currently running…[/italic][/dim]"

    rendered = render(text)

    assert str(rendered) == "Task is currently running…"


def test_queued_placeholder_markup_is_valid() -> None:
    text = "[dim][italic]Task is queued[/italic][/dim]"

    rendered = render(text)

    assert str(rendered) == "Task is queued"
