"""Deck assembly from per-slide skeleton .pptx files.

Strategy: every skeleton is a standalone one-slide presentation. Each slide is
FILLED in its own file with ordinary python-pptx operations (token replacement
in text frames, image swaps), and only then transplanted into the output deck
via OPC part surgery — the surgery layer never edits content, it only copies
finished parts. The first slide's skeleton serves as the base presentation, so
the deck inherits its masters/theme; appended slides bring their own layouts
(remapped onto the base master) and media with them.
"""

from __future__ import annotations

import re
from pathlib import Path

from pptx import Presentation
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.opc.package import Part
from pptx.oxml.ns import qn
from pptx.util import Emu

TOKEN_RE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")

MSO_PICTURE = 13
MSO_GROUP = 6

# Content pictures vs decorations (sidebar strips, logos): a content slot is
# reasonably wide AND reasonably large.
MIN_PIC_WIDTH_IN = 1.0
MIN_PIC_AREA_SQIN = 2.0


def iter_shapes(container):
    for shape in container.shapes:
        if shape.shape_type == MSO_GROUP:
            yield from _iter_group(shape)
        else:
            yield shape


def _iter_group(group):
    for shape in group.shapes:
        if shape.shape_type == MSO_GROUP:
            yield from _iter_group(shape)
        else:
            yield shape


def find_tokens(slide) -> list[str]:
    tokens = []
    for shape in iter_shapes(slide):
        if shape.has_text_frame:
            tokens += TOKEN_RE.findall(shape.text_frame.text)
    return tokens


def fill_tokens(slide, values: dict) -> list[str]:
    """Replace {{ token }} occurrences; formatting of each paragraph's first
    run is preserved (Google exports often split tokens across runs, so runs
    are consolidated per paragraph before substitution)."""
    filled = []
    for shape in iter_shapes(slide):
        if not shape.has_text_frame:
            continue
        for para in shape.text_frame.paragraphs:
            text = "".join(run.text for run in para.runs)
            hits = TOKEN_RE.findall(text)
            if not hits:
                continue
            new_text = TOKEN_RE.sub(
                lambda m: str(values.get(m.group(1), m.group(0))), text
            )
            if para.runs:
                para.runs[0].text = new_text
                for run in para.runs[1:]:
                    run.text = ""
            filled += [h for h in hits if h in values]
    return filled


def content_pictures(slide) -> list:
    pics = []
    for shape in iter_shapes(slide):
        if shape.shape_type != MSO_PICTURE:
            continue
        w, h = Emu(shape.width).inches, Emu(shape.height).inches
        if w >= MIN_PIC_WIDTH_IN and w * h >= MIN_PIC_AREA_SQIN:
            pics.append(shape)
    pics.sort(key=lambda s: s.width * s.height, reverse=True)
    return pics


PLACEHOLDER_TEXT_RE = re.compile(r"insert screenshot", re.IGNORECASE)


def image_slots(slide) -> list:
    """Fillable image slots: content PICTURE shapes (largest first), then
    'Insert screenshot here' auto-shape placeholders (left-to-right)."""
    slots = content_pictures(slide)
    placeholders = [
        s
        for s in iter_shapes(slide)
        if s.shape_type != MSO_PICTURE
        and s.has_text_frame
        and PLACEHOLDER_TEXT_RE.search(s.text_frame.text)
    ]
    placeholders.sort(key=lambda s: (s.left, s.top))
    return slots + placeholders


def _fill_placeholder(slide, shape, image_path: Path) -> None:
    """Replace an 'Insert screenshot here' auto-shape with the image,
    aspect-fit within the shape's bounds."""
    from PIL import Image

    with Image.open(image_path) as im:
        iw, ih = im.size
    scale = min(shape.width / iw, shape.height / ih)
    w, h = int(iw * scale), int(ih * scale)
    left = shape.left + (shape.width - w) // 2
    top = shape.top + (shape.height - h) // 2
    slide.shapes.add_picture(str(image_path), left, top, w, h)
    shape._element.getparent().remove(shape._element)


def fill_images(slide, image_paths: list) -> None:
    slots = image_slots(slide)
    if len(image_paths) > len(slots):
        raise RuntimeError(
            f"{len(image_paths)} images provided but only {len(slots)} slots on slide"
        )
    for path, slot in zip(image_paths, slots):
        if slot.shape_type == MSO_PICTURE:
            replace_picture(slide, slot, Path(path))
        else:
            _fill_placeholder(slide, slot, Path(path))
    # An unfilled placeholder must not ship its "Insert screenshot" note.
    for slot in slots[len(image_paths):]:
        if slot.shape_type != MSO_PICTURE and slot.has_text_frame:
            for para in slot.text_frame.paragraphs:
                for run in para.runs:
                    run.text = ""


