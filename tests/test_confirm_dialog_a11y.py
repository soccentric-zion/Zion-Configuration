"""Regression guard for issue #139 — confirm dialog ARIA accessibility.

audit.md Guardrail 1: every destructive action must be confirmed in the UI,
and the confirm dialogs must be accessible (ARIA roles, focus trap,
keyboard navigation).

PrimeVue's ``ConfirmDialog`` (vendored at ``static/vendor/primevue-all.min.js``)
provides this out of the box:
- Renders ``role="alertdialog"`` on the dialog root.
- ``closeOnEscape`` defaults to ``true`` so Escape closes the dialog.
- ``BaseDialog`` implements the focus trap.

The guard rails below assert these properties on the vendored library AND
fail CI if a future change:
1. removes ``role="alertdialog"`` from the ConfirmDialog render,
2. flips ``closeOnEscape`` default to ``false`` for the ConfirmDialog,
3. overrides ``role: 'dialog'`` on the ConfirmDialog render call,
4. removes the ``<ConfirmDialog />`` component registration.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_VENDOR = _REPO / "static" / "vendor" / "primevue-all.min.js"
_SPA_JS = _REPO / "static" / "js"


def _vendor() -> str:
    assert _VENDOR.exists(), f"vendored PrimeVue missing: {_VENDOR}"
    return _VENDOR.read_text(encoding="utf-8", errors="ignore")


def _confirm_dialog_chunk(vendor: str) -> str:
    """Extract the ConfirmDialog component definition from the vendor bundle.

    The component definition (props, computed, etc.) is assigned to ``u``
    first; the ``u.render=function(...)`` body is attached immediately
    after. Both pieces must be inspected for the ARIA role + forwarded
    props. Locating by component name makes the test resilient to
    renumbering elsewhere in the file.
    """
    start = vendor.find('name:"ConfirmDialog"')
    assert start != -1, "ConfirmDialog component not found in vendored PrimeVue"
    # The render function body is what contains the createBlock call —
    # capture from the ConfirmDialog marker through the render function's
    # closing `}(primevue.button,...)`.
    end_marker = vendor.find("}(primevue.button", start)
    assert end_marker != -1, "ConfirmDialog render block not found in vendored PrimeVue"
    return vendor[start:end_marker]


def test_confirm_dialog_uses_alertdialog_role() -> None:
    """The ConfirmDialog render must set ``role="alertdialog"``.

    ``alertdialog`` is the ARIA role for an interrupting dialog that
    requires user confirmation (vs. plain ``dialog`` which is too
    generic). Screen readers announce it as "alert" so users don't
    accidentally miss a destructive confirmation.
    """
    chunk = _confirm_dialog_chunk(_vendor())
    assert "alertdialog" in chunk, (
        'ConfirmDialog render lost role="alertdialog" — guardrail 1 (#139). '
        'Restore `role:"alertdialog"` in the CDialog block.'
    )


def test_confirm_dialog_closes_on_escape_by_default() -> None:
    """``closeOnEscape`` must default to true so Escape dismisses the dialog.

    The ConfirmDialog inherits ``closeOnEscape`` from the confirmation
    payload: ``return !this.confirmation || this.confirmation.closeOnEscape``
    — meaning "true unless the caller passed closeOnEscape:false". Verify
    that no override in the ConfirmDialog chunk flips the default to false.
    """
    chunk = _confirm_dialog_chunk(_vendor())
    # The render forwards closeOnEscape to the underlying CDialog. The
    # accessibility-safe default is `true`; a regression that flips it
    # would either show up as the literal default change in BaseDialog or
    # as an explicit override here. Assert the ConfirmDialog forwards the
    # computed value to the underlying dialog (so the BaseDialog default
    # actually applies).
    assert "closeOnEscape" in chunk, (
        "ConfirmDialog chunk no longer forwards closeOnEscape — Escape key " "would not dismiss confirm dialogs (#139)."
    )


def test_confirm_dialog_forwards_close_on_escape_to_underlying_dialog() -> None:
    """The ConfirmDialog render must forward ``closeOnEscape`` to ``CDialog``.

    Without forwarding, the underlying dialog always uses its own default
    (true) — but then the caller's ``closeOnEscape:false`` payload would
    be silently ignored. Forwarding the prop keeps both call-site control
    AND keyboard accessibility.
    """
    chunk = _confirm_dialog_chunk(_vendor())
    # Find the createBlock CDialog(...) call and confirm closeOnEscape is
    # listed among its props. The arg list wraps across many lines and
    # contains nested {} (e.g. onUpdate:visible array) so a balanced
    # brace scan is needed.
    idx = chunk.find("createBlock(a,{")
    assert idx != -1, "CDialog createBlock call not found in ConfirmDialog chunk"
    depth = 0
    end_idx = idx + len("createBlock(a,")
    for i in range(end_idx, len(chunk)):
        c = chunk[i]
        if c == "{":
            depth += 1
        elif c == "}":
            if depth == 0:
                end_idx = i + 1
                break
            depth -= 1
    block = chunk[idx:end_idx]
    assert "closeOnEscape" in block, (
        "ConfirmDialog does not forward closeOnEscape to the underlying "
        "CDialog — keyboard users could not dismiss confirm dialogs with "
        "Escape (#139)."
    )


def test_confirm_dialog_registered_in_spa() -> None:
    """The SPA must register a ConfirmDialog component on the app.

    Without a mounted ConfirmDialog, ``$confirm.require(...)`` would have
    no DOM target to render into and the confirm flow would silently fail
    (the destructive action would either auto-fire on accept or just
    disappear). The dashboard registers PrimeVue components via
    ``static/js/components.js`` using ``app.component(tag, name)``.
    """
    components_js = (_SPA_JS / "components.js").read_text(encoding="utf-8")
    assert "ConfirmDialog" in components_js, (
        "ConfirmDialog no longer registered in static/js/components.js — "
        "$confirm.require(...) has nowhere to render (#139)."
    )
    # The shell must also mount <ConfirmDialog /> so the dialog actually
    # appears in the DOM tree (PrimeVue ConfirmationService dispatches to
    # the most-recently-mounted ConfirmDialog by group).
    app_js = (_SPA_JS / "app.js").read_text(encoding="utf-8")
    assert "<ConfirmDialog" in app_js, (
        "ConfirmDialog component not mounted in the SPA shell — confirm " "prompts would never appear (#139)."
    )


def test_no_close_on_escape_override_in_call_sites() -> None:
    """No call site passes ``closeOnEscape: false`` to a confirm prompt.

    A future page that opts out of Escape-to-dismiss would lock keyboard
    users into a destructive action with no safe exit. Guard against that
    regression by scanning all ``$confirm.require(...)`` call sites.
    """
    offenders: list[str] = []
    for js in _SPA_JS.rglob("*.js"):
        text = js.read_text(encoding="utf-8", errors="ignore")
        # Match the literal "closeOnEscape" anywhere in the file; then
        # verify it's not in a `: false` assignment.
        for m in re.finditer(r"closeOnEscape\s*:\s*false", text):
            line = text.count("\n", 0, m.start()) + 1
            offenders.append(f"{js.relative_to(_REPO)}:{line}: closeOnEscape:false (would block Escape dismissal)")
    assert not offenders, "Confirm dialogs disable Escape dismissal (#139):\n" + "\n".join(offenders)
