"""Foundation-model families: which seats share the weights that would make them collude.

Essay II.IV: "a common foundation model whose checkpoint releases act as a global
forcing function". II.III.b: "the general producer class and the general evaluator
class are not permitted to collude". A judge on its author's foundation model reads
the author's work through the same training, so the evaluation layer is kept
heterogeneous by family, never by route: a provider change is not a model change
(``venice:z-ai-glm-5-3-flash`` is ``z-ai/glm-5.3-flash`` served elsewhere), and a
size or tier of one model line (``gpt-5.6-sol``, ``gpt-5.6-luna``) is one family.
"""

from __future__ import annotations

import re

#: Route prefixes that name who serves a model, not which model it is.
PROVIDER_PREFIXES = ("venice:", "x402:", "openrouter:")

#: A token in a model id and the foundation family it names. The first token of the
#: id (after its route prefix) that matches a key's prefix decides the family.
FAMILY_TOKENS: tuple[tuple[str, str], ...] = (
    ("glm", "glm"), ("z", "glm"), ("zai", "glm"),
    ("qwen", "qwen"), ("qwq", "qwen"),
    ("deepseek", "deepseek"),
    ("openai", "gpt"), ("gpt", "gpt"), ("o1", "gpt"), ("o3", "gpt"), ("o4", "gpt"),
    ("anthropic", "claude"), ("claude", "claude"),
    ("google", "gemini"), ("gemini", "gemini"), ("gemma", "gemini"),
    ("meta", "llama"), ("llama", "llama"),
    ("mistral", "mistral"), ("mistralai", "mistral"), ("mixtral", "mistral"),
    ("x", "grok"), ("xai", "grok"), ("grok", "grok"),
    ("moonshotai", "kimi"), ("kimi", "kimi"),
    ("minimax", "minimax"),
)

_SPLIT = re.compile(r"[/\-_.\s]+")


def model_family(model_id: str) -> str:
    """The foundation family a model id belongs to, independent of the route serving it.

    Guarantees: the same family for an id and its provider-prefixed or ``:online``
    variants; one family for every size of one model line; a ``fake-`` test double
    is its own family (it stands for no foundation model, so two doubles never
    share one); the program seat is ``program``; an id with no known family token
    falls back to its vendor (the part before ``/``), else its first token.
    """
    mid = str(model_id).strip().lower()
    if mid.startswith("fake-"):
        return mid.split(":", 1)[0]
    for prefix in PROVIDER_PREFIXES:
        if mid.startswith(prefix):
            mid = mid[len(prefix):]
            break
    mid = mid.split(":", 1)[0]
    if mid == "program":
        return "program"
    tokens = [t for t in _SPLIT.split(mid) if t]
    for token in tokens:
        for key, family in FAMILY_TOKENS:
            if token == key or (len(key) > 2 and token.startswith(key)):
                return family
    if "/" in mid:
        return mid.split("/", 1)[0]
    return tokens[0] if tokens else mid
