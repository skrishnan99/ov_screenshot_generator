"""Side test: judge the quality of a run's text artifacts by generating a
customer-facing test-report abstract from them alone (no images).

Usage: uv run python report_abstract.py [runs/<run-dir>]   (default: latest run)

Feeds descriptions.json + node_red_description.md to the model and asks for the
two abstract sections an Overview sales engineer would put in front of the
customer: the inspection problem, and the solution & how it worked out. Saves
report_abstract.md into the run dir.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import anthropic

MODEL = "claude-opus-5"

PROMPT = """You are a sales engineer at Overview writing the abstract of a test report for the \
Overview AI Vision Inspection System. The test was conducted on a customer's plant; the report \
is submitted to that customer. Your audience is quality engineers at the plant who need to \
quickly understand what was inspected and how it went — write as if this will appear at the \
top of a one-page test report PDF.

Below are (a) detailed descriptions of screenshots from the camera's configuration UI for the \
tested inspection recipe, and (b) a summary of the camera's IO/pass-fail logic. Write the \
abstract from this material ONLY, as two Markdown sections:

## Inspection Problem
3-4 crisp sentences. State what part or product is being inspected, what specific defects or \
conditions are checked, and the production context (e.g. the camera's role in the line or \
cell). Example tone: "Inspects automotive door panels for surface defects including scratches \
and dents across 20 inspection zones."

## Solution & Results
3-4 crisp sentences. Describe how the AI vision system solves the problem at a high level — \
the key stages and what each decides (e.g. inspection regions, what each classifier or \
segmentation model checks, with class names and training example counts where they strengthen \
the story) — then how it worked out: training outcomes and the crux of the pass/fail rule and \
how results reach the plant's systems, in plain terms. Example tone: "The system inspects 6 \
hole regions with a classifier trained on 74 Pass and 9 Fail examples... Parts failing any \
check are reported to the line PLC via EtherNet/IP for automated reject."

Rules:
- Crisp, clear copy for quality engineers — no technical internals: no camera configuration \
minutiae (exposure/gain/gamma/white balance), no IP addresses or ports, no node or variable \
names, no UI/JSON field names, no UUIDs.
- Numbers are welcome only when they tell the story: class names, training example counts, \
accuracies, region counts. Skip housekeeping numbers (capture-store totals, timestamps, IDs).
- Use only facts present in the material — never invent metrics, part names, or outcomes.
- Each section must be 3-4 sentences. Nothing outside the two sections.

=== SCREENSHOT DESCRIPTIONS (filename: description) ===
{descriptions}

=== NODE-RED IO LOGIC SUMMARY ===
{node_red}"""


def main() -> int:
    if len(sys.argv) > 1:
        run_dir = Path(sys.argv[1])
    else:
        run_dir = sorted((Path(__file__).parent / "runs").iterdir())[-1]
    descriptions = json.loads((run_dir / "descriptions.json").read_text())
    node_red = (run_dir / "node_red_description.md").read_text()

    desc_text = "\n\n".join(f"### {name}\n{text}" for name, text in descriptions.items())
    client = anthropic.Anthropic()
    with client.messages.stream(
        model=MODEL,
        max_tokens=2000,
        messages=[
            {
                "role": "user",
                "content": PROMPT.format(descriptions=desc_text, node_red=node_red),
            }
        ],
    ) as stream:
        response = stream.get_final_message()
    if response.stop_reason == "refusal":
        print("model refused", file=sys.stderr)
        return 1
    abstract = "".join(b.text for b in response.content if b.type == "text").strip()

    out = run_dir / "report_abstract.md"
    out.write_text(f"# Test Report Abstract\n\n{abstract}\n")
    print(f"saved -> {out}\n")
    print(abstract)
    return 0


if __name__ == "__main__":
    sys.exit(main())
