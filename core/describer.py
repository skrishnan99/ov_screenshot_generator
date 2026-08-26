"""Vision descriptions of captured screenshots.

Every screenshot is passed through the model to produce a thorough text
description of what it shows in the context of the OV camera product. The
descriptions are the durable record of what the recipe was doing — downstream
consumers (e.g. PPT generation) use them both for content and to decide which
screenshot belongs where — so they must be exhaustive and accurate.
"""

from __future__ import annotations

from core import llm
from core.llm import LLMRefusal, complete

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
- The "IO Logic" tab has TWO modes, both valid representations of the recipe's pass/fail \
logic: Advanced (an embedded Node-RED flow editor) and Basic (a "Pass/Fail & IO Logic" page \
with Classification/Segmentation rule builders beside a capture preview).
- Imaging features worth explicitly flagging when visible: photometric stereo, HDR mode, \
interval trigger, image rotation, LED strobe — these materially change the inspection.

Context for this screenshot:
- Recipe: {recipe}
- Screen / step: {step}
- What the capture flow was doing: {intent}
{item_line}{models_block}
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
Write flowing prose (no markdown headers), roughly 150-400 words.

Alongside the prose, extract `facts`: every discrete, checkable value visible on the screen, \
as {{subject, property, value}} entries with values copied VERBATIM from the UI. Subjects: \
"recipe", "camera", "model: <model name>", "class: <model name>/<class name>". Suggested \
property vocabulary (use these names when they fit; add others freely): train_accuracy, \
val_accuracy, training_loss, mean_iou, training_images, iterations, class_count, roi_count, \
label_count, class_color, capture_count, capture_id, plc_recipe_id, recipe_status, \
trigger_mode, resolution, exposure_ms, gain, gamma, white_balance, firmware_version, serial, \
last_trained, deployment_status. Only facts actually visible — an empty list is valid."""

DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {
            "type": "string",
            "description": "The thorough prose description, per the instructions.",
        },
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "property": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["subject", "property", "value"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["description", "facts"],
    "additionalProperties": False,
}


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


TABLE_LOADED_SCHEMA = {
    "type": "object",
    "properties": {
        "loaded": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["loaded", "reason"],
    "additionalProperties": False,
}

TABLE_LOADED_PROMPT = """This is a screenshot of a page from an Overview AI industrial vision \
camera's web UI ({hint}). The page contains a DATA TABLE — for example the list of AI models \
with their training status, metrics and controls — which may still be loading.

Judge whether the table has FINISHED loading:
- loaded = true when the rows show real content: model names, numbers, status badges, dates or \
action buttons. A table that has finished loading and legitimately has NO rows also counts as \
loaded (an explicit empty state, or a visible "no data" message) — there is nothing to wait for.
- loaded = false when the rows are skeleton placeholders — uniform grey or blank bars, pills and \
circles standing in for text and controls — or a spinner or progress indicator is shown.

