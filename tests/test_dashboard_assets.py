from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path


DASHBOARD = Path(__file__).parents[1] / "src" / "bananloop" / "dashboard"


class DashboardParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.labels: set[str] = set()
        self.controls: list[tuple[str, dict[str, str | None]]] = []
        self.buttons: list[str] = []
        self._button_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.append(attrs["id"])
        if tag == "label" and attrs.get("for"):
            self.labels.add(attrs["for"])
        if tag in {"input", "select", "textarea"}:
            self.controls.append((tag, attrs))
        if tag == "button":
            self.buttons.append("")
            self._button_depth += 1

    def handle_endtag(self, tag):
        if tag == "button" and self._button_depth:
            self._button_depth -= 1

    def handle_data(self, data):
        if self._button_depth and self.buttons:
            self.buttons[-1] += data


def test_dashboard_assets_have_local_keyboard_and_screen_reader_basics():
    html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    css = (DASHBOARD / "dashboard.css").read_text(encoding="utf-8")
    js = (DASHBOARD / "dashboard.js").read_text(encoding="utf-8")
    parser = DashboardParser()
    parser.feed(html)

    assert len(parser.ids) == len(set(parser.ids))
    assert all(attrs.get("aria-label") or attrs.get("aria-labelledby") or attrs.get("id") in parser.labels
               for _tag, attrs in parser.controls)
    assert all(button.strip() for button in parser.buttons)
    assert "<main>" in html and "<header" in html and "<footer>" in html
    assert 'role="status"' in html and 'aria-live="polite"' in html
    assert "http://" not in html and "https://" not in html
    assert "localStorage" not in js and "sessionStorage" not in js and "indexedDB" not in js
    assert "textContent" in js and "selectionGeneration" in js
    assert ":focus-visible" in css and "@media (max-width: 760px)" in css
