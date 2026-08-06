"""Sky Nodes - custom ComfyUI node pack.

Add a new node: create a module in nodes/ that defines node classes,
then register it in the imports + mappings below.
"""
from .nodes.multi_upload import LoadImagesMultiUpload
from .nodes.utils import AnyToString
from .nodes.mask_batch_union import MaskBatchUnion
from .nodes.batch_collector import BatchImageCollector
from .nodes.json_extract import ExtractJSON
from .nodes.lora_download import SkyLoraDownload

NODE_CLASS_MAPPINGS = {
    "LoadImagesMultiUpload": LoadImagesMultiUpload,
    "AnyToString": AnyToString,
    "MaskBatchUnion": MaskBatchUnion,
    "BatchImageCollector": BatchImageCollector,
    "ExtractJSON": ExtractJSON,
    "SkyLoraDownload": SkyLoraDownload,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "LoadImagesMultiUpload": "Load Images (Multi-Upload)",
    "AnyToString": "Any to String (list-safe)",
    "MaskBatchUnion": "Mask Batch Union (merge all)",
    "BatchImageCollector": "Batch Image Collector (across runs)",
    "ExtractJSON": "Extract JSON (strip VLM thinking)",
    "SkyLoraDownload": "Sky LoRA Download (R2 -> loras)",
}
WEB_DIRECTORY = "./web/js"
