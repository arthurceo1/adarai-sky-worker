"""
BatchImageCollector - accumulate images across SEPARATE workflow runs, show them
in a built-in gallery, and save them all as a ZIP or as individual files.

Pairs with the Multi-Upload loader running "one at a time" (group size 1): each
queued prompt processes one image, but you want a single place that gathers ALL
of them. This node keeps a buffer that persists between prompt executions (keyed
by the node's UNIQUE_ID). Each run it appends the new image(s), shows the running
gallery, and once the buffer reaches `total` it outputs the whole stacked batch.
Until then it blocks its IMAGE output (via ExecutionBlocker) so a downstream Save
Image fires exactly once.

The gallery's "Save ZIP" / "Save to output" buttons call the server routes below,
which read the live buffer and write real files.

Wiring:
    Multi-Upload  IMAGE --> [ your per-image workflow ] --> images
    Multi-Upload  COUNT --------------------------------> total
    BatchImageCollector IMAGE --> Save Image / Preview Image   (optional)
"""

import io
import os
import zipfile

import numpy as np
import torch
from PIL import Image

import folder_paths

try:
    from comfy_execution.graph import ExecutionBlocker
except Exception:  # older ComfyUI without ExecutionBlocker
    ExecutionBlocker = None

_WARNED_NO_BLOCKER = False

# uid -> {"tensors": [ [1,H,W,C] ... ], "previews": [ {filename, subfolder, type} ... ]}
_BUFFERS = {}


def _unwrap(v):
    """INPUT_IS_LIST delivers every arg as a list; take the first scalar."""
    return v[0] if isinstance(v, list) else v


def _to_pil(t):
    """[1,H,W,C] or [H,W,C] float tensor -> PIL.Image (RGB/RGBA)."""
    a = t[0] if t.dim() == 4 else t
    arr = (a.cpu().numpy() * 255.0).clip(0, 255).astype(np.uint8)
    if arr.ndim == 2:
        return Image.fromarray(arr, "L").convert("RGB")
    if arr.shape[2] == 4:
        return Image.fromarray(arr, "RGBA")
    return Image.fromarray(arr[:, :, :3], "RGB")


class BatchImageCollector:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "total": ("INT", {"default": 0, "min": 0, "max": 1000000}),
            },
            "optional": {
                "reset": ("BOOLEAN", {"default": False, "label_on": "reset", "label_off": "keep"}),
            },
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("IMAGE", "INT")
    RETURN_NAMES = ("IMAGE", "COLLECTED")
    INPUT_IS_LIST = True
    OUTPUT_NODE = True          # always execute (it's a display/save node), even
                                # when its outputs aren't connected downstream
    FUNCTION = "collect"
    CATEGORY = "image"

    def collect(self, images, total, reset=False, unique_id=None):
        total = int(_unwrap(total) or 0)
        reset = bool(_unwrap(reset))
        uid = str(_unwrap(unique_id))

        # `images` arrives as a list (INPUT_IS_LIST); each element is a [B,H,W,C]
        # tensor. Split into single frames so a small batch still accumulates.
        incoming = []
        for t in (images if isinstance(images, list) else [images]):
            if t is None:
                continue
            for i in range(t.shape[0]):
                incoming.append(t[i : i + 1].clone())

        entry = _BUFFERS.get(uid)
        # Only start fresh on an explicit reset (the Clear button / reset toggle).
        # Otherwise keep accumulating across runs and batches.
        if reset or entry is None:
            entry = {"tensors": [], "previews": []}

        # Save every collected image to the OUTPUT folder by default (persistent
        # on the pod). The gallery shows these same output files.
        out_dir = folder_paths.get_output_directory()
        os.makedirs(out_dir, exist_ok=True)
        for t in incoming:
            entry["tensors"].append(t)
            pil = _to_pil(t)
            try:
                full_folder, filename, counter, subfolder, _ = folder_paths.get_save_image_path(
                    "batch_collected", out_dir, pil.width, pil.height
                )
                file = f"{filename}_{counter:05}_.png"
                pil.save(os.path.join(full_folder, file))
                entry["previews"].append({"filename": file, "subfolder": subfolder, "type": "output"})
            except Exception as e:
                print(f"[BatchCollector] output save failed: {e}")

        _BUFFERS[uid] = entry
        collected = len(entry["tensors"])
        print(f"[BatchCollector] {collected}" + (f"/{total}" if total else "") + " collected")

        # Custom key (not "images") so ComfyUI does NOT also render them natively
        # on the node — only our gallery widget shows them.
        ui = {"gallery": entry["previews"], "collected": [collected], "uid": [uid]}

        # Not complete yet: block the IMAGE output so a downstream Save fires once.
        blocking = total and collected < total
        if blocking and ExecutionBlocker is not None:
            return {"ui": ui, "result": (ExecutionBlocker(None), collected)}
        if blocking:
            global _WARNED_NO_BLOCKER
            if not _WARNED_NO_BLOCKER:
                print("[BatchCollector] ExecutionBlocker unavailable in this ComfyUI "
                      "build; the IMAGE output runs every step (a downstream Save Image "
                      "may save partial batches). The gallery + Save buttons still work.")
                _WARNED_NO_BLOCKER = True

        try:
            out = torch.cat(entry["tensors"], dim=0)
        except Exception:
            # Mixed sizes can't be stacked into one IMAGE batch; the gallery and
            # the Save buttons still handle them, so don't hard-fail the graph.
            print("[BatchCollector] collected images differ in size; the IMAGE output "
                  "carries only the latest frame. Use the gallery's Save buttons for all.")
            out = entry["tensors"][-1]
        return {"ui": ui, "result": (out, collected)}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # Must re-run on every queued prompt so accumulation happens each time.
        return float("nan")


