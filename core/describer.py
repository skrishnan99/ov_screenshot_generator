"""Vision descriptions of captured screenshots.

Every screenshot is passed through the model to produce a thorough text
description of what it shows in the context of the OV camera product. The
descriptions are the durable record of what the recipe was doing — downstream
consumers (e.g. PPT generation) use them both for content and to decide which
screenshot belongs where — so they must be exhaustive and accurate.
"""

from __future__ import annotations

import base64

import anthropic

MODEL = "claude-opus-5"

PROMPT = """You are documenting a screenshot from the web UI of an Overview AI industrial \
vision camera ({variant}). These descriptions are the authoritative record of what this \
inspection recipe is configured to do: they will later be used to reconstruct the recipe's \
intent and to build other assets (e.g. slide decks), including deciding which screenshot \
belongs in which slot. Be exhaustive and precise — downstream consumers see only your text, \
not the image.

Domain knowledge about this product (use it to INTERPRET what you see, not just transcribe; \
if the UI contradicts any of this, describe what you actually see):
- A recipe's configuration pipeline is: Imaging Setup -> Template Image & Alignment -> \
Inspection Setup (ROIs assigned to models) -> AI blocks (Classification = a class decision \
per ROI; Segmentation = pixel-level defect masks; also OCR / Unsupervised / Measurement) -> \
Train -> IO Logic (Node-RED). Say where the shown screen sits in this workflow.
- "PLC Recipe ID" is the user-assigned integer identifying this recipe to the plant PLC over \
the fieldbus (distinct from any internal id). The "Recipe Active/Inactive" pill means whether \
this recipe is the one currently selected/running on the camera.
- Trigger modes: "Manual HMI Trigger" = captures are taken from the HMI's capture button; \
"PLC Trigger" = the plant PLC triggers a capture each cycle (the camera is in-line).
- "Skip Aligner" disables the alignment step: ROIs are placed directly on the captured image \
(typical for fixtured parts); alignment otherwise tracks the part via template + search areas.
- Training counts: labeled REGIONS can exceed distinct IMAGES (multiple ROIs per capture) — \
report both when visible and don't conflate them.
- Class names starting with "pass"/"fail" (e.g. pass_hole_presence) conventionally drive the \
IO pass/fail rule: a classification passes when its predicted class name begins with "pass".
- Imaging features worth explicitly flagging when visible: photometric stereo, HDR mode, \
interval trigger, image rotation, LED strobe — these materially change the inspection.

Context for this screenshot:
- Recipe: {recipe}
- Screen / step: {step}
- What the capture flow was doing: {intent}
{item_line}
Write a thorough plain-text description covering:
1. Which screen/page of the camera UI this is and its role in the product's inspection \
workflow.
2. Every meaningful piece of information visible: recipe name and status, settings and \
their exact values (exposure, gain, gamma, trigger mode, resolution, ...), model names and \
types, class names with their counts/colors, ROI names and counts, capture navigation info \
(capture number/ID/timestamps/notes), version and serial info, toggles and their states, \
enabled/disabled buttons, banners or warnings, open modals.
3. What the image/canvas area shows, if present: the inspected part, overlays, ROI boxes, \
annotations/segmentation masks and their colors, image quality (e.g. dark/unlit vs \
populated).
4. Anything that reveals the recipe's inspection intent (what is being inspected, for \
which defects/classes, with what approach).

Do not speculate beyond what is visible, and do not omit details because they seem minor. \
Write flowing prose (no markdown headers), roughly 150-400 words."""


IMAGE_LOADED_SCHEMA = {
    "type": "object",
    "properties": {
        "loaded": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["loaded", "reason"],
    "additionalProperties": False,
}

IMAGE_LOADED_PROMPT = """This is a screenshot of a page from an Overview AI industrial vision \
camera's web UI ({hint}). The page contains a main capture/inspection image area that may still \
be loading.

Judge whether the main image area (a single large image, or a grid of image thumbnails) has \
FULLY finished loading and rendering:
- loaded = true when actual camera imagery is visible — including dark or low-light \
photographs, as long as real texture, scene detail, or drawn overlays (ROI boxes, \
annotations, masks) are present. For a thumbnail grid, all visible tiles must show imagery.
- loaded = false when the image area (or some grid tiles) is a uniform blank/black/grey \
placeholder with no texture or overlays, shows a loading spinner or progress indicator, shows \
a "Connecting to camera" or similar overlay, or is visibly partially rendered.

Judge only the main image area — side panels, toolbars, and tables being loaded does NOT mean \
the image is loaded. Answer with loaded and a one-sentence reason."""


def check_image_loaded(png_bytes: bytes, hint: str = "") -> dict:
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        output_config={"format": {"type": "json_schema", "schema": IMAGE_LOADED_SCHEMA}},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.standard_b64encode(png_bytes).decode(),
                        },
                    },
                    {"type": "text", "text": IMAGE_LOADED_PROMPT.format(hint=hint or "n/a")},
                ],
            }
        ],
    )
    if response.stop_reason == "refusal":
        return {"loaded": False, "reason": "vision check refused"}
    import json as _json

    text = "".join(b.text for b in response.content if b.type == "text")
    return _json.loads(text)


