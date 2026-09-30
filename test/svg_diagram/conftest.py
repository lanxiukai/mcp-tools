from pathlib import Path
import sys

import pytest

COMPONENT = Path(__file__).resolve().parents[2] / "svg-diagram"
sys.path.insert(0, str(COMPONENT))


@pytest.fixture(scope="session")
def svg_browser_ready():
    """CPU-only clone tests still work; full CI installs these optional binaries."""
    from playwright.sync_api import Error, sync_playwright

    with sync_playwright() as driver:
        try:
            browser = driver.chromium.launch(timeout=20000)
            browser.close()
        except Error as error:
            if "Executable doesn't exist" in str(error):
                pytest.skip("Install Playwright Chromium for SVG integration tests")
            raise
    if not (
        COMPONENT.parent / "format-conversion/node_modules/mathjax/package.json"
    ).is_file():
        pytest.skip("Restore the pinned MathJax runtime for SVG integration tests")


@pytest.fixture
def svg_page(svg_browser_ready):
    from svg_diagram.runtime import browser_page

    with browser_page() as page:
        yield page
