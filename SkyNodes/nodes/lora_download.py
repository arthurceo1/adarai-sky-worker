"""
SkyLoraDownload - fetch a creator's identity LoRA from R2 at RUNTIME, into the
worker's own loras folder, and hand its file name to a LoraLoaderModelOnly.

WHY IT EXISTS. Every model this worker needs is now baked into the Docker image,
which is what lets an endpoint run in ANY datacenter instead of being nailed to
the one holding its network volume. One family of files cannot be baked: the
per-creator identity LoRAs. A new creator is trained every few days, and
re-baking a multi-GB image (and re-tagging every endpoint) for a 200MB adapter
is not a deploy story anybody would keep up. So the app presigns the .safetensors
on R2 (short-lived GET url), passes the url + the destination file name in the
graph, and this node materialises the file on the container's local disk before
the loader runs. Second job on the same worker finds it already there.

WHAT IT GUARANTEES.
- Cache: an existing file of the expected size is reused, never re-downloaded.
- Atomicity: bytes land in a hidden `.part` file in the SAME directory, then
  `os.replace` flips it into place — a reader never sees a half file.
- Concurrency: an in-process lock plus an flock on a sidecar `.lock` file, so two
  jobs racing for the same creator on one worker download once, not twice, and
  never interleave writes on one path.
- Integrity: byte count vs Content-Length, vs the caller-declared `expected_size`
  when given, an optional sha256, and a cheap safetensors header parse. Any of
  them failing deletes the temp file and RAISES.
- Loudness: every failure path raises RuntimeError with the file name, the
  destination and the reason. It never returns a name it has not verified — a
  silent return here would let ComfyUI load a missing or truncated LoRA, i.e.
  ship a render under the wrong creator's face, which is exactly the class of bug
  the app's identity guard exists to prevent.

WIRING (see INTEGRATION.md for the full story):
    SkyLoraDownload  lora_name --> LoraLoaderModelOnly  lora_name

`lora_name` on the loader is normally a COMBO (a dropdown built from
folder_paths at import time), and a prompt is validated BEFORE any node runs, so
a literal name for a file that is not on disk yet is rejected up front. Feeding
it from a link sidesteps that: links are type-checked, not membership-checked,
and this node's wildcard return type satisfies the type check the same way
AnyToString's input does in the other direction.
"""

import hashlib
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request

import folder_paths

try:  # POSIX only; the worker is Linux, but keep the node importable anywhere.
    import fcntl
except Exception:  # pragma: no cover - non-POSIX
    fcntl = None


# Wildcard type, same trick as nodes/utils.py's AnyToString: `__ne__` always says
# "equal", which is what lets this output connect into LoraLoaderModelOnly's
# COMBO input without ComfyUI rejecting the link on a type mismatch.
class _AnyType(str):
    def __ne__(self, other):
        return False


_any = _AnyType("*")

_CHUNK = 1024 * 1024
_STALE_PART_SECONDS = 3600
_MAX_HEADER_BYTES = 64 * 1024 * 1024  # safetensors JSON header sanity ceiling

# filename -> threading.Lock (one ComfyUI process, several execution threads).
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(name):
    with _LOCKS_GUARD:
        lock = _LOCKS.get(name)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[name] = lock
        return lock


def _safe_name(raw):
    """A plain file name, never a path. Rejects traversal and wrong extensions.

    The value comes from the app, but it ends up as a path join on this box, so
    it is validated here rather than trusted: a `../` would write outside the
    models tree, and a non-.safetensors name would be a file the loader can
    never use anyway."""
    name = (raw or "").strip().strip('"').strip("'")
    if not name:
        raise RuntimeError("SkyLoraDownload: `filename` is empty — nothing to download or load.")
    if name != os.path.basename(name) or name in (".", ".."):
        raise RuntimeError(
            f"SkyLoraDownload: `filename` must be a bare file name, got {name!r} "
            "(no directories, no traversal)."
        )
    if not name.lower().endswith(".safetensors"):
        raise RuntimeError(
            f"SkyLoraDownload: `filename` must end in .safetensors, got {name!r}."
        )
    return name


def _cache_dir():
    """Where downloaded LoRAs live: a writable loras folder on the CONTAINER.

    Prefers a folder that is not on /runpod-volume — the whole point of this node
    is that the network volume is going away, and a cache written there would
    vanish with it (and be read-only on workers that never mount it). Falls back
    to <models_dir>/loras, registering it with folder_paths so the loader can
    resolve names from it."""
    override = (os.environ.get("SKY_LORA_CACHE_DIR") or "").strip()
    candidates = []
    if override:
        candidates.append(override)
    try:
        paths = list(folder_paths.get_folder_paths("loras") or [])
    except Exception:
        paths = []
    candidates += [p for p in paths if not str(p).startswith("/runpod-volume")]
    candidates += [p for p in paths if str(p).startswith("/runpod-volume")]
    try:
        candidates.append(os.path.join(folder_paths.models_dir, "loras"))
    except Exception:
        pass

    tried = []
    for d in candidates:
        if not d:
            continue
        tried.append(d)
        try:
            os.makedirs(d, exist_ok=True)
            if os.access(d, os.W_OK):
                if d not in paths:
                    try:
                        folder_paths.add_model_folder_path("loras", d)
                    except Exception:
                        pass
                return d
        except Exception:
            continue
    raise RuntimeError(
        "SkyLoraDownload: no writable loras directory on this worker "
        f"(tried: {tried or 'none'}). Set SKY_LORA_CACHE_DIR to a writable path."
    )


