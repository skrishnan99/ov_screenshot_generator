"""Trace cache: record what the agent did, replay it deterministically later.

A trace is a list of actions keyed by (variant, ui_version). Steps whose target
text/row mentioned the recipe the agent matched are marked recipe-scoped; on
replay, the requested recipe is first resolved to its exact on-screen name by
an LLM (core.resolver) — the recipe set may have changed since the trace was
recorded, so name resolution is never done with string heuristics. Clicking is
then deterministic: the resolved name must appear verbatim in the target's row.
Any failure or non-confident resolution aborts the replay so the caller falls
back to the full agent — replay must never guess.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from core.paths import traces_dir

FIND_ATTEMPTS = 4
FIND_RETRY_MS = 1500


def trace_path(variant: str, version_key: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "._-" else "-" for c in version_key)
    return traces_dir() / variant / f"{safe}.json"


def load(variant: str, version_key: str) -> dict | None:
    p = trace_path(variant, version_key)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def _row_state(ctx: str) -> str:
    # "Inactive" contains "active"; check it first, case-sensitively.
    if "Inactive" in ctx:
        return "inactive"
    if "Active" in ctx:
        return "active"
    return "unknown"


def save(variant: str, version_key: str, step_id: str, actions: list[dict], matched_recipe: str):
    p = trace_path(variant, version_key)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(p.read_text()) if p.exists() else {"steps": {}}
    branch = "unknown"
    for a in actions:
        a["recipe_scoped"] = bool(
            matched_recipe
            and (matched_recipe in a.get("text", "") or matched_recipe in a.get("ctx", ""))
        )
        if a["recipe_scoped"] and branch == "unknown":
            branch = _row_state(a.get("ctx", ""))
    data["steps"][step_id] = {
        "matched_recipe": matched_recipe,
        "branch": branch,
        "actions": actions,
    }
    p.write_text(json.dumps(data, indent=2))


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _find_ref(last_items: dict, action: dict, resolved_recipe: str | None) -> int | None:
    candidates = [
        it
        for it in last_items.values()
        if it["tag"] == action.get("tag") and not it.get("disabled")
    ]
    if action.get("recipe_scoped"):
        if not resolved_recipe:
            return None
        # Same label (e.g. "Edit") appears once per row; the right one is the
        # one whose row contains the LLM-resolved name verbatim. Multiple hits
        # are duplicate buttons in that row (or identically-named recipes) —
        # similarity to the originally recorded row context picks between them.
        labeled = [it for it in candidates if it["text"] == action["text"]] or candidates
        needle = resolved_recipe.lower()
        matches = [
            it
            for it in labeled
            if needle in (it.get("ctx", "") + " " + it["text"]).lower()
        ]
        if not matches:
            return None
        recorded_ctx = action.get("ctx", "")
        matches.sort(
            key=lambda it: _similarity(recorded_ctx, it.get("ctx", "")), reverse=True
        )
        return matches[0]["ref"]
    exact = [it for it in candidates if it["text"] == action["text"]]
    if len(exact) == 1:
        return exact[0]["ref"]
    if len(exact) > 1:
        same_ctx = [it for it in exact if it.get("ctx", "") == action.get("ctx", "")]
        if len(same_ctx) == 1:
            return same_ctx[0]["ref"]
    return None


def _stable_snapshot(browser, max_wait_ms: int = 9000, interval_ms: int = 1500) -> str:
    """Snapshot once the page stops changing — async lists render late."""
    prev = browser.snapshot()
    waited = 0
    while waited < max_wait_ms:
        browser.page.wait_for_timeout(interval_ms)
        waited += interval_ms
        cur = browser.snapshot()
        if cur == prev:
            return cur
        prev = cur
    return prev


def replay(
    browser, step: dict, recipe: str | None, resolve, resolved: str | None = None, log=print
) -> tuple[bool, str, str | None]:
    """Returns (ok, why, resolved_recipe_name).

    `resolved` carries the exact on-screen recipe name when an earlier step in
    this run already resolved it; resolution then isn't repeated.
    """

    def ensure_resolved() -> str | None:
        nonlocal resolved
        if resolved is None:
            if not recipe or resolve is None:
                return None
            r: dict = {}
            for attempt in range(3):
                snap = _stable_snapshot(browser)
                r = resolve(recipe, snap)
                if r.get("status") == "matched" and r.get("name"):
                    log(f'  resolve: "{recipe}" -> "{r["name"]}"')
                    resolved = r["name"]
                    return resolved
                if r.get("status") != "not_found":
                    break  # genuine ambiguity won't improve with waiting
                # A slow-rendering list looks like "no recipes"; give it time.
                log(f"  resolve attempt {attempt + 1}: not_found; waiting for page")
                browser.page.wait_for_timeout(4000)
            log(f"  resolve failed: {r}")
            return None
        return resolved

    for action in step["actions"]:
        if action["action"] == "click_text":
            text = action["text"]
            if action.get("recipe_scoped"):
                # The recorded text is a specific recipe's name (e.g. a
                # breadcrumb); substitute the recipe this run is about.
                if ensure_resolved() is None:
                    return False, "replay: could not resolve recipe for click_text", resolved
                text = resolved
            result = browser.click_text(text)
        else:
            if action.get("recipe_scoped") and ensure_resolved() is None:
                return False, "replay: recipe resolution failed", resolved
            # The UI renders lists asynchronously; retry with fresh snapshots
            # before concluding the target is gone.
            ref = None
            for _ in range(FIND_ATTEMPTS):
                browser.snapshot()
                ref = _find_ref(browser.last_items, action, resolved)
                if ref is not None:
                    break
                browser.page.wait_for_timeout(FIND_RETRY_MS)
            if ref is None:
                return False, f"replay: no confident target for {action}", resolved
            if action.get("recipe_scoped"):
                # A trace records one branch of the flow (Edit for active
                # recipes, Activate for inactive ones). Replaying it against a
                # recipe in the other state would skip the intended flow.
                recorded = step.get("branch", "unknown")
                live = _row_state(browser.last_items[ref].get("ctx", ""))
                if recorded != "unknown" and live != "unknown" and recorded != live:
                    return (
                        False,
                        f"replay: recipe is now {live} but trace was recorded for {recorded}",
                        resolved,
                    )
            if action["action"] == "click":
                result = browser.click(ref)
            elif action["action"] == "type_text":
                result = browser.type_text(ref, action["value"])
            else:
                return False, f"replay: unknown action {action['action']}", resolved
        log(f"  replay: {action['action']} \"{action.get('text', '')}\" -> {result[:80]}")
        if result.startswith("Error"):
            return False, f"replay: {result}", resolved
    return True, "ok", resolved
