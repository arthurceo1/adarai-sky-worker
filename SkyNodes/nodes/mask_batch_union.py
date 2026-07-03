"""
MaskBatchUnion - merge ALL masks in a batch into ONE mask (pixel-wise max).

Use after SAM3 text segmentation when multiple detections (face + hair etc.)
come out as separate masks and you want one combined region.
"""

import torch


class MaskBatchUnion:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"masks": ("MASK",)}}

    RETURN_TYPES = ("MASK", "INT")
    RETURN_NAMES = ("mask", "count")
    FUNCTION = "union"
    CATEGORY = "mask"

    def union(self, masks):
        if masks.dim() == 2:          # [H,W] -> already single
            return (masks.unsqueeze(0), 1)
        n = masks.shape[0]
        merged = masks.max(dim=0, keepdim=True).values   # [1,H,W]
        return (merged, n)
