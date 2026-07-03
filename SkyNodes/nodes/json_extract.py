"""
ExtractJSON - clean a VLM's raw text down to ONE valid JSON object.

The gemma "abliterated" vision model often ignores "minified JSON, no analysis"
and dumps its whole reasoning before the answer, e.g.:

    <|channel>thought
    The user wants a JSON prompt... wait, the rule says blonde... let me refine
    the bbox... let me recheck the colors...
    <channel|>{"high_level_description": ...}

That monologue is what bloats the prompt. This node throws all of it away and
returns only the final minified JSON string, so a downstream CLIPTextEncode gets
a clean prompt instead of the model's thinking.

Wiring (drop it right after the VLM):
    VISION LLM (llama_cpp_instruct_adv)  STRING --> ExtractJSON  value
    ExtractJSON  json --------------------------> Text Concatenate / CLIPTextEncode

It is list-safe (the Multi-Upload loader makes everything a list), so it behaves
the same whether it gets a plain string or a 1-item list.
"""

import json


# Accept ANY upstream type (STRING, list-typed STRING, etc.), like AnyToString.
class _AnyType(str):
    def __ne__(self, other):
        return False


_any = _AnyType("*")


def _unwrap(v):
    """Multi-Upload outputs are lists; drill down to the first scalar."""
    while isinstance(v, (list, tuple)) and v:
        v = v[0]
    return v


def _balanced_json_objects(s):
    """Yield every top-level {...} substring that has balanced braces."""
    depth = 0
    start = None
    in_str = False
    esc = False
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    yield s[start:i + 1]
                    start = None


def _extract(text):
    text = str(text)

    # 1) If a reasoning/channel close-marker exists, keep only what's after the LAST one.
    for tok in ("<channel|>", "<|channel|>", "<|message|>", "assistantfinal", "</think>"):
        if tok in text:
            text = text.rsplit(tok, 1)[-1]

    # 2) Drop ```json fences.
    text = text.replace("```json", "").replace("```", "")

    # 3) Return the LAST balanced {...} block that actually parses as JSON (minified).
    candidates = list(_balanced_json_objects(text))
    for cand in reversed(candidates):
        try:
            obj = json.loads(cand)
            return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            continue

    # 4) Fallbacks: last balanced block as-is, else trimmed raw text.
    if candidates:
        return candidates[-1].strip()
    return text.strip()


class ExtractJSON:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "value": (_any, {"forceInput": True}),
            },
            "optional": {
                "passthrough_on_fail": (
                    "BOOLEAN",
                    {"default": True, "label_on": "keep raw", "label_off": "empty"},
                ),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("json",)
    FUNCTION = "run"
    CATEGORY = "utils"

    def run(self, value, passthrough_on_fail=True):
        raw = _unwrap(value)
        cleaned = _extract(raw)
        if not cleaned and not passthrough_on_fail:
            cleaned = ""
        print(f"[ExtractJSON] in={len(str(raw))} chars -> out={len(cleaned)} chars")
        return (cleaned,)
