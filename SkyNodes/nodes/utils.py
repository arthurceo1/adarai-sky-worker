class _AnyType(str):
    def __ne__(self, other):
        return False

_any = _AnyType("*")

class AnyToString:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"value": (_any, {})}}
    RETURN_TYPES = ("STRING",)
    FUNCTION = "run"
    CATEGORY = "utils"
    def run(self, value):
        return (str(value),)
