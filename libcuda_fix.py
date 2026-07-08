# --- adarai libcuda fix (2026-07-08) ---
# Fixes Triton's JIT-compiled CUDA launcher stub failing to link with
# "cannot find -lcuda" on RunPod serverless GPU workers (SageAttention's
# Triton kernels, used by the PathchSageAttentionKJ node whenever
# sage_attention != "disabled"). Root cause: gcc's `-lcuda` linker flag
# requires an UNVERSIONED `libcuda.so` file on the library path, but the
# NVIDIA container runtime only bind-mounts the real, VERSIONED driver
# library (`libcuda.so.1`) into the container once RunPod schedules it onto
# a live GPU worker -- the plain `libcuda.so` dev symlink is a byproduct of
# a full driver package install that bare-metal GPU rental hosts routinely
# skip. This can only be fixed at process start on a real GPU worker (never
# at `docker build`, since no GPU/driver is attached then), so it is
# prepended to handler.py and runs on every real container boot, before
# ComfyUI/torch/Triton get a chance to run.
#
# Pure stdlib (os/subprocess/glob only) so it can't collide with handler.py's
# own imports. Safe no-op if the symlink already exists, and harmless if
# sage_attention stays "disabled" in the graph (Triton is never invoked, so
# this code path is simply never exercised).
import os as _os
import subprocess as _sp
import glob as _glob


def _adarai_fix_libcuda():
    candidates = [
        "/usr/lib/x86_64-linux-gnu",
        "/usr/lib/aarch64-linux-gnu",
        "/usr/lib/nvidia",
        "/usr/local/nvidia/lib64",
        "/usr/local/cuda/compat",
    ]
    found = None
    for d in candidates:
        p = _os.path.join(d, "libcuda.so.1")
        if _os.path.exists(p):
            found = p
            break
    if not found:
        for pattern in ("/usr/lib/**/libcuda.so.1", "/usr/local/**/libcuda.so.1"):
            hits = _glob.glob(pattern, recursive=True)
            if hits:
                found = hits[0]
                break
    if not found:
        print(
            "[adarai-libcuda-fix] WARNING: libcuda.so.1 not found on this "
            "worker, skipping (Triton/-lcuda will fail exactly as before "
            "if sage_attention is enabled)",
            flush=True,
        )
        return
    d = _os.path.dirname(found)
    link = _os.path.join(d, "libcuda.so")
    if _os.path.exists(link):
        print(f"[adarai-libcuda-fix] {link} already present, skipping", flush=True)
    else:
        try:
            _os.symlink(found, link)
            _sp.run(["ldconfig"], check=False)
            print(f"[adarai-libcuda-fix] symlinked {found} -> {link}", flush=True)
        except Exception as e:
            print(f"[adarai-libcuda-fix] WARNING: symlink failed: {e}", flush=True)
            return
    # Make Triton's lookup deterministic instead of relying on the ldconfig
    # cache having refreshed in time (avoids a cold-start race).
    _os.environ["TRITON_LIBCUDA_PATH"] = d


_adarai_fix_libcuda()
# --- end adarai libcuda fix ---
