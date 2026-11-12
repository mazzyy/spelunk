import os

from spelunk.scanner import SMALL_BUCKET, Scanner, find, human, iter_nodes, remove


def test_totals_and_tree(fake_home):
    top = Scanner(min_node_bytes=1_000_000).scan([fake_home])
    root = top.children[0]
    assert root.path == fake_home
    assert root.size >= 11_000_000  # sum of the big files (allocated >= apparent)
    assert root.files == 56  # 6 big files + 50 notes; the symlink isn't counted
    caches = find(top, os.path.join(fake_home, "Library/Caches"))
    assert caches is not None and caches.size >= 5_000_000


def test_small_items_are_bucketed(fake_home):
    top = Scanner(min_node_bytes=100_000).scan([fake_home])
    notes = find(top, os.path.join(fake_home, "Documents/notes"))
    assert notes is not None
    names = [c.name for c in notes.children]
    assert names == [SMALL_BUCKET]  # the 50 tiny notes collapse into one bucket
    assert notes.children[0].files == 50
    assert notes.small_count == 50


def test_symlinks_are_not_followed(fake_home):
    top = Scanner(min_node_bytes=1).scan([fake_home])
    assert find(top, os.path.join(fake_home, "link-to-etc")) is None
    assert all("/etc/" not in n.path for n in iter_nodes(top))


def test_hardlinks_counted_once(tmp_path):
    a = tmp_path / "a.bin"
    a.write_bytes(os.urandom(2_000_000))
    os.link(a, tmp_path / "b.bin")
    top = Scanner(min_node_bytes=1).scan([str(tmp_path)])
    assert top.size < 3_000_000


def test_unreadable_folder_is_flagged(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "x").write_bytes(b"x" * 10)
    os.chmod(locked, 0)
    try:
        if os.access(locked, os.R_OK):  # running as root: permissions don't apply
            return
        top = Scanner(min_node_bytes=1).scan([str(tmp_path)])
        node = find(top, str(locked))
        assert node is not None and node.denied
    finally:
        os.chmod(locked, 0o755)


def test_remove_updates_ancestors(fake_home):
    top = Scanner(min_node_bytes=1).scan([fake_home])
    before = top.size
    node = find(top, os.path.join(fake_home, "Downloads/talk.mp4"))
    size = node.size
    remove(node)
    assert top.size == before - size
    assert find(top, os.path.join(fake_home, "Downloads/talk.mp4")) is None


def test_human():
    assert human(999) == "999 B"
    assert human(1_500_000) == "1.5 MB"
    assert human(80_000_000_000) == "80.0 GB"
