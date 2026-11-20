"""The public landing page, served at ``/``.

The markup lives in ``backend/templates/index.html`` (Jinja2) and its stylesheet in
``backend/static/styles/app.css``, which ``make web-css`` compiles from the ``source/`` and
``custom/`` folders beside it. The one running process serves the API, the page and its assets, so
a deployment is still a single container.

The download buttons are driven by three settings — ``app_store_url``, ``play_store_url`` and
``app_download_url`` (a direct link, e.g. an APK) — and each renders only when set. When none is set
the page shows an honest "coming soon" state instead of a dead link, which matches the project's
actual status.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

# src/kise/platform/api/landing.py -> backend/
BACKEND_ROOT = Path(__file__).resolve().parents[4]
TEMPLATES_DIR = BACKEND_ROOT / "templates"
STATIC_DIR = BACKEND_ROOT / "static"

STATIC_PATH = "/static"
STYLE_PATH = f"{STATIC_PATH}/styles"


def _style_version() -> str:
    """A short hash of the compiled stylesheet, added to its URL as ``?v=``.

    Static files are served without a cache lifetime, so browsers guess one and can keep an old
    stylesheet after a deploy (new pages then render unstyled). A URL that changes with the file
    makes every browser fetch the new one.
    """
    try:
        return hashlib.sha256((STATIC_DIR / "styles" / "app.css").read_bytes()).hexdigest()[:12]
    except OSError:
        return "dev"


STYLE_VERSION = _style_version()

_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(("html",)),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_landing_page(
    *,
    download_url: str = "",
    app_store_url: str = "",
    play_store_url: str = "",
    api_docs_path: str = "/docs",
) -> str:
    """Return the landing page HTML. All download URLs empty ⇒ a 'coming soon' button."""
    template = _env.get_template("index.html")
    return template.render(
        download_url=download_url,
        app_store_url=app_store_url,
        play_store_url=play_store_url,
        docs_path=api_docs_path,
        style_path=STYLE_PATH,
        style_version=STYLE_VERSION,
        static_path=STATIC_PATH,
    )
