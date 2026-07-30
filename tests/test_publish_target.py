"""Where a publish actually lands.

Finished decks go FLAT into the team-wide shared drive so the whole team can
find every report in one place. Raw assets are working material and go to the
engineer's OWN Drive library, inside a dated folder — a shared space the team
reads should not accumulate 28-file asset dumps.

The constraint that makes this fragile is invisible from the code: our
`drive.file` scope can WRITE to the shared drive but cannot READ it. Verified
against the real drive:

    drives.get(driveId)              -> 403
    files.get(fileId=driveId)        -> 404
    files.create(parents=[driveId])  -> OK

So the flat path must never call folder_exists() or find_folder(), which
always fail for that target. `library_folder()`'s find-or-create-by-name is
exactly the pattern that cannot be used. A refactor that reintroduces a lookup
would pass every other test and fail only against the real drive — hence the
spy below.

Run: uv run python tests/test_publish_target.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from publish import gdrive  # noqa: E402


class FakeClient:
    """Records what a publish did, and screams if it reads the shared drive."""

    def __init__(self):
        self.uploads: list[dict] = []
        self.folders: list[tuple[str, str | None]] = []
        self.reads: list[str] = []

    def upload(self, path, parent, convert_to=None, name=None):
        self.uploads.append(
            {"file": Path(path).name, "parent": parent,
             "name": name, "convert_to": convert_to}
        )
        return {"id": f"id-{len(self.uploads)}", "name": name or Path(path).name,
                "link": None}

    def create_folder(self, name, parent=None):
        self.folders.append((name, parent))
        return f"folder-{len(self.folders)}"

    # These two cannot work against a shared drive.
    def folder_exists(self, fid):
        self.reads.append(f"folder_exists({fid})")
        return True

    def find_folder(self, name):
        self.reads.append(f"find_folder({name})")
        return None


def main() -> int:
    import tempfile

    failures = []
    TEAM = gdrive.TEAM_DRIVE_ID

    if not TEAM:
        print("FAIL: TEAM_DRIVE_ID is empty; the default destination is unset")
        return 1

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        deck = tmp / "deck.pptx"
        deck.write_bytes(b"not really a deck")
        run = tmp / "run"
        (run / "deliverables").mkdir(parents=True)
        (run / "deliverables" / "shot.png").write_bytes(b"png")
        (run / "data").mkdir()
        (run / "data" / "manifest.json").write_text('{"recipe_input": "Widget A"}')

        # ---- default: deck alone, flat in the team drive ----
        c = FakeClient()
        rep = gdrive.publish(None, deck, client=c, log=lambda *a: None)
        if rep.get("target") != "team-drive":
            failures.append(f"default target is {rep.get('target')!r}, want team-drive")
        if c.folders:
            failures.append(f"flat publish created folders: {c.folders}")
        if c.reads:
            failures.append(f"flat publish READ the shared drive: {c.reads}")
        # The report's shape must not change with the destination.
        for key in ("folder_id", "folder_link", "slides_link", "uploaded", "failed"):
            if key not in rep:
                failures.append(f"flat publish report is missing {key!r}")
        if rep.get("folder_id") != TEAM:
            failures.append("flat publish folder_id should be the shared drive")
        if len(c.uploads) != 1:
            failures.append(f"expected 1 upload, got {len(c.uploads)}")
        else:
            up = c.uploads[0]
            if up["parent"] != TEAM:
                failures.append(f"deck parent is {up['parent']!r}, want the team drive")
            if up["convert_to"] != gdrive.SLIDES_MIME:
                failures.append("deck was not converted to Google Slides")
            if not up["name"].startswith("OV Test Report — "):
                failures.append(f"flat deck name lost the convention: {up['name']!r}")

        # ---- a run with assets goes to the personal library, foldered ----
        c = FakeClient()
        rep = gdrive.publish(run, deck, client=c,
                             include=gdrive.DEFAULT_INCLUDE, log=lambda *a: None)
        if rep.get("target") != "personal-drive":
            failures.append(f"asset publish target is {rep.get('target')!r}")
        if not c.folders:
            failures.append("asset publish created no dated folder")
        if not any(u["parent"] == TEAM for u in c.uploads) is False:
            pass
        if any(u["parent"] == TEAM for u in c.uploads):
            failures.append("assets were uploaded straight into the shared drive")

        # ---- explicit opt-out sends the deck to the personal library ----
        c = FakeClient()
        rep = gdrive.publish(None, deck, client=c, team_drive="", log=lambda *a: None)
        if rep.get("target") != "personal-drive":
            failures.append(f"team_drive='' gave target {rep.get('target')!r}")
        if any(u["parent"] == TEAM for u in c.uploads):
            failures.append("team_drive='' still uploaded to the shared drive")

        # ---- an explicit id is honoured (SG_TEAM_DRIVE_ID's mechanism) ----
        c = FakeClient()
        gdrive.publish(None, deck, client=c, team_drive="OTHER", log=lambda *a: None)
        if not c.uploads or c.uploads[0]["parent"] != "OTHER":
            failures.append("an explicit team_drive id was not used as the parent")

        # ---- the dry run must describe the same destination ----
        plan = gdrive.plan_publish(None, deck)
        if plan.get("target") != "team-drive" or not plan.get("flat"):
            failures.append(f"dry run disagrees with publish: {plan.get('target')}")
        if not any(TEAM in line for line in plan["tree"]):
            failures.append("dry run does not name the shared drive")
        plan_assets = gdrive.plan_publish(run, deck, include=gdrive.DEFAULT_INCLUDE)
        if plan_assets.get("target") != "personal-drive":
            failures.append("dry run sends assets to the wrong drive")

        # ---- publishing twice never overwrites the first upload ----
        # Drive tolerates duplicate names, and there is no update path here at
        # all: a re-publish must always create a new file. Silently clobbering
        # a deck a colleague has already edited is the worst thing this could
        # do, and in a SHARED drive it would be someone else's work.
        c = FakeClient()
        gdrive.publish(None, deck, client=c, log=lambda *a: None)
        gdrive.publish(None, deck, client=c, log=lambda *a: None)
        if len(c.uploads) != 2:
            failures.append("a second publish did not create a second file")
        if any("update" in r for r in c.reads):
            failures.append("publish attempted an in-place update")

        # ---- nothing to publish is still an error ----
        try:
            gdrive.publish(None, None, client=FakeClient(), log=lambda *a: None)
            failures.append("publishing nothing should raise")
        except ValueError:
            pass

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("ALL PUBLISH-TARGET CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