Judge the table BODY only. Column headers render immediately and are never row content: a table \
whose headers ("Model", "Status", "Last Trained", ...) are readable while every cell beneath them \
is a plain grey bar is STILL LOADING, not loaded. Answer with loaded and a one-sentence reason."""


def check_table_loaded(png_bytes: bytes, hint: str = "") -> dict:
    # SONNET, same rationale as the image check: measured faster than Haiku
    # per call on the agent-sdk transport, and a sharper read of skeleton
    # rows vs real content.
    try:
        return complete(
            TABLE_LOADED_PROMPT.format(hint=hint or "n/a"),
            schema=TABLE_LOADED_SCHEMA,
            images=[png_bytes],
            max_tokens=1000,
            model=llm.SONNET,
        )
    except LLMRefusal:
        return {"loaded": False, "reason": "table check refused"}


def poll_table_loaded(
    browser, max_wait_s: float = 60, interval_s: float = 5, log=print
) -> tuple[bool, str]:
    """Poll until a page's data table is populated.

    The counterpart to poll_image_loaded, and the reason it exists: the Train
    Models page renders its skeleton rows instantly, so a capture taken too
    early looks like a finished page with no models. Worse, enumeration then
    reads the only real text present — the column headers — and "Model"
    becomes a model name, which sent a run hunting for a training report that
    could not exist until it exhausted its turn budget.
    """
    elapsed = 0.0
    while True:
        try:
            verdict = check_table_loaded(
                browser.screenshot_bytes(), hint=browser.page.title()
            )
        except Exception as e:
            verdict = {"loaded": False, "reason": f"table check error: {e}"}
        log(
            f"  table-load check at {elapsed:.0f}s -> "
            f"{'loaded' if verdict['loaded'] else 'not loaded'} ({verdict['reason'][:70]})"
        )
        if verdict["loaded"]:
            return True, f"loaded after {elapsed:.0f}s: {verdict['reason']}"
        if elapsed >= max_wait_s:
            return False, f"still NOT loaded after {max_wait_s:.0f}s: {verdict['reason']}"
        browser.page.wait_for_timeout(int(interval_s * 1000))
        elapsed += interval_s


def check_image_loaded(png_bytes: bytes, hint: str = "") -> dict:
    # SONNET, deliberately: this is the hottest inner loop in every capture
    # wait (dozens of calls per run, more since the pick loops), and on the
    # agent-sdk transport Sonnet measured FASTER than Haiku per identical
    # vision call (6.5s vs 14.6s — session overhead dominates; see the
    # tier-policy caveats in core/llm.py). Accuracy tier is a bonus here,
    # not the motivation.
    try:
        return complete(
            IMAGE_LOADED_PROMPT.format(hint=hint or "n/a"),
            schema=IMAGE_LOADED_SCHEMA,
            images=[png_bytes],
            max_tokens=1000,
            model=llm.SONNET,
        )
    except LLMRefusal:
        return {"loaded": False, "reason": "vision check refused"}


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

Alongside the markdown, extract `facts`: the discrete decision-logic values as \
{{subject, property, value}} entries, values verbatim from the flow. Suggested properties: \
pass_rule (one per rule, stated plainly), blob_area_threshold_px, plc_output (what is written \
and its meaning), plc_protocol, master_endpoint, mqtt_topic, camera_role. Subject is usually \
"io_logic". Only facts present in the configuration.

Node-RED flow JSON:
```json
{flow_json}
```"""

