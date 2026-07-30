> # Node-RED Flow Description — Recipe "#4 Camera 56959 Tail" (ov80i)

## 1. Overview

This is a **single-tab, five-node flow** that implements the pass/fail IO logic for one camera operating as a **slave** in a multi-camera cell. The tab is labelled **`Slave Camera 3 V5`** (note: the recipe is named "#4 Camera 56959 Tail", but every internal label in the flow refers to "Slave 3" / "Slave Camera 3").

In plain language, on every inspection the flow:

1. Receives the complete AI pipeline result from the camera (`overview-unified-pipeline-input`).
2. Runs **one JavaScript function** that evaluates three independent checks — **alignment found**, **segmentation blob size**, and **classification class names** — and ANDs them into a single boolean verdict.
3. Fans that verdict out to **three destinations simultaneously**:
   * the camera's own verdict/statistics node (`final-pass-fail`),
   * an **HTTP POST of a rich JSON result report to a master camera/controller** at `http://192.168.1.80:1880/api/slave3/results`,
   * a **single-byte EtherNet/IP write to the PLC** (`1` = pass, `0` = fail).

There are no MQTT nodes, no GPIO nodes, no delays/triggers/debounce, no HTTP-in endpoints, no dashboard buttons, no debug nodes and no disabled nodes. The entire decision logic lives in one function node; everything else is plumbing.

### Node inventory

| Node ID | Type | Name | Role |
|---|---|---|---|
| `1009d1369bf47cf7` | `overview-unified-pipeline-input` | Slave 3 Local Input | Trigger / source of inspection results |
| `1a94f97194f2f89f` | `function` (3 outputs) | Process & Prepare Results | All decision logic |
| `39bc8a7b38534023` | `final-pass-fail` | Final Pass/Fail | Camera verdict, stats, mapped hardware IO |
| `7bb66654259ee8d0` | `http request` | POST Results to Master | Network report to master |
| `55e20ea1e4e0cf81` | `ethernet-ip-user-data-write` | Write PLC Data | PLC output |
| `8d9d1960644ac168` | `global-config` | — | Declares module `overview-nodes` version `0.0.0`; empty `env` array |

All five functional nodes sit in one visual group (`116c1b2862d90ca2`, untitled, grey styling) — purely cosmetic.

### Wiring

```
overview-unified-pipeline-input ──> function "Process & Prepare Results"
                                        ├─ output 0 ──> final-pass-fail
                                        ├─ output 1 ──> http request (POST to master)
                                        └─ output 2 ──> ethernet-ip-user-data-write
```

