#!/usr/bin/env python3
"""Assemble the offline PrimeVue UMD bundle: core foundation + component closure.

PrimeVue 3's ``core/core.min.js`` is a single, correctly-ordered file that
provides the *foundation* every component needs — base classes, the per-component
*styles*, all icons, ``config``, ``ripple``/``tooltip`` directives, the event
buses and composables (``usetoast``/``useconfirm``) — but it only embeds a
handful of actual component render modules. The remaining components ship one
UMD file each (``<name>/<name>.min.js``) whose IIFE runs immediately with its
dependency globals as arguments, so they must be appended *after* core and in
dependency order.

This script:
  1. downloads ``core/core.min.js`` (foundation),
  2. resolves the transitive closure of component files starting from the set
     the SPA registers (skipping anything core already provides),
  3. topologically sorts them so each component loads after its component deps,
  4. concatenates ``core`` + the ``ToastService``/``ConfirmationService``
     plugins (which core omits) + the ordered components into one file:
     ``static/vendor/primevue-all.min.js``.

Run via ``deploy/fetch-vendor.sh`` (or directly). Requires internet.
"""

from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

PV_VERSION = "3.53.1"
BASE = f"https://unpkg.com/primevue@{PV_VERSION}"
OUT = Path(__file__).resolve().parent.parent / "static" / "vendor" / "primevue-all.min.js"

# Components the SPA registers (see static/js/components.js). The resolver pulls
# in every component these transitively depend on.
SEED = [
    "button", "inputtext", "password", "textarea", "inputnumber", "inputswitch",
    "dropdown", "selectbutton", "slider", "knob", "card", "panel", "toolbar",
    "tag", "badge", "message", "divider", "datatable", "column", "tabview",
    "tabpanel", "panelmenu", "menu", "breadcrumb", "accordion", "accordiontab",
    "dialog", "confirmdialog", "toast", "progressbar", "fileupload", "skeleton",
    "scrollpanel", "avatar", "chart",
]

# Names already provided by core.min.js (foundation, styles, icons, directives,
# composables, event buses). We must NOT try to fetch these as component files.
FOUNDATION = {
    "base", "basecomponent", "baseicon", "basedirective", "baseeditableholder",
    "utils", "usestyle", "config", "api", "ripple", "tooltip", "icons", "portal",
    "overlayeventbus", "confirmationeventbus", "toasteventbus",
    "dynamicdialogeventbus", "focustrap", "useconfirm", "usetoast", "usedialog",
    "passthrough", "terminalservice", "animateonscroll", "styleclass",
    "badgedirective", "keyfilter", "inputtext",  # inputtext ships inside core
}

# These services are plugins that live in their own packages (core omits them).
SERVICES = ["toastservice", "confirmationservice"]

_ref_re = re.compile(r"primevue\.([a-zA-Z]+)(?:\.([a-zA-Z]+))?")


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (trusted CDN)
        return resp.read().decode()


def component_deps(name: str, source: str) -> set[str]:
    """Other *component* names referenced by ``source``.

    ``.style`` sub-references and foundation names are ignored because core
    already provides them; only component-to-component edges matter for order.
    """
    out: set[str] = set()
    for a, b in _ref_re.findall(source):
        if b == "style":  # primevue.<x>.style — provided by core
            continue
        if a in FOUNDATION or a == name:
            continue
        out.add(a)
    return out


def main() -> int:
    sources: dict[str, str] = {}
    styles: dict[str, str] = {}
    graph: dict[str, set[str]] = {}
    queue = list(SEED)
    while queue:
        name = queue.pop()
        if name in sources or name in FOUNDATION:
            continue
        try:
            src = fetch(f"{BASE}/{name}/{name}.min.js")
        except Exception as exc:  # noqa: BLE001 — 404 => foundation-only name
            print(f"  (foundation) {name}: {exc}", file=sys.stderr)
            FOUNDATION.add(name)
            continue
        sources[name] = src
        # Each component reads ``primevue.<name>.style`` (set by a separate style
        # module) at IIFE-invocation time. core.min.js sets these styles but then
        # embeds its own components which clobber them, so we re-load the style
        # module immediately before the component below.
        try:
            styles[name] = fetch(f"{BASE}/{name}/style/{name}style.min.js")
        except Exception:  # noqa: BLE001 — some components have no style module
            styles[name] = ""
        deps = component_deps(name, src)
        graph[name] = deps
        for dep in deps:
            if dep not in sources and dep not in FOUNDATION:
                queue.append(dep)

    # Topological sort (component deps first). Cycles broken arbitrarily.
    ordered: list[str] = []
    seen: set[str] = set()

    def visit(node: str, stack: set[str]) -> None:
        if node in seen or node in stack or node not in sources:
            return
        stack.add(node)
        for dep in sorted(graph.get(node, set())):
            visit(dep, stack)
        stack.discard(node)
        seen.add(node)
        ordered.append(node)

    for node in sorted(sources):
        visit(node, set())

    print("Downloading core foundation...")
    core = fetch(f"{BASE}/core/core.min.js")
    print("Downloading service plugins...")
    services = {s: fetch(f"{BASE}/{s}/{s}.min.js") for s in SERVICES}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    parts = [
        f"/* PrimeVue {PV_VERSION} offline bundle "
        "— generated by deploy/build_vendor_bundle.py */",
        "/* --- core (foundation + icons + composables + directives) --- */",
        core,
    ]
    for svc in SERVICES:
        parts.append(f"\n/* --- {svc} --- */\n{services[svc]}")
    for name in ordered:
        # style first (re-establishes primevue.<name>.style), then the component
        # which captures it via its closure before reassigning the namespace.
        if styles[name]:
            parts.append(f"\n/* --- {name} (style) --- */\n{styles[name]}")
        parts.append(f"\n/* --- {name} --- */\n{sources[name]}")
    OUT.write_text("\n".join(parts))
    print(
        f"Wrote {OUT} (core + {len(SERVICES)} services + "
        f"{len(ordered)} components, {OUT.stat().st_size} bytes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