def poll_image_loaded(
    browser, max_wait_s: float = 90, interval_s: float = 7, log=print
) -> tuple[bool, str]:
    """Poll with independent stateless vision checks until the page's main
    image content is fully rendered. Shared by the navigator tool and the
    deterministic capture loops."""
    elapsed = 0.0
    while True:
        try:
            verdict = check_image_loaded(
                browser.screenshot_bytes(), hint=browser.page.title()
            )
        except Exception as e:
            verdict = {"loaded": False, "reason": f"vision check error: {e}"}
        log(
            f"  image-load check at {elapsed:.0f}s -> "
            f"{'loaded' if verdict['loaded'] else 'not loaded'} ({verdict['reason'][:70]})"
        )
        if verdict["loaded"]:
            return True, f"loaded after {elapsed:.0f}s: {verdict['reason']}"
        if elapsed >= max_wait_s:
            return False, f"still NOT loaded after {max_wait_s:.0f}s: {verdict['reason']}"
        browser.page.wait_for_timeout(int(interval_s * 1000))
        elapsed += interval_s


NODE_RED_PROMPT = """Below is the exported Node-RED flow configuration (JSON) from an Overview AI \
industrial vision camera ({variant}), recipe "{recipe}". Node-RED implements the camera's IO / \
pass-fail logic: how inspection results are turned into outputs (PLC signals, GPIO, network \
messages, etc.).

Domain knowledge about Overview Node-RED flows (use it to interpret; if this flow differs, \
describe what the flow actually contains):
- Canonical Overview node types: `overview-unified-pipeline-input` (inspection results from \
the camera's AI pipeline enter the flow here), `final-pass-fail` (sets the camera's own \
verdict, statistics, and mapped hardware IO), `classification-block-logic`, \
`format-data-for-plc`, `ethernet-ip-user-data-write` (EtherNet/IP write to the plant PLC), \
`global-config`.
- Common conventions: MQTT topics like `camera/trigger`, `overview/inspection/result`, \
`overview/inspection/report`; function-node variables like `min_mark_area_px` (segmentation \
blob-area threshold) and `edas_roi_filter`; classification results conventionally pass when \
the predicted class name begins with "pass" (e.g. pass_hole_presence).
- Node-RED stores credentials in a separate encrypted file — the flow JSON containing no \
passwords/tokens is expected and not worth noting.

Write a thorough Markdown summary (`node_red_description.md`) of what this flow does and what it \
is trying to achieve in the context of the vision inspection setup. Cover:

1. **Overview** — what the flow as a whole accomplishes, in plain language.
2. **Logic walk-through** — the nodes and how they are wired: triggers/inputs (inspection \
results, MQTT/HTTP endpoints, buttons), the decision logic (functions, switches, conditions — \
quote and explain any embedded code), and the outputs (GPIO pins, PLC/industrial-ethernet \
writes, network calls, debug nodes), including timing elements (delays, triggers, debounce).
3. **Inspection context** — how this maps to the recipe's pass/fail behavior: what happens \
when a part passes vs fails, which model results or classes are referenced (name them exactly \
as they appear), and any thresholds or counts involved.
4. **Notable details** — disabled nodes, dead ends, hardcoded values, environment-specific \
configuration (IPs, topics, pin numbers), and anything that looks unfinished or unusual.

Be accurate and exhaustive — downstream consumers rely on this summary instead of reading the \
JSON. Do not speculate beyond what the configuration contains.

Node-RED flow JSON:
```json
{flow_json}
```"""


def describe_node_red(flow_json: str, context: dict) -> str:
    client = anthropic.Anthropic()
    prompt = NODE_RED_PROMPT.format(
        variant=context.get("variant", "unknown variant"),
        recipe=context.get("recipe", "unknown"),
        flow_json=flow_json,
    )
    with client.messages.stream(
        model=MODEL, max_tokens=8000, messages=[{"role": "user", "content": prompt}]
    ) as stream:
        response = stream.get_final_message()
    if response.stop_reason == "refusal":
        return "[node-red description refused by model]"
    return "".join(b.text for b in response.content if b.type == "text").strip()


def describe_screenshot(png_bytes: bytes, context: dict) -> str:
    item = context.get("item")
    item_line = f"- Specific item captured: {item}\n" if item else ""
    prompt = PROMPT.format(
        variant=context.get("variant", "unknown variant"),
        recipe=context.get("recipe", "unknown"),
        step=context.get("step", "unknown"),
        intent=" ".join((context.get("intent") or "").split()) or "n/a",
        item_line=item_line,
    )
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=3000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.standard_b64encode(png_bytes).decode(),
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    if response.stop_reason == "refusal":
        return "[description refused by model]"
    return "".join(b.text for b in response.content if b.type == "text").strip()