def _refresh_folder_cache():
    """Make the freshly written file visible to folder_paths.

    `get_full_path` stats the folders directly, so a load works even without
    this; the cached NAME LIST is what feeds the loader's dropdown and the
    validation of any literal `lora_name` elsewhere in the same prompt. Cheap
    enough to always do, and it keeps the two views of the folder consistent."""
    try:
        folder_paths.filename_list_cache.pop("loras", None)
    except Exception:
        pass
    try:
        folder_paths.cache_helper.clear()
    except Exception:
        pass
    try:
        folder_paths.get_filename_list("loras")
    except Exception:
        pass


def _sweep_stale_parts(directory, name):
    """Drop `.part` leftovers from a worker that died mid-download."""
    prefix = f".{name}."
    now = time.time()
    try:
        entries = os.listdir(directory)
    except Exception:
        return
    for e in entries:
        if not (e.startswith(prefix) and e.endswith(".part")):
            continue
        p = os.path.join(directory, e)
        try:
            if now - os.path.getmtime(p) > _STALE_PART_SECONDS:
                os.remove(p)
                print(f"[SkyLoraDownload] removed stale partial {e}")
        except Exception:
            pass


def _check_safetensors(path):
    """(ok, reason) — a real safetensors starts with an 8-byte little-endian
    header length followed by that many bytes of JSON. Catches an HTML error
    page, an XML S3 error body or a truncated file when no sha256 was given."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(8)
            if len(head) < 8:
                return False, "file is shorter than a safetensors header"
            n = int.from_bytes(head, "little")
            if n <= 0 or n + 8 > size:
                return False, f"declared header length {n} does not fit in {size} bytes"
            if n > _MAX_HEADER_BYTES:
                return False, f"declared header length {n} is implausibly large"
            raw = f.read(n)
        if len(raw) != n:
            return False, "header is truncated"
        obj = json.loads(raw.decode("utf-8"))
        if not isinstance(obj, dict):
            return False, "header JSON is not an object"
        return True, ""
    except Exception as e:
        return False, f"header unreadable ({e})"


def _fetch(url, tmp_path, expected_size, sha256, timeout_s):
    """One download attempt into `tmp_path`. Returns (bytes_written, digest)."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "SkyNodes-LoraDownload/1.0",
            # No transparent compression: the byte count is compared against
            # Content-Length, and a re-encoded body would break that check.
            "Accept-Encoding": "identity",
        },
    )
    h = hashlib.sha256() if sha256 else None
    written = 0
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        status = getattr(resp, "status", None) or resp.getcode()
        if status != 200:
            raise RuntimeError(f"HTTP {status} from the download url")
        clen = resp.headers.get("Content-Length")
        clen = int(clen) if (clen or "").isdigit() else None
        if clen is not None and expected_size > 0 and clen != expected_size:
            raise RuntimeError(
                f"the url serves {clen} bytes but the job expects {expected_size} "
                "— wrong object, or the file changed after the job was built"
            )
        with open(tmp_path, "wb") as out:
            while True:
                chunk = resp.read(_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                written += len(chunk)
                if h is not None:
                    h.update(chunk)
            out.flush()
            os.fsync(out.fileno())
    if clen is not None and written != clen:
        raise RuntimeError(f"truncated download: got {written} of {clen} bytes")
    if expected_size > 0 and written != expected_size:
        raise RuntimeError(f"size mismatch: got {written} bytes, expected {expected_size}")
    if written == 0:
        raise RuntimeError("the url returned an empty body")
    return written, (h.hexdigest() if h is not None else "")


class SkyLoraDownload:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "url": ("STRING", {"default": "", "multiline": True}),
                "filename": ("STRING", {"default": ""}),
            },
            "optional": {
                # 0 = unknown. Given, it is checked against Content-Length BEFORE
                # the body is read and against the byte count after, which is the
                # cheapest way to notice a wrong or half object.
                "expected_size": ("INT", {"default": 0, "min": 0, "max": 0x7FFFFFFF}),
                "sha256": ("STRING", {"default": ""}),
                "timeout_s": ("INT", {"default": 600, "min": 10, "max": 3600}),
                "retries": ("INT", {"default": 3, "min": 1, "max": 10}),
                "verify_header": (
                    "BOOLEAN",
                    {"default": True, "label_on": "verify", "label_off": "skip"},
                ),
            },
        }

    # Wildcard so the output can drive LoraLoaderModelOnly's COMBO `lora_name`
    # (see the module docstring). The VALUE is always a plain file name string.
    RETURN_TYPES = (_any,)
    RETURN_NAMES = ("lora_name",)
    FUNCTION = "run"
    CATEGORY = "loaders"

    def run(
        self,
        url,
        filename,
        expected_size=0,
        sha256="",
        timeout_s=600,
        retries=3,
        verify_header=True,
    ):
        name = _safe_name(filename)
        url = (url or "").strip()
        sha256 = (sha256 or "").strip().lower()
        expected_size = int(expected_size or 0)

        # Already resolvable anywhere folder_paths knows about (baked into the
        # image, or downloaded by an earlier job): reuse it.
        existing = None
        try:
            existing = folder_paths.get_full_path("loras", name)
        except Exception:
            existing = None
        if existing and os.path.isfile(existing):
            have = os.path.getsize(existing)
            if expected_size <= 0 or have == expected_size:
                print(f"[SkyLoraDownload] cache hit: {name} ({have} bytes) at {existing}")
                return (name,)
            print(
                f"[SkyLoraDownload] {name} on disk is {have} bytes but the job "
                f"expects {expected_size} — re-downloading"
            )

        if not url:
            raise RuntimeError(
                f'SkyLoraDownload: "{name}" is not on this worker and no download '
                "url was provided, so the LoRA cannot be loaded. Pass the presigned "
                "R2 url in the graph (see INTEGRATION.md)."
            )

        directory = _cache_dir()
        dest = os.path.join(directory, name)

        # A size-mismatched file that lives in ANOTHER folder (e.g. baked into the
        # image) must not be shadowed silently: two files with one name, and which
        # one loads then depends on folder order. Say so instead.
        if existing and os.path.isfile(existing) and os.path.abspath(existing) != os.path.abspath(dest):
            raise RuntimeError(
                f'SkyLoraDownload: "{name}" already exists at {existing} with a '
                f"different size ({os.path.getsize(existing)} vs expected {expected_size}). "
                "Refusing to write a second copy under the same name — rename the "
                "creator's LoRA or remove the stale file."
            )

        _sweep_stale_parts(directory, name)

        with _lock_for(name):
            lock_path = os.path.join(directory, f".{name}.lock")
            lock_fh = None
            try:
                if fcntl is not None:
                    lock_fh = open(lock_path, "a+")
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)

                # Double-checked: another thread/process may have finished while
                # we waited on the lock.
                if os.path.isfile(dest):
                    have = os.path.getsize(dest)
                    if expected_size <= 0 or have == expected_size:
                        print(f"[SkyLoraDownload] cache hit after lock: {name} ({have} bytes)")
                        _refresh_folder_cache()
                        return (name,)

                last = None
                for attempt in range(1, int(retries) + 1):
                    fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{name}.", suffix=".part")
                    os.close(fd)
                    try:
                        t0 = time.time()
                        written, digest = _fetch(url, tmp, expected_size, sha256, int(timeout_s))
                        if sha256 and digest != sha256:
                            raise RuntimeError(
                                f"sha256 mismatch: got {digest}, expected {sha256}"
                            )
                        if verify_header:
                            ok, why = _check_safetensors(tmp)
                            if not ok:
                                raise RuntimeError(f"downloaded file is not a valid safetensors ({why})")
                        os.replace(tmp, dest)
                        tmp = None
                        secs = time.time() - t0
                        mb = written / (1024 * 1024)
                        print(
                            f"[SkyLoraDownload] {name}: {mb:.1f} MB in {secs:.1f}s "
                            f"-> {dest}"
                        )
                        _refresh_folder_cache()
                        return (name,)
                    except (urllib.error.URLError, urllib.error.HTTPError, OSError, RuntimeError) as e:
                        last = e
                        print(
                            f"[SkyLoraDownload] attempt {attempt}/{retries} failed for {name}: {e}"
                        )
                        if attempt < int(retries):
                            time.sleep(min(2 ** attempt, 15))
                    finally:
                        if tmp and os.path.exists(tmp):
                            try:
                                os.remove(tmp)
                            except Exception:
                                pass

                # Out of attempts. Raise: returning the name here would let the
                # loader run against a file that is not there.
                raise RuntimeError(
                    f'SkyLoraDownload: could not download the creator LoRA "{name}" '
                    f"after {retries} attempts -> {dest}. Last error: {last}. "
                    "The presigned url may have expired (they are short-lived), the "
                    "object may be missing from R2, or the worker has no egress."
                )
            finally:
                if lock_fh is not None:
                    try:
                        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        pass
                    try:
                        lock_fh.close()
                    except Exception:
                        pass
