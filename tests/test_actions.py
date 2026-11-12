import json
import os

import pytest

from spelunk import actions as actions_mod
from spelunk.actions import Actions
from spelunk.knowledge import KnowledgeBase


def make(home):
    return Actions(KnowledgeBase.load(home=home), home=home)


def test_plan_blocks_never_and_hard_denied(fake_home):
    a = make(fake_home)
    plan = a.plan("delete", [os.path.join(fake_home, "Library/Keychains"), fake_home + "/Library",
                             os.path.join(fake_home, "Downloads/installer.dmg")])
    blocked = {i["path"]: i["blocked"] for i in plan["items"]}
    assert blocked[os.path.join(fake_home, "Library/Keychains")]
    assert blocked[fake_home + "/Library"]
    assert not blocked[os.path.join(fake_home, "Downloads/installer.dmg")]
    assert plan["count"] == 1


def test_plan_dedupes_children_of_selected_parent(fake_home):
    a = make(fake_home)
    parent = os.path.join(fake_home, "Library/Caches/Google")
    plan = a.plan("trash", [parent, parent + "/cache.bin"])
    assert [i["path"] for i in plan["items"]] == [parent]


def test_review_items_need_ack(fake_home):
    a = make(fake_home)
    plan = a.plan("delete", [os.path.join(fake_home, "Desktop/Projects/app/node_modules")])
    assert plan["needs_ack"]
    with pytest.raises(PermissionError):
        a.execute(plan["id"], acknowledged=False, background=False)
    job = a.execute(plan["id"], acknowledged=True, background=False)
    assert job.state == "done"
    assert not os.path.exists(os.path.join(fake_home, "Desktop/Projects/app/node_modules"))


def test_plan_ids_are_single_use(fake_home):
    a = make(fake_home)
    plan = a.plan("trash", [os.path.join(fake_home, "Downloads/installer.dmg")])
    a.execute(plan["id"], background=False)
    with pytest.raises(KeyError):
        a.execute(plan["id"], background=False)


def test_trash_moves_into_bin(fake_home):
    a = make(fake_home)
    src = os.path.join(fake_home, "Library/Caches/Google")
    job = a.execute(a.plan("trash", [src])["id"], background=False)
    assert job.state == "done"
    assert not os.path.exists(src)
    assert os.path.exists(os.path.join(fake_home, ".Trash", "Google"))
    log = [json.loads(line) for line in open(os.path.join(fake_home, ".spelunk", "actions.jsonl"))]
    assert log[-1]["action"] == "trash" and log[-1]["ok"]


def test_move_copies_verifies_and_writes_manifest(fake_home, drive):
    a = make(fake_home)
    src_dir = os.path.join(fake_home, "Desktop/Projects/app")
    os.symlink("node_modules/react/index.js", os.path.join(src_dir, "entry.js"))
    plan = a.plan("move", [src_dir], dest=drive)
    assert plan["dest"]["error"] is None
    job = a.execute(plan["id"], acknowledged=True, background=False)
    assert job.state == "done", job.results
    archived = os.path.join(drive, "Spelunk Archive", src_dir.lstrip("/"))
    assert os.path.isfile(os.path.join(archived, "node_modules/react/index.js"))
    assert os.path.islink(os.path.join(archived, "entry.js"))  # links kept where the drive allows
    assert not os.path.exists(src_dir)
    manifest = [json.loads(line) for line in open(os.path.join(drive, "Spelunk Archive", "manifest.jsonl"))]
    assert manifest[-1]["original"] == src_dir and manifest[-1]["verified"] == "sha256"


def test_failed_verification_keeps_original(fake_home, drive, monkeypatch):
    a = make(fake_home)
    src = os.path.join(fake_home, "Downloads/talk.mp4")
    calls = {"n": 0}

    def bad_hash(path, chunk=0):
        calls["n"] += 1
        return "a" if calls["n"] % 2 else "b"  # source and copy never match

    monkeypatch.setattr(actions_mod, "sha256_file", bad_hash)
    job = a.execute(a.plan("move", [src], dest=drive)["id"], acknowledged=True, background=False)
    assert job.state == "failed"
    assert os.path.exists(src), "original must survive a failed verification"


def test_move_rejects_destination_inside_home(fake_home):
    a = make(fake_home)
    plan = a.plan("move", [os.path.join(fake_home, "Downloads/talk.mp4")], dest=os.path.join(fake_home, "Documents"))
    assert plan["dest"]["error"]
    assert plan["count"] == 0


def test_moving_apps_to_exfat_warns(fake_home, drive, monkeypatch):
    from spelunk import system
    app = os.path.join(fake_home, "Applications", "Old.app", "Contents")
    os.makedirs(app)
    with open(os.path.join(app, "Info.plist"), "w") as f:
        f.write("x")
    monkeypatch.setattr(system, "filesystem", lambda p: "exfat")
    a = make(fake_home)
    plan = a.plan("move", [os.path.join(fake_home, "Applications", "Old.app")], dest=drive)
    assert any("exfat" in w for w in plan["warnings"])
    plan = a.plan("move", [os.path.join(fake_home, "Downloads/talk.mp4")], dest=drive)
    assert not any("exfat" in w for w in plan["warnings"])


def test_spelunk_never_removes_itself(fake_home):
    a = make(fake_home)
    me = os.path.join(fake_home, "Applications", "Spelunk.app")
    os.makedirs(me)
    a.self_bundle = me
    assert a.check(me)["blocked"]
    assert a.check(me + "/Contents")["blocked"]


def test_trash_falls_back_when_finder_is_unavailable(fake_home, monkeypatch):
    import subprocess
    a = make(fake_home)
    a.use_finder = True
    monkeypatch.setattr(subprocess, "run", lambda *args, **kw: (_ for _ in ()).throw(OSError("no osascript")))
    src = os.path.join(fake_home, "Downloads/installer.dmg")
    job = a.execute(a.plan("trash", [src])["id"], background=False)
    assert job.state == "done" and os.path.exists(os.path.join(fake_home, ".Trash", "installer.dmg"))
