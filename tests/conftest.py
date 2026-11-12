import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def write(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(os.urandom(size))


@pytest.fixture
def fake_home(tmp_path):
    """A small home folder with the shapes Spelunk cares about."""
    home = tmp_path / "home"
    h = str(home)
    write(f"{h}/Library/Caches/com.microsoft.VSCode.ShipIt/update.zip", 3_000_000)
    write(f"{h}/Library/Caches/Google/cache.bin", 2_000_000)
    write(f"{h}/Library/Keychains/login.keychain-db", 10_000)
    write(f"{h}/Desktop/Projects/app/node_modules/react/index.js", 2_500_000)
    write(f"{h}/Downloads/installer.dmg", 1_500_000)
    write(f"{h}/Downloads/talk.mp4", 2_200_000)
    for i in range(50):
        write(f"{h}/Documents/notes/n{i}.txt", 100)
    os.symlink("/etc", f"{h}/link-to-etc")
    return h


@pytest.fixture
def drive(tmp_path):
    d = tmp_path / "SSD"
    d.mkdir()
    return str(d)
