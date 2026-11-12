import json
import os

from spelunk.knowledge import RULES_DIR, SAFETY_ORDER, KnowledgeBase, hard_denied

HOME = "/Users/test"
kb = KnowledgeBase.load(home=HOME)


def ex(rel):
    return kb.explain(HOME + "/" + rel if not rel.startswith("/") else rel)


def test_rules_file_is_valid():
    required = {"id", "match", "title", "category", "safety", "what", "if_removed", "action"}
    ids = set()
    for name in os.listdir(RULES_DIR):
        with open(os.path.join(RULES_DIR, name)) as f:
            for rule in json.load(f)["rules"]:
                assert required <= rule.keys(), rule.get("id")
                assert rule["safety"] in SAFETY_ORDER, rule["id"]
                assert rule["action"] in {"trash", "delete", "move", "app", "command", "none"}, rule["id"]
                assert rule["id"] not in ids, f"duplicate id {rule['id']}"
                ids.add(rule["id"])


def test_specific_beats_generic():
    assert ex("Library/Caches/com.microsoft.VSCode.ShipIt")["id"] == "caches-shipit"
    assert ex("Library/Caches/SomeApp")["id"] == "caches"
    assert ex("Library/Caches/com.apple.Safari")["id"] == "caches-apple"


def test_inheritance():
    e = ex("Library/Caches/Google/Chrome/Default/Cache")
    assert e["id"] == "caches-browsers" and e["inherited"]


def test_protective_folder_wins_over_generic_pattern():
    # A video inside the Photos library must never be offered for moving.
    e = ex("Pictures/Photos Library.photoslibrary/originals/A/IMG_1.mov")
    assert e["id"] == "photos-lib" and e["safety"] == "keep"


def test_never_rules():
    assert ex("Library/Keychains/login.keychain-db")["safety"] == "never"
    assert ex("/System/Library/CoreServices")["safety"] == "never"
    assert ex(".ssh/id_ed25519")["safety"] == "never"


def test_developer_rules():
    assert ex("code/app/node_modules")["id"] == "node-modules"
    assert ex("code/app/.venv")["id"] == "python-venv"
    assert ex("Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw")["id"] == "docker-raw"
    assert ex("Downloads/Thing.dmg")["id"] == "installers"


def test_unknown_is_review():
    e = ex("some/random/folder")
    assert e["id"] == "unknown" and e["safety"] == "review"


def test_hard_deny():
    assert hard_denied("/", HOME)
    assert hard_denied("/System/Library", HOME)
    assert hard_denied(HOME, HOME)
    assert hard_denied(HOME + "/Library", HOME)
    assert hard_denied(HOME + "/Documents", HOME)
    assert hard_denied(HOME + "/.ssh/id_rsa", HOME)
    assert hard_denied("/Users/someone", HOME)
    assert hard_denied(HOME + "/Library/Caches/Foo", HOME) is None
    assert hard_denied(HOME + "/Documents/old.mov", HOME) is None


def test_app_managed_folders_are_opaque():
    # node_modules inside a tool's runtime is not "a project's dependencies".
    e = ex(".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules")
    assert e["id"] == "codex-runtime"
    # ...but a real project's node_modules still is.
    assert ex("Desktop/Projects/app/node_modules")["id"] == "node-modules"
    # Anything inside an app's Application Support folder belongs to that app.
    e = ex("Library/Application Support/SomeApp/plugins/node_modules")
    assert e["id"] == "app-support-item"
    # Specific rooted rules inside still win.
    assert ex("Library/Application Support/Slack/Cache")["id"] == "electron-cache"
