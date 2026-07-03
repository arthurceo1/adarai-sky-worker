import os, json
import numpy as np
import torch
from PIL import Image, ImageOps
import folder_paths

def _load_one(name):
    input_dir = folder_paths.get_input_directory()
    path = os.path.join(input_dir, name)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Uploaded image missing on disk: {name}")
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    rgb = img.convert("RGB")
    arr = np.array(rgb).astype(np.float32) / 255.0
    t = torch.from_numpy(arr)[None,]
    if "A" in img.getbands():
        m = np.array(img.getchannel("A")).astype(np.float32) / 255.0
        mask = (1.0 - torch.from_numpy(m)).unsqueeze(0)
    else:
        mask = torch.zeros((img.height, img.width), dtype=torch.float32).unsqueeze(0)
    return t, mask

class LoadImagesMultiUpload:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images_list": ("STRING", {"default": "[]", "multiline": True}),
                "mode": (["one at a time", "all at once"], {"default": "one at a time"}),
                "index": ("INT", {"default": 0, "min": 0, "max": 999999, "control_after_generate": True}),
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK", "INT")
    RETURN_NAMES = ("IMAGE", "MASK", "COUNT")
    OUTPUT_IS_LIST = (True, True, False)
    FUNCTION = "load"
    CATEGORY = "image"

    def load(self, images_list, mode, index):
        try:
            names = json.loads(images_list)
        except Exception:
            names = []
        if not names:
            raise ValueError("No images uploaded. Use the node's Add button or drag files onto it.")
        # Always one image per run.
        runs = len(names)
        r = index % runs
        name = names[r]
        print(f"[MultiUpload] run {r+1}/{runs}: image {r+1} of {runs}")
        img, mask = _load_one(name)
        return ([img], [mask], len(names))

    @classmethod
    def IS_CHANGED(cls, images_list, mode, index):
        return f"{mode}:{index}:{images_list}"