# --------------------------------------------------------------------------- #
# Server routes for the gallery's Save buttons (read the live buffer).
# --------------------------------------------------------------------------- #
try:
    from aiohttp import web
    from server import PromptServer

    @PromptServer.instance.routes.get("/skynodes/collector/reset")
    async def _collector_reset(request):
        # Clear the in-memory buffer for a new batch. Files already written to the
        # output folder are left on disk (they're saved by default).
        uid = request.query.get("uid", "")
        _BUFFERS.pop(uid, None)
        return web.json_response({"ok": True})

    @PromptServer.instance.routes.get("/skynodes/collector/state")
    async def _collector_state(request):
        uid = request.query.get("uid", "")
        entry = _BUFFERS.get(uid)
        if not entry:
            return web.json_response({"gallery": [], "collected": 0})
        return web.json_response({"gallery": entry["previews"], "collected": len(entry["tensors"])})

    @PromptServer.instance.routes.get("/skynodes/collector/zip")
    async def _collector_zip(request):
        uid = request.query.get("uid", "")
        entry = _BUFFERS.get(uid)
        if not entry or not entry["tensors"]:
            return web.json_response({"error": "nothing collected"}, status=404)
        mem = io.BytesIO()
        with zipfile.ZipFile(mem, "w", zipfile.ZIP_STORED) as z:
            for i, t in enumerate(entry["tensors"], 1):
                png = io.BytesIO()
                _to_pil(t).save(png, format="PNG", compress_level=4)
                z.writestr(f"image_{i:03}.png", png.getvalue())
        mem.seek(0)
        return web.Response(
            body=mem.read(),
            headers={
                "Content-Type": "application/zip",
                "Content-Disposition": 'attachment; filename="batch_collected.zip"',
            },
        )

    @PromptServer.instance.routes.get("/skynodes/collector/save")
    async def _collector_save(request):
        uid = request.query.get("uid", "")
        prefix = request.query.get("prefix", "batch_collected")
        entry = _BUFFERS.get(uid)
        if not entry or not entry["tensors"]:
            return web.json_response({"error": "nothing collected"}, status=404)
        out_dir = folder_paths.get_output_directory()
        saved = []
        for t in entry["tensors"]:
            pil = _to_pil(t)
            full_folder, filename, counter, subfolder, _ = folder_paths.get_save_image_path(
                prefix, out_dir, pil.width, pil.height
            )
            file = f"{filename}_{counter:05}_.png"
            pil.save(os.path.join(full_folder, file))
            saved.append(os.path.join(subfolder, file) if subfolder else file)
        return web.json_response({"saved": saved, "count": len(saved)})

except Exception as _e:  # server not ready / unavailable
    print(f"[BatchCollector] save routes not registered: {_e}")
