"""Assemble a standalone offline graph viewer."""
import html
import json

from knowledge_foundry.shared.paths import REPOSITORY_ROOT


def _json_for_script(value):
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_html(graph, title="Knowledge provenance graph"):
    """Return a deterministic, standalone local HTML viewer for a built graph."""
    asset_dir = REPOSITORY_ROOT / "apps/kpg_viewer"
    template = (asset_dir / "index.html").read_text(encoding="utf-8")
    styles = (asset_dir / "styles.css").read_text(encoding="utf-8")
    scripts = [
        (asset_dir / name).read_text(encoding="utf-8")
        for name in ("core.js", "graph.js", "detail.js", "app.js")
    ]
    script = f"window.graphData = {_json_for_script(graph)};\n" + "\n".join(scripts)
    return (template.replace("__TITLE__", html.escape(title))
            .replace("__STYLES__", styles)
            .replace("__SCRIPT__", script))
