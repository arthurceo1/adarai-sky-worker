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
import re as _re


def _adarai_ldconfig_libcuda_dirs():
    # Mirror Triton's OWN lookup (triton/backends/nvidia/driver.py libcuda_dirs):
    # `ldconfig -p` output, lines mentioning libcuda.so.1, dirname of each match.
    # This is authoritative for what gcc/Triton will actually search — more
    # reliable than guessing well-known paths, since a minimal image may not
    # have /lib symlinked to /usr/lib (no usr-merge), putting the real file
    # somewhere our own guesses wouldn't cover.
    try:
        out = _sp.run(["ldconfig", "-p"], check=False, capture_output=True, text=True).stdout
    except Exception:
        return []
    dirs = set()
    for line in out.splitlines():
        if "libcuda.so.1" in line:
            m = _re.search(r"=>\s*(\S+)", line)
            if m:
                dirs.add(_os.path.dirname(m.group(1)))
    return sorted(dirs)


def _adarai_fix_libcuda():
    # 1) Authoritative: wherever ldconfig itself reports libcuda.so.1 (same
    #    source Triton's libcuda_dirs() reads).
    dirs = set(_adarai_ldconfig_libcuda_dirs())
    # 2) Backstop: well-known driver mount points, in case ldconfig's cache
    #    hasn't been refreshed yet at this point in boot (a real, documented
    #    race) or the file was mounted somewhere ldconfig doesn't index.
    for d in [
        "/usr/lib/x86_64-linux-gnu",
        "/lib/x86_64-linux-gnu",
        "/usr/lib/aarch64-linux-gnu",
        "/lib/aarch64-linux-gnu",
        "/usr/lib/nvidia",
        "/usr/local/nvidia/lib64",
        "/usr/local/cuda/compat",
    ]:
        if _os.path.exists(_os.path.join(d, "libcuda.so.1")):
            dirs.add(d)
    if not dirs:
        for pattern in ("/usr/lib/**/libcuda.so.1", "/lib/**/libcuda.so.1", "/usr/local/**/libcuda.so.1"):
            for hit in _glob.glob(pattern, recursive=True):
                dirs.add(_os.path.dirname(hit))
    if not dirs:
        print(
            "[adarai-libcuda-fix] WARNING: libcuda.so.1 not found anywhere on "
            "this worker, skipping (Triton/-lcuda will fail exactly as before "
            "if sage_attention is enabled)",
            flush=True,
        )
        return
    # Symlink in EVERY directory that has the versioned file — gcc is invoked
    # with multiple -L paths (Triton's own bundled dir + wherever it detected
    # libcuda.so.1), so covering every candidate is cheap insurance against
    # picking the "wrong" one relative to whatever -L list gcc ends up with.
    linked_dirs = []
    for d in sorted(dirs):
        real = _os.path.join(d, "libcuda.so.1")
        link = _os.path.join(d, "libcuda.so")
        if not _os.path.exists(real):
            continue
        if _os.path.exists(link):
            print(f"[adarai-libcuda-fix] {link} already present, skipping", flush=True)
            linked_dirs.append(d)
            continue
        try:
            _os.symlink(real, link)
            print(f"[adarai-libcuda-fix] symlinked {real} -> {link}", flush=True)
            linked_dirs.append(d)
        except Exception as e:
            print(f"[adarai-libcuda-fix] WARNING: symlink failed for {d}: {e}", flush=True)
    if not linked_dirs:
        print("[adarai-libcuda-fix] WARNING: found libcuda.so.1 but no symlink could be created", flush=True)
        return
    _sp.run(["ldconfig"], check=False)
    # Make Triton's lookup deterministic instead of relying on the ldconfig
    # cache having refreshed in time (avoids a cold-start race). Also prepend
    # every linked dir to LD_LIBRARY_PATH as a second, independent safety net
    # (Triton's libcuda_dirs() falls back to LD_LIBRARY_PATH if ldconfig -p
    # comes up empty).
    _os.environ["TRITON_LIBCUDA_PATH"] = linked_dirs[0]
    existing_ld = _os.environ.get("LD_LIBRARY_PATH", "")
    _os.environ["LD_LIBRARY_PATH"] = ":".join(linked_dirs + ([existing_ld] if existing_ld else []))
    print(f"[adarai-libcuda-fix] TRITON_LIBCUDA_PATH={linked_dirs[0]} LD_LIBRARY_PATH={_os.environ['LD_LIBRARY_PATH']}", flush=True)


_adarai_fix_libcuda()
# --- end adarai libcuda fix ---