NODE_RED_SCHEMA = {
    "type": "object",
    "properties": {
        "markdown": {
            "type": "string",
            "description": "The thorough Markdown summary, per the instructions.",
        },
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "property": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["subject", "property", "value"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["markdown", "facts"],
    "additionalProperties": False,
}


def describe_node_red(flow_json: str, context: dict) -> dict:
    """Returns {"markdown": analysis, "facts": [{subject, property, value}]}."""
    prompt = NODE_RED_PROMPT.format(
        variant=context.get("variant", "unknown variant"),
        recipe=context.get("recipe", "unknown"),
        flow_json=flow_json,
    )
    try:
        # Sonnet: structured summarisation of a JSON flow — reading nodes and
        # wires and restating the pass/fail logic in prose. It was falling
        # through to the Opus default and cost 163.9s of a 21-minute run, the
        # single most expensive call in the extractor, for a task well inside
        # Sonnet's range. Falls back up the ladder if Sonnet is unavailable.
        return complete(
            prompt, schema=NODE_RED_SCHEMA, max_tokens=10000, model=llm.SONNET
        )
    except LLMRefusal:
        return {"markdown": "[node-red description refused by model]", "facts": []}


IO_RULES_PROMPT = """Below is the VERBATIM text of the "Pass/Fail & IO Logic" page of an Overview AI \
industrial vision camera ({variant}), captured for the recipe "{recipe}". The page was in BASIC \
mode: instead of a Node-RED flow, the pass/fail logic is defined by rule builders. The text \
mixes the rules' selected values with page chrome (navigation, capture preview metadata, \
buttons) — ignore the chrome entirely.

Domain knowledge for interpreting the rules (the page follows one of two layouts):
- OV80i layout — "Classification Rules" rows read like: <ROI scope> <condition> <class> — \
e.g. "All ROIs match zero" means every region's classification prediction must be the class \
"zero" for the check to pass. "Segmentation Rules" group per inspection type; rows read \
like: <defect class> <metric> <aggregation> <comparator> <threshold> — e.g. "Defect Pixel \
Count Lowest <= 50" bounds the defect mask's pixel count.
- OV20i layout ("Basic IO Block") — numbered sections: "Save images" (which frames persist \
to the Library); "Rules" (each rule checks an AI metric or PLC input against a value; \
"No rules — inspection always passes" means exactly that); "Overall result" (the rules \
combine by AND — "All rules pass" — or OR — "Any rule passes"); "Digital Outputs (DO)" \
(which of the two DO pins are driven, from a rule or the overall result, with latch/pulse \
and N.O./N.C. polarity).
- Multiple rules and rule groups combine into the overall verdict; describe the composition \
as shown. Rules reference the recipe's models / inspection types by name.
- The combined result is the camera's pass/fail verdict for each inspection cycle.
- Numeric thresholds are typed into input boxes; their values appear at the END of the text \
under "VISIBLE INPUT VALUES (in page order)" — associate them with the rules they belong to.

Write `markdown`: a plain-language analysis (~150-300 words) of what this recipe's pass/fail \
logic actually does — which model outputs are checked, the exact conditions and thresholds \
(values verbatim), and how they combine into the verdict. Describe the LOGIC only: never \
mention the page layout, buttons, or that this text came from a UI dump.

Also extract `facts`: discrete checkable values as {{subject, property, value}} entries with \
subject "io_logic" — one per rule (e.g. property "classification_rule" / "segmentation_rule" \
with the rule text verbatim), plus thresholds. Only what the text actually states.

PAGE TEXT:
{rules_text}"""


def describe_io_rules(rules_text: str, context: dict) -> dict:
    """Basic-Mode sibling of describe_node_red — same {"markdown", "facts"}
    contract, sourced from the rules page's verbatim innerText instead of
    the exported flow JSON, so everything downstream (the
    node_red_description.md file, io_logic facts, the deck's logic slide
    material) works unchanged."""
    prompt = IO_RULES_PROMPT.format(
        variant=context.get("variant", "unknown variant"),
        recipe=context.get("recipe", "unknown"),
        rules_text=str(rules_text)[:20000],
    )
    try:
        return complete(
            prompt, schema=NODE_RED_SCHEMA, max_tokens=10000, model=llm.SONNET
        )
    except LLMRefusal:
        return {"markdown": "[io rules description refused by model]", "facts": []}


def describe_screenshot(png_bytes: bytes, context: dict) -> dict:
    """Returns {"description": prose, "facts": [{subject, property, value}]}.

    context["models"] (the meta["models"] roster) steers fact subjects onto
    the recipe's authoritative model names; cli.py canonicalizes in code as
    the backstop. Callers without a roster (engineer photos) omit it."""
    item = context.get("item")
    item_line = f"- Specific item captured: {item}\n" if item else ""
    roster = context.get("models") or []
    models_block = ""
    if roster:
        names = "; ".join(
            f"{m.get('name', '')} ({m.get('type', '?')})" for m in roster
        )
        models_block = (
            f"- This recipe's models, with their AUTHORITATIVE names: {names}. "
            f"Record `model:`/`class:` fact subjects under these exact names, even "
            f"when the screen shows a truncated or shorthand form of one of them. "
            f"Screens can also show models, classes or recipes that are NOT in this "
            f"list (other recipes share these screens) — describe them in prose if "
            f"relevant, but never record facts for them.\n"
        )
    prompt = PROMPT.format(
        variant=context.get("variant", "unknown variant"),
        recipe=context.get("recipe", "unknown"),
        step=context.get("step", "unknown"),
        intent=" ".join((context.get("intent") or "").split()) or "n/a",
        item_line=item_line,
        models_block=models_block,
    )
    try:
        return complete(prompt, schema=DESCRIBE_SCHEMA, images=[png_bytes], max_tokens=4000)
    except LLMRefusal:
        return {"description": "[description refused by model]", "facts": []}