def replace_picture(slide, pic, image_path: Path) -> None:
    """Swap a picture's image, aspect-fitting it within the original bounds."""
    from PIL import Image

    with Image.open(image_path) as im:
        iw, ih = im.size
    left, top, width, height = pic.left, pic.top, pic.width, pic.height
    scale = min(width / iw, height / ih)
    new_w, new_h = int(iw * scale), int(ih * scale)
    pic.left = left + (width - new_w) // 2
    pic.top = top + (height - new_h) // 2
    pic.width, pic.height = new_w, new_h

    image_part, rId = pic.part.get_or_add_image_part(str(image_path))
    blip = pic._element.blipFill.find(qn("a:blip"))
    blip.set(qn("r:embed"), rId)


# ---------------------------------------------------------------------------
# Freeform slides: no dedicated skeleton. A donor skeleton supplies the
# theme/background/decorations; its token text and image slots are stripped
# and title/body/images are laid out programmatically.
# ---------------------------------------------------------------------------


def fill_freeform(pres: Presentation, job: dict) -> None:
    from pptx.util import Inches, Pt

    slide = pres.slides[0]
    doomed = set()
    for shape in iter_shapes(slide):
        if shape.has_text_frame and TOKEN_RE.search(shape.text_frame.text):
            doomed.add(shape._element)
    for slot in image_slots(slide):
        doomed.add(slot._element)
    for el in doomed:
        el.getparent().remove(el)

    tokens = job.get("tokens") or {}
    title = tokens.get("_ff_title", "")
    body = tokens.get("_ff_body", "")
    images = job.get("images") or []
    sw, sh = pres.slide_width, pres.slide_height
    margin = Inches(0.55)

    if title:
        tb = slide.shapes.add_textbox(margin, Inches(0.35), sw - 2 * margin, Inches(0.75))
        tb.text_frame.word_wrap = True
        run = tb.text_frame.paragraphs[0].add_run()
        run.text = title
        run.font.size = Pt(22)
        run.font.bold = True
    content_top = Inches(1.25)
    content_h = sh - content_top - Inches(0.35)
    if body and images:
        text_w = int((sw - 2 * margin) * 0.4)
        _freeform_body(slide, body, margin, content_top, text_w, content_h)
        img_left = margin + text_w + Inches(0.25)
        _freeform_images(slide, images, img_left, content_top, sw - margin - img_left, content_h)
    elif images:
        _freeform_images(slide, images, margin, content_top, sw - 2 * margin, content_h)
    elif body:
        _freeform_body(slide, body, margin, content_top, sw - 2 * margin, content_h)


def _freeform_body(slide, text: str, left, top, width, height) -> None:
    from pptx.util import Pt

    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    for i, line in enumerate(text.split("\n")):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = para.add_run()
        run.text = line
        run.font.size = Pt(13)


def _freeform_images(slide, paths: list, left, top, width, height) -> None:
    """Stack images vertically, each aspect-fit within its cell."""
    from pptx.util import Inches
    from PIL import Image

    gap = Inches(0.15)
    cell_h = int((height - gap * (len(paths) - 1)) / len(paths))
    for i, path in enumerate(paths):
        with Image.open(path) as im:
            iw, ih = im.size
        cell_top = top + i * (cell_h + gap)
        scale = min(width / iw, cell_h / ih)
        w, h = int(iw * scale), int(ih * scale)
        slide.shapes.add_picture(
            str(path), left + (width - w) // 2, cell_top + (cell_h - h) // 2, w, h
        )


# ---------------------------------------------------------------------------
# OPC surgery: transplant a finished slide into the base presentation.
# ---------------------------------------------------------------------------


def _add_rel(part, reltype, target, rId, external=False):
    """Add a relationship preserving the ORIGINAL rId — the transplanted
    slide XML references relationships by rId (r:embed etc.), so auto-assigned
    ids would break it. Mirrors _Relationships.load_from_xml's construction."""
    from pptx.opc.oxml import RTM
    from pptx.opc.package import _Relationship

    rels = part.rels
    rels._rels[rId] = _Relationship(
        rels._base_uri,
        rId,
        reltype,
        target_mode=RTM.EXTERNAL if external else RTM.INTERNAL,
        target=target,
    )


