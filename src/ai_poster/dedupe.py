import hashlib
import re
import unicodedata
from difflib import SequenceMatcher


def words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"(?m)^\s*(?:source|источник)\s*:.*$", "", text)
    return re.findall(r"\w+", text)


def content_key(text: str) -> str:
    return hashlib.sha256(" ".join(words(text)).encode()).hexdigest()


def near_identical(first: str, second: str) -> bool:
    a, b = words(first), words(second)
    if not a or not b:
        return False
    if a == b:
        return True
    bodies = [re.sub(r"(?im)^\s*(?:source|источник)\s*:.*$", "", t) for t in (first, second)]
    if set(re.findall(r"https?://\S+", bodies[0])) != set(re.findall(r"https?://\S+", bodies[1])):
        return False
    if {w for w in a if any(c.isdigit() for c in w)} != {
        w for w in b if any(c.isdigit() for c in w)
    }:
        return False
    return min(len(a), len(b)) >= 20 and SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.94


def overlap(first: str, second: str) -> float:
    # Shortlist only: this score never causes deletion or suppression by itself.
    a, b = ({w for w in words(t) if len(w) >= 5} for t in (first, second))
    if len(a & b) < 3:
        return 0
    return len(a & b) / max(1, min(len(a), len(b)))
