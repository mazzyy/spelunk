import os

from spelunk import duplicates as dup_mod
from spelunk.duplicates import DuplicateSearch
from spelunk.scanner import Scanner


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def test_finds_identical_files_only_in_user_folders(tmp_path):
    home = str(tmp_path / "h")
    a = os.urandom(3_000_000)
    b = bytearray(a)
    b[-1] ^= 0xFF  # same size, same start, different end
    write(f"{home}/Desktop/talk.mov", a)
    write(f"{home}/Downloads/talk (1).mov", a)
    write(f"{home}/Documents/other.mov", bytes(b))
    write(f"{home}/Movies/Old.app/Contents/talk.mov", a)          # inside an app bundle: skipped
    write(f"{home}/Library/Caches/talk.mov", a)                    # Library: skipped
    write(f"{home}/code/proj/node_modules/x/talk.mov", a)          # node_modules: skipped
    tree = Scanner(min_node_bytes=100_000).scan([home])
    s = DuplicateSearch()
    s.start(tree, home, min_bytes=1_000_000, background=False)
    snap = s.snapshot()
    assert snap["state"] == "done"
    assert len(snap["groups"]) == 1
    g = snap["groups"][0]
    assert g["count"] == 2 and g["wasted"] == 3_000_000
    assert {os.path.relpath(f["path"], home) for f in g["files"]} == {"Desktop/talk.mov", "Downloads/talk (1).mov"}


def test_unique_sizes_are_never_read(tmp_path, monkeypatch):
    home = str(tmp_path / "h")
    write(f"{home}/Desktop/a.bin", os.urandom(1_200_000))
    write(f"{home}/Desktop/b.bin", os.urandom(1_300_000))
    reads = []
    real = dup_mod._partial_hash
    monkeypatch.setattr(dup_mod, "_partial_hash", lambda p, size: reads.append(p) or real(p, size))
    tree = Scanner(min_node_bytes=100_000).scan([home])
    s = DuplicateSearch()
    s.start(tree, home, min_bytes=1_000_000, background=False)
    assert reads == [] and s.snapshot()["groups"] == []


def test_forget_removed_copies(tmp_path):
    home = str(tmp_path / "h")
    data = os.urandom(1_500_000)
    for n in ("a", "b", "c"):
        write(f"{home}/Documents/{n}.zip", data)
    tree = Scanner(min_node_bytes=100_000).scan([home])
    s = DuplicateSearch()
    s.start(tree, home, min_bytes=1_000_000, background=False)
    assert s.snapshot()["groups"][0]["count"] == 3
    s.forget([f"{home}/Documents/b.zip"])
    assert s.snapshot()["groups"][0]["count"] == 2
    s.forget([f"{home}/Documents/c.zip"])
    assert s.snapshot()["groups"] == []
