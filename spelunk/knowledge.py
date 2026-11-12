"""Knowledge base: turns a path into a plain-language explanation.

Rules live in ``rules/*.json``. Each rule has one or more glob patterns:

* ``~`` expands to the user's home folder
* ``*`` matches inside one path segment, ``**`` matches across segments

The most specific matching rule wins (most literal characters). A rule with
``"inherit": true`` also explains everything inside the matched folder, so
``~/Library/Caches`` covers ``~/Library/Caches/com.foo/bar`` too.

Safety levels, from most to least deletable:

``safe``    Rebuilds itself or is a pure leftover. Deleting loses nothing.
``review``  Might be safe, depends on you (old downloads, venvs, models).
``keep``    An app needs it. Shrink it from inside that app instead.
``never``   System-critical or irreplaceable. Spelunk refuses to touch it.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

RULES_DIR = os.path.join(os.path.dirname(__file__), "rules")
SAFETY_ORDER = {"safe": 0, "review": 1, "keep": 2, "never": 3}

UNKNOWN = {
    "id": "unknown",
    "title": "Not in the knowledge base yet",
    "owner": "",
    "category": "unknown",
    "safety": "review",
    "what": "Spelunk doesn't recognise this path yet. Check what it is before removing it.",
    "if_removed": "Unknown.",
    "action": "none",
}


def _glob_to_regex(pattern: str) -> "re.Pattern[str]":
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
                continue
            if pattern[i : i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE)


class Rule:
    __slots__ = ("data", "patterns", "specificity", "inherit")

    def __init__(self, data: dict, home: str) -> None:
        self.data = data
        pats = data["match"] if isinstance(data["match"], list) else [data["match"]]
        self.patterns: List[Tuple["re.Pattern[str]", int]] = []
        for p in pats:
            expanded = home + p[1:] if p.startswith("~") else p
            literal = len(expanded.replace("*", "").replace("?", ""))
            # A pattern rooted at "/" is more trustworthy than "**/name".
            if expanded.startswith("/"):
                literal += 1000
            self.patterns.append((_glob_to_regex(expanded), literal))
        self.inherit = bool(data.get("inherit", False))

    def score(self, path: str) -> int:
        best = -1
        for rx, literal in self.patterns:
            if rx.match(path):
                best = max(best, literal)
        return best


class KnowledgeBase:
    def __init__(self, rules: List[dict], home: Optional[str] = None) -> None:
        self.home = home or os.path.expanduser("~")
        self.rules = [Rule(r, self.home) for r in rules]

    @classmethod
    def load(cls, directory: str = RULES_DIR, home: Optional[str] = None) -> "KnowledgeBase":
        rules: List[dict] = []
        for name in sorted(os.listdir(directory)):
            if name.endswith(".json"):
                with open(os.path.join(directory, name), encoding="utf-8") as f:
                    rules.extend(json.load(f)["rules"])
        return cls(rules, home)

    def _direct(self, path: str, inheriting_only: bool = False) -> Optional[Tuple[Rule, int]]:
        best: Optional[Tuple[Rule, int]] = None
        for rule in self.rules:
            if inheriting_only and not rule.inherit:
                continue
            s = rule.score(path)
            if s >= 0 and (best is None or s > best[1]):
                best = (rule, s)
        return best

    def explain(self, path: str) -> Dict:
        """Return the explanation for ``path`` (never ``None``)."""
        path = os.path.normpath(path)
        return dict(_cached_explain(self, path))

    def _ancestor(self, path: str) -> Optional[Tuple[Rule, str]]:
        """Closest ancestor folder whose rule is marked ``inherit``."""
        child, parent = path, os.path.dirname(path)
        while parent and parent != child:
            hit = self._direct(parent, inheriting_only=True)
            if hit:
                return hit[0], parent
            child, parent = parent, os.path.dirname(parent)
        return None

    def _explain(self, path: str) -> Dict:
        direct = self._direct(path)
        anc = self._ancestor(path)
        use_anc = False
        if anc and not direct:
            use_anc = True
        elif anc and direct and direct[1] < 1000:
            # A generic pattern (e.g. "**/*.mov") must not override a
            # protective folder rule (e.g. inside a Photos library), nor a
            # folder an app manages itself ("opaque", e.g. a tool runtime
            # whose node_modules is not "a project's dependencies").
            if SAFETY_ORDER[anc[0].data["safety"]] > SAFETY_ORDER[direct[0].data["safety"]]:
                use_anc = True
            elif anc[0].data.get("opaque"):
                use_anc = True
        if use_anc and anc:
            out = dict(anc[0].data)
            out["inherited"] = True
            out["inherited_from"] = anc[1]
        elif direct:
            out = dict(direct[0].data)
            out["inherited"] = False
        else:
            out = dict(UNKNOWN)
            out["inherited"] = False
        out.pop("match", None)
        out.pop("inherit", None)
        out.pop("opaque", None)
        return out

    def is_never(self, path: str) -> bool:
        return self.explain(path).get("safety") == "never"


@lru_cache(maxsize=200_000)
def _cached_explain(kb: KnowledgeBase, path: str) -> Tuple[Tuple[str, object], ...]:
    return tuple(kb._explain(path).items())


# Absolute guard rails that hold even if a rule file is edited badly.
HARD_DENY = [
    "/",
    "/System",
    "/bin",
    "/sbin",
    "/usr",
    "/etc",
    "/var",
    "/private",
    "/private/var",
    "/private/etc",
    "/private/var/db",
    "/private/var/vm",
    "/private/var/folders",
    "/Library",
    "/Applications",
    "/Users",
    "/Volumes",
    "/cores",
    "/opt",
    "/opt/homebrew",
]
HARD_DENY_PREFIXES = [
    "/System/",
    "/bin/",
    "/sbin/",
    "/private/var/db/",
    "/private/var/vm/",
    "/private/var/folders/",
    "/usr/bin/",
    "/usr/sbin/",
    "/usr/lib/",
    "/usr/libexec/",
    "/usr/share/",
]
HOME_DENY = [
    "",  # the home folder itself
    "Library",
    "Library/Keychains",
    "Library/Preferences",
    "Library/Application Support",
    "Library/Containers",
    "Library/Group Containers",
    "Desktop",
    "Documents",
    "Downloads",
    "Pictures",
    "Movies",
    "Music",
    ".ssh",
    ".gnupg",
]
HOME_DENY_PREFIXES = ["Library/Keychains/", ".ssh/", ".gnupg/"]


def hard_denied(path: str, home: Optional[str] = None) -> Optional[str]:
    """Reason string if ``path`` may never be deleted or moved, else ``None``."""
    home = os.path.normpath(home or os.path.expanduser("~"))
    p = os.path.normpath(path)
    if p in HARD_DENY:
        return "This is a top-level system folder."
    for prefix in HARD_DENY_PREFIXES:
        if p.startswith(prefix):
            return "This is part of macOS itself."
    if p == home or p.startswith(home + "/"):
        rel = "" if p == home else p[len(home) + 1 :]
        if rel in HOME_DENY:
            return "This is a top-level folder of your account. Act on items inside it instead."
        for prefix in HOME_DENY_PREFIXES:
            if rel.startswith(prefix):
                return "This holds passwords or keys."
    elif p.startswith("/Users/") and p.count("/") <= 2:
        return "This is a user's home folder."
    return None