def _partname_template(src_partname: str) -> str:
    if "/slideLayouts/" in src_partname:
        return "/ppt/slideLayouts/slideLayout%d.xml"
    if "/slides/" in src_partname:
        return "/ppt/slides/slide%d.xml"
    if "/media/" in src_partname:
        ext = src_partname.rsplit(".", 1)[-1]
        return f"/ppt/media/image%d.{ext}"
    if "/notesSlides/" in src_partname:
        return "/ppt/notesSlides/notesSlide%d.xml"
    stem, ext = src_partname.rsplit(".", 1)
    stem = re.sub(r"\d+$", "", stem)
    return f"{stem}%d.{ext}"


def _register_master(base: Presentation, master_part) -> None:
    """Make an imported slide master part official: relate it to the
    presentation part and add a sldMasterId entry."""
    pres_part = base.part
    rId = pres_part.relate_to(master_part, RT.SLIDE_MASTER)
    pres_el = pres_part._element
    lst = pres_el.find(qn("p:sldMasterIdLst"))
    existing = [int(e.get("id")) for e in lst.findall(qn("p:sldMasterId"))]
    el = lst.makeelement(qn("p:sldMasterId"), {"id": str(max(existing) + 1)})
    el.set(qn("r:id"), rId)
    lst.append(el)


def _alloc_partname(tmpl: str, used: set):
    from pptx.opc.packuri import PackURI

    n = 1
    while tmpl % n in used:
        n += 1
    used.add(tmpl % n)
    return PackURI(tmpl % n)


def _import_part(base: Presentation, src_part, mapping: dict, ctx: dict):
    key = str(src_part.partname)
    if key in mapping:
        return mapping[key]
    ct = src_part.content_type
    blob = src_part.blob
    # Identical parts (shared layouts, the decorative sidebar image every
    # skeleton carries) are stored once per deck.
    content_key = (ct, hash(blob))
    if content_key in ctx["by_hash"]:
        mapping[key] = ctx["by_hash"][content_key]
        return mapping[key]
    pkg = base.part.package
    partname = _alloc_partname(_partname_template(str(src_part.partname)), ctx["used"])
    new_part = Part(partname, ct, pkg, blob)
    mapping[key] = new_part
    ctx["by_hash"][content_key] = new_part
    for rId, rel in sorted(src_part.rels.items()):
        if rel.is_external:
            _add_rel(new_part, rel.reltype, rel.target_ref, rId, external=True)
            continue
        if rel.target_part.content_type.endswith("notesSlide+xml"):
            continue  # notes are irrelevant to the deck; slide XML holds no rId to them
        target = _import_part(base, rel.target_part, mapping, ctx)
        _add_rel(new_part, rel.reltype, target, rId)
    if ct.endswith("slideMaster+xml"):
        # Slides keep their own master (backgrounds/themes differ per
        # skeleton); identical masters dedup via by_hash above, so the shared
        # content master is imported and registered exactly once.
        _register_master(base, new_part)
    return new_part


def append_slide(base: Presentation, src_pres: Presentation, ctx: dict) -> None:
    """Append src's (single, already-filled) slide to the base deck."""
    src_slide_part = src_pres.slides[0].part
    mapping: dict = {}
    new_part = _import_part(base, src_slide_part, mapping, ctx)
    rId = base.part.relate_to(new_part, RT.SLIDE)
    base.slides._sldIdLst.add_sldId(rId)


def build_deck(slide_jobs: list[dict], out_path: Path) -> None:
    """slide_jobs: [{skeleton: Path, tokens: {name: value}, image: Path|None}].
    The first job's skeleton becomes the base presentation."""
    if not slide_jobs:
        raise ValueError("no slides to build")

    def fill(pres, job):
        if job.get("freeform"):
            fill_freeform(pres, job)
            return
        slide = pres.slides[0]
        if job.get("tokens"):
            fill_tokens(slide, job["tokens"])
        images = job.get("images") or ([job["image"]] if job.get("image") else [])
        if images:
            fill_images(slide, images)

    base = Presentation(str(slide_jobs[0]["skeleton"]))
    fill(base, slide_jobs[0])
    ctx = {
        "used": {str(p.partname) for p in base.part.package.iter_parts()},
        "by_hash": {},
    }
    for job in slide_jobs[1:]:
        src = Presentation(str(job["skeleton"]))
        fill(src, job)
        append_slide(base, src, ctx)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    base.save(str(out_path))
