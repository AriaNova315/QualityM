from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_alpine_factory_is_defined_before_dynamic_runtime_load() -> None:
    # Regression: ISSUE-001 — Alpine initialized before qualityMApp existed.
    # Found by /qa on 2026-08-21.
    # Report: .gstack/qa-reports/qa-report-qualitym-2026-08-21.md
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "app/static/app.js").read_text(encoding="utf-8")

    assert 'type="module" src="/static/app.js"' in html
    assert "cdn.jsdelivr.net/npm/alpinejs" not in html
    assert javascript.index("window.qualityMApp") < javascript.index(
        'document.createElement("script")'
    )


def test_mobile_forms_stack_to_prevent_upload_action_overflow() -> None:
    # Regression: ISSUE-002 — the template import button overflowed at 375px.
    # Found by /qa on 2026-08-21.
    # Report: .gstack/qa-reports/qa-report-qualitym-2026-08-21.md
    stylesheet = (ROOT / "app/static/styles.css").read_text(encoding="utf-8")

    mobile = stylesheet.split("@media (max-width: 640px)", maxsplit=1)[1]
    assert "form { flex-direction: column; align-items: stretch; }" in mobile
    assert 'input[type="file"] { width: 100%; min-width: 0; }' in mobile
