# Overview.ai brand rules for decks

Authoritative source: `assets/brand/` — the Brand Guidelines pages shipped as
SVG/PDF, plus the logo lockups. Everything below was read out of those files,
not invented. Machine-readable form: `assets/tokens.json`.

**If this document and the brand pack ever disagree, the brand pack wins.**
Re-sample it rather than trusting these numbers from memory.

---

## 1. Colour

The guidelines define exactly four core colours, each with a four-step tint
ramp (Brand Guidelines, page 06 "Colors").

| Role | Name | Hex | RGB |
|---|---|---|---|
| Primary | Purple | `#532EE3` | 83 46 227 |
| Dark ground | Navy Purple | `#180E42` | 24 14 66 |
| Highlight | Yellow | `#FFC524` | 255 197 36 |
| Neutral | Grey | `#F2F2F2` | 242 242 242 |

Tint ramps, darkest → lightest:

```
purple  #532EE3  #735CFF  #A2A0FF  #C4C2FF  #F1F1FF
navy    #180E42  #251566  #332373  #433480  #564793
yellow  #FFC524  #FFD359  #FFDD80  #FEEAB2  #FFF7E2
grey    #BCC2CC  #CCD1D9  #E1E4E6  #F2F2F2  #FAFAFA
```

`#735CFF`, `#A2A0FF` and `#532EE3` are also the three fills of the logomark
itself, which is why the purple ramp is the one that carries the brand.

### Hard rules

- **No colour outside those ramps**, plus pure white `#FFFFFF` and pure black
  `#000000`. `brandcheck.py` fails the deck on anything else.
- **Do not eyedrop colours from an existing Overview deck.** Several decks in
  circulation — including the STADLER connector-inspection deck used as the
  layout reference — use a near-miss palette (`#201553`, `#2C1B69`, `#7B5CFF`,
  `#EFEBFA`). Those are *wrong*. Borrow that deck's layout, never its hexes.
- **Green and red do not exist in this brand.** If a status colour is
  unavoidable (pass/fail, a callout rule), use Yellow `#FFC524` for emphasis
  and let the words carry the verdict. The one place red/green may appear is
  *inside a product screenshot*, which is evidence and must never be recoloured.
- Body copy on light backgrounds: `#180E42` for headings, `#332373` for body,
  `#564793` for footnotes. Never grey-on-white for body text.
- Text on the navy ground: `#FFFFFF` for primary, `#A2A0FF` for supporting.

### Semantic assignments used by `ovdeck.py`

| Token | Hex | Where |
|---|---|---|
| `page_bg_tinted` | `#F1F1FF` | content-slide background |
| `header_bar` / `dark_bg` | `#180E42` | title bar, section dividers, title/closing |
| `dark_bg_alt` | `#251566` | panels on the navy ground |
| `accent` | `#532EE3` | card titles, rules, emphasis |
| `accent_light` | `#735CFF` | section numbers, connectors, title halo |
| `accent_on_dark` | `#A2A0FF` | supporting text on navy |
| `chip_bg` / `chip_border` | `#F1F1FF` / `#C4C2FF` | spec chips |
| `hairline` | `#E1E4E6` | image borders |
| `highlight` / `highlight_soft` | `#FFC524` / `#FFF7E2` | badges |

---

## 2. Logo

Files in `assets/brand/`:

| File | What it is |
|---|---|
| `logo-H-colored.png` | horizontal lockup, **black** wordmark — light backgrounds only |
| `logo-V-colored.png` | vertical lockup, **black** wordmark — light backgrounds only |
| `logo .png` / `logo .svg` | the logomark alone |
| `Logo-2.svg` | guidelines page 01, "Horizontal Logo" (logomark + logotype) |
| `Logo-5.svg` | guidelines page 02, "Safe Zone" |
| `Logo-6.svg` | guidelines page 04, "Vertical Logo" |
| `Logo-4.svg` | guidelines page 06, "Colors" |

`assets/brand/derived/` holds variants produced by
`scripts/make_logo_variants.py` — run it once after install:

| File | What it is |
|---|---|
| `logo-H-white.png` | horizontal lockup, white wordmark, purple mark — **for the navy ground** |
| `logo-V-white.png` | vertical equivalent |
| `logo-H-dark.png` / `logo-V-dark.png` | trimmed originals for light backgrounds |
| `logomark.png` | trimmed mark alone |

The white variants are produced by recolouring the black wordmark pixels only;
the artwork is never redrawn or re-typeset.

### Hard rules

- **The wordmark must never sit as black on navy** — it disappears. Use the
  `-white` variants on any dark ground.
- **Clear space**: at least half the logomark's height on every side
  (guidelines page 02). `ovdeck.py` reserves this automatically.
- **Minimum width** 1.1 in on a 13.333 in slide. Smaller and the mark's notch
  fills in.
- **Never** recolour, outline, stretch, rotate, add effects to, or place the
  logo on a busy photo.
- Logo is **mandatory on the opening and closing slides**. Both `ovdeck.py` and
  `brandcheck.py` fail a deck that is missing either.
- Content slides carry the brand through the navy header bar instead — do not
  add a logo to every slide.

---

## 3. Typography

**The brand pack ships no typography page.** Pages 01, 02, 04 and 06 are
present; 03 and 05 are not in the folder, and the PDFs have outlined text, so
no typeface can be read out of them.

The only house-font evidence is in the example decks, and they disagree:

- **Hot Bar Soldering** is set in **Proxima Nova** (Bold / Semibold / Regular).
  This is the strongest signal of an intended brand typeface.
- **STADLER** is set in **Calibri**.

Default: **Calibri**, because

- it is present in both PowerPoint and Google Slides, so a deck uploaded to
  Drive keeps its metrics;
- LibreOffice substitutes **Carlito**, which is metric-compatible, so local
  renders match what the customer sees;
- Proxima Nova is licensed, is not bundled with this skill, and a font that is
  not installed on the presenting machine is silently replaced by PowerPoint
  with something arbitrary — worse than shipping Calibri deliberately.

To request Proxima Nova: `Deck(out, font="Proxima Nova")`. The engine checks
whether it is actually installed; if not it emits a `font-fallback` warning and
uses Calibri rather than substituting silently. Use it when you know the deck
will be presented from a machine that has the licence.

`ovdeck.py` measures every string against the real Carlito/Calibri glyph
advances — that is how text-overflow detection works. Do not switch fonts
without updating `assets/tokens.json`; the measurement path follows the token.

Type scale (pt), from `tokens.json`:

| Role | Size |
|---|---|
| Title-slide headline | 40 |
| Section number | 60 |
| Slide title (header bar) | 25, auto-shrinks to fit |
| Card title | 16 |
| Lead paragraph | 14 |
| Body | 12.5 |
| Bullet | 12 |
| Caption / image caption | 11.5 |
| Spec chip | 10.5 |
| Footnote | 10 |

Never set body copy below 10 pt. If text does not fit, cut words or split the
slide — do not shrink type.

---

## 4. Geometry

- Slide: **13.333 × 7.5 in** (16:9). Never 10 × 5.63.
- Outer margin: **0.62 in** on all four sides.
- Header bar height: **1.11 in**.
- Gutter between cards/columns: **0.20 in**.
- Body content ends at **y = 7.05 in**; only footnotes sit below.

---

## 5. Tone

The brand voice in these decks is plainly factual, not promotional. Concrete
numbers beat adjectives: "83 training images, 100% training accuracy" rather
than "excellent accuracy". See `content-rules.md`.