The `http request` node has one output wired to **nothing** (the master's HTTP response is discarded). `final-pass-fail` and `ethernet-ip-user-data-write` are terminal nodes.

---

## 2. Logic walk-through

### 2.1 Input

`overview-unified-pipeline-input` ("Slave 3 Local Input") is the only trigger. It emits the unified pipeline result for each inspection on this camera; there is no external trigger (no `camera/trigger` MQTT subscription, no HTTP-in) in this flow.

### 2.2 The function node "Process & Prepare Results"

The function has **3 outputs** and no `timeout`, `initialize` or `finalize` code and no extra `libs`. Walking through the code:

#### Data extraction

```javascript
const rois = msg.payload.roi?.rois || [];
const blobs = msg.payload.segmentation?.blobs || [];
const classifications = msg.payload.classification?.predictions || [];
const imageUrl = msg.payload.image_url || '';
const inspTime = msg.payload.inspection_time || new Date().toISOString();
```

Four pipeline sections are consumed: `roi.rois` (only used later to resolve ROI *names* for failing classifications), `segmentation.blobs`, `classification.predictions`, plus `image_url` and `inspection_time` metadata. Missing sections default to empty arrays / empty string / current time.

#### Check 1 — Alignment

```javascript
const alignmentFound = msg.payload.alignment?.predictions?.[0]?.success === true;
```

Only the **first** alignment prediction is examined, and it must have `success === true` (strict). If the `alignment` section is absent, or `predictions` is empty, or `success` is anything other than boolean `true`, `alignmentFound` is `false` → the part **fails**. So a missing/failed alignment stage is treated as a fail (fail-safe behaviour).

#### Check 2 — Segmentation blob area

```javascript
const PIXEL_THRESHOLD = 250;

const failingBlobs = blobs.filter(b => (b.pixel_count || 0) > PIXEL_THRESHOLD);
const segPass = failingBlobs.length === 0;
```

The blob-area threshold is **hardcoded to 250 pixels** (this is the local equivalent of the conventional `min_mark_area_px` variable; the conventional name is not used here). Any blob whose `pixel_count` is **strictly greater than 250** is a defect. Blob *class* is irrelevant to the verdict — every segmentation blob over 250 px fails the part, regardless of `predicted_class`. Blobs of 250 px or fewer are ignored entirely, so small/noise blobs are tolerated. If there are no blobs at all, segmentation passes.

Failing blobs are then **grouped for reporting** (not for the verdict):

```javascript
const key = b.roi_name || b.roi_id || 'Global';
...
byRoi[key].classes.add(b.predicted_class || 'unknown');
byRoi[key].count++;
byRoi[key].maxPixels = Math.max(byRoi[key].maxPixels, b.pixel_count || 0);
```

producing `segFailDetails` entries of `{ roiName, classes[], count, maxPixels }` — one per ROI (or `'Global'` when the blob carries no ROI identity), with the de-duplicated set of predicted classes, the number of oversized blobs, and the largest blob's pixel count.

#### Check 3 — Classification

```javascript
const failingClassifications = classifications.filter(p => {
    const predictedClass = String(p.predicted_class || '').trim().toLowerCase();
    return !predictedClass.startsWith('pass');
});

const classPass = failingClassifications.length === 0;
```

This is the standard Overview convention: a classification prediction passes **only if its `predicted_class` string begins with `pass`** after trimming whitespace and lower-casing (so `pass_hole_presence`, `Pass`, `PASS_xyz` all pass; `fail_*`, `no_part`, empty/missing class all fail). No confidence/score threshold is applied — the class name alone decides. If there are no classification predictions at all, this check passes.

For each failing classification, the code resolves a human-readable ROI name by matching the prediction's `roi_id` against the `roi.rois` list, tolerating type mismatches:

```javascript
const matchedRoi = rois.find(r =>
    r.id === roiId ||
    r.id === Number(roiId) ||
    String(r.id) === String(roiId)
);
roiName = matchedRoi ? (matchedRoi.name || ('ROI ' + matchedRoi.id)) : roiName;
```

Fallbacks: the prediction's own `roi_name`, else the literal `'Global'`, else `'ROI <id>'`. Result: `classFailDetails` = `[{ roiName, className }]`.

#### Final verdict

```javascript
const pass = !(
    segPass === false ||
    classPass === false ||
    alignmentFound === false
);
```

A logical AND of the three checks: the part passes **only** when alignment was found **and** no blob exceeds 250 px **and** every classification class starts with `pass`. There is no weighting, no per-ROI override, no minimum-count logic and no "warn" state — the verdict is strictly binary.

#### Fail reasons and logging

Human-readable reasons are assembled in a fixed order — alignment first, then segmentation, then classification:

```javascript
if (alignmentFound === false) { failReasons.push('Alignment not found'); }

failReasons.push(
    'Seg fail in ' + sf.roiName + ': ' + sf.classes.join(', ') +
    ' (max ' + sf.maxPixels + 'px, threshold ' + PIXEL_THRESHOLD + 'px)'
);

failReasons.push('Class fail in ' + cf.roiName + ': ' + cf.className);
```

Then, **on every single inspection (pass or fail)**, the node emits a `node.warn(...)`:

```
'Slave 3 - Align: <bool>, Seg: PASS|FAIL, Class: PASS|FAIL, Overall: PASS|FAIL
 | Blobs>250px: <n>, ClassFails: <n>'
```

This is unconditional debug logging in production — it appears in the Node-RED sidebar/log for each part cycle.

#### The three outputs

```javascript
// Output 0: final-pass-fail
const passFailMsg = { payload: pass };
```
A bare boolean, nothing else — the fail reasons are **not** forwarded to `final-pass-fail`.

```javascript
// Output 1: HTTP POST to master
const MASTER_IP = '192.168.1.80';
const MASTER_PORT = 1880;
const httpMsg = {
    payload: { camera: 'Slave Camera 3', pass, alignmentFound, segPass, classPass,
               failReasons, segFailDetails, classFailDetails,
               blobCount: blobs.length, failingBlobCount: failingBlobs.length,
               classificationCount: classifications.length,
               failingClassificationCount: failingClassifications.length,
               imageUrl, timestamp: inspTime },
    url: 'http://' + MASTER_IP + ':' + MASTER_PORT + '/api/slave3/results',
    method: 'POST',
    headers: { 'Content-Type': 'application/json' }
};
```
The URL, method and headers are set **in the message**, not in the `http request` node.

```javascript
// Output 2: PLC write
const plcMsg = { payload: Buffer.from([pass ? 1 : 0]) };
```
A **one-byte buffer**: `0x01` for pass, `0x00` for fail.

```javascript
return [passFailMsg, httpMsg, plcMsg];
```

All three messages are emitted synchronously in the same tick — the camera verdict, the master report and the PLC write happen together with no sequencing, delay, debounce or pulse-reset logic.

### 2.3 Outputs

**`final-pass-fail` ("Final Pass/Fail")** — no configuration properties in the JSON beyond its name and position. It receives `msg.payload = true|false` and is responsible for the camera's own verdict display, pass/fail statistics and any hardware IO mapped in the camera configuration (that mapping is not in this flow).

**`http request` ("POST Results to Master")** — configured with:
* `method: "use"` → takes the verb from `msg.method` (`POST`)
* `url: ""` → takes the URL from `msg.url`
* `ret: "obj"` → parses the master's reply as a JSON object
* `senderr: false` → **HTTP/connection errors are swallowed**, not sent to the output
* `persist: false`, no TLS config, no proxy, no auth (`authType: ""`), no static headers
* Output wire: **empty** — the response is discarded (dead end).

Consequence: if the master at `192.168.1.80:1880` is unreachable, the slave carries on silently; there is no retry, catch node, or alarm.

**`ethernet-ip-user-data-write` ("Write PLC Data")** — an EtherNet/IP user-data write to the plant PLC. The node's JSON carries **only** `name`, position and an empty `wires` array; no target PLC address, tag name, byte offset or connection reference is present in this export, so the destination is taken from the node's defaults / the camera's global EtherNet/IP configuration rather than being visible here. The written value is the single byte from output 2.

**Timing elements** — none. There are no `delay`, `trigger`, `inject`, `link`, `switch` or `change` nodes anywhere in the flow.

---

## 3. Inspection context

This camera is one of several **slave** stations reporting to a **master** at `192.168.1.80:1880` (the master presumably aggregates the slaves and produces the cell-level verdict). This flow handles only *this* camera's own contribution.

### What must be true for a part to PASS

| Check | Source in pipeline result | Rule | Threshold |
|---|---|---|---|
| Alignment | `alignment.predictions[0].success` | must be strictly `true` | — |
| Segmentation | `segmentation.blobs[].pixel_count` | **no** blob may exceed the threshold | `PIXEL_THRESHOLD = 250` px (strictly `>`) |
| Classification | `classification.predictions[].predicted_class` | **every** prediction's class name must start with `pass` (trimmed, lower-cased) | — |

Any one failing check fails the part.

### On PASS
* `final-pass-fail` receives `true` → camera registers a pass and drives its mapped IO.
* PLC receives byte `1`.
* Master receives a JSON body with `pass: true`, `failReasons: []`, empty `segFailDetails` / `classFailDetails`, plus `blobCount`, `failingBlobCount` (0), `classificationCount`, `failingClassificationCount` (0), `imageUrl` and `timestamp`.

### On FAIL
* `final-pass-fail` receives `false`.
* PLC receives byte `0`.
* Master receives `pass: false` with the individual sub-results (`alignmentFound`, `segPass`, `classPass`), the ordered `failReasons` strings, and structured details:
  * segmentation: per-ROI `{ roiName, classes[], count, maxPixels }`
  * classification: `{ roiName, className }` per failing prediction
* A `node.warn` line is logged (also logged on pass).

### Model results / class names referenced

The flow references **generic** pipeline fields, not specific model or class names. The only class-name literal that participates in logic is the prefix **`pass`** (via `predictedClass.startsWith('pass')`). Literal strings used for reporting fallbacks are `'unknown'`, `'Global'`, and `'ROI ' + id`. No named recipe classes (e.g. no `pass_hole_presence`-style identifiers) and no ROI names/IDs are hardcoded — the logic is ROI-agnostic and applies uniformly to every ROI, blob and classification the recipe produces.

### Counts / thresholds

* Blob area threshold: **250 px**, strictly greater-than, applied to `pixel_count`.
* Failing-blob count tolerance: **0** (`segPass = failingBlobs.length === 0`).
* Failing-classification tolerance: **0** (`classPass = failingClassifications.length === 0`).
* Alignment predictions examined: **only index `[0]`**.

---

## 4. Notable details

* **Naming mismatch** — the recipe is "#4 Camera 56959 Tail" but the tab is `Slave Camera 3 V5`, the input node is "Slave 3 Local Input", the reported camera name is `'Slave Camera 3'`, the log prefix is `'Slave 3 - ...'` and the master endpoint path is `/api/slave3/results`. Nothing in the flow mentions "Tail", "#4" or serial 56959.
* **Hardcoded, environment-specific values**: master IP `192.168.1.80`, port `1880`, path `/api/slave3/results`, camera label `Slave Camera 3`, pixel threshold `250`. All are literals inside the function body, so changing them means editing code (no `env` variables — the `global-config` node has an empty `env` array).
* **The `edas_roi_filter` convention is not used**; there is no ROI whitelist/blacklist. Every ROI is treated equally.
* **No MQTT anywhere** — no `camera/trigger` subscription, no `overview/inspection/result` or `overview/inspection/report` publication. Result distribution is done exclusively via the HTTP POST to the master.
* **No GPIO nodes / no pin numbers** in this flow. Physical outputs, if any, are whatever `final-pass-fail` is configured to drive plus the EtherNet/IP byte.
* **EtherNet/IP node carries no visible target configuration** (no tag, offset, PLC IP or connection config node in this export). The single-byte payload implies a 1-byte user-data area where `1` = PASS and `0` = FAIL, but the exact tag/offset cannot be determined from this JSON.
* **HTTP errors are silently ignored** (`senderr: false`, output wire empty, no `catch` node). A down or wrongly-addressed master produces no alarm and no retry; the PLC output still fires.
* **Unconditional `node.warn` on every cycle** — useful for commissioning, but it is verbose logging left enabled in a production flow.
* **`final-pass-fail` gets only a boolean** — the diagnostic detail (`failReasons`, `segFailDetails`, `classFailDetails`) exists only in the HTTP payload. If the master is not collecting it, that diagnostic data is lost.
* **Fail-open/fail-closed nuances**:
  * Missing `alignment` section → `alignmentFound === false` → **fail** (fail-safe).
  * Missing `segmentation` or `classification` sections → empty arrays → those checks **pass** (fail-permissive). A pipeline that silently stops producing classification predictions would therefore report PASS as long as alignment succeeds.
  * A blob or prediction with a missing field defaults to `pixel_count = 0` (ignored) and `predicted_class = ''` (fails the `startsWith('pass')` test).
* **Unused extraction**: `rois` is fetched but used only for resolving names of *failing* classifications; `imageUrl` and `inspTime` are only forwarded to the master.
* **No disabled nodes**, no orphaned/unwired nodes other than the `http request` node's unused output, no dead branches, no `status`/`catch`/`complete` nodes, and no timing/debounce elements.
* Declared module dependency: `overview-nodes` at version `0.0.0`.