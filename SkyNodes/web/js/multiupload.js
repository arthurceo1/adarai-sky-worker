import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

app.registerExtension({
  name: "skynodes.multiupload",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "LoadImagesMultiUpload") return;
    const onCreated = nodeType.prototype.onNodeCreated;

    nodeType.prototype.onNodeCreated = function () {
      onCreated?.apply(this, arguments);
      const node = this;
      const listWidget = node.widgets.find((w) => w.name === "images_list");
      const indexWidget = node.widgets.find((w) => w.name === "index");
      const modeWidget = node.widgets.find((w) => w.name === "mode");
      for (const w of node.widgets) {
        if (w.name !== "multiupload_ui") { w.computeSize = () => [0, -4]; w.hidden = true; }
      }
      const caf = node.widgets.find((w) => w.name === "control_after_generate" || w.name === "control after generate");
      if (caf) caf.value = "increment";
      if (modeWidget) modeWidget.value = "one at a time";
      if (indexWidget) indexWidget.value = 0;

      const dedupe = (a) => [...new Set(a)];
      const getNames = () => { try { return dedupe(JSON.parse(listWidget.value || "[]")); } catch { return []; } };
      const setNames = (n) => { listWidget.value = JSON.stringify(dedupe(n)); render(); };
      const alive = () => app.graph?.getNodeById(node.id) === node;
      const grp = () => 1;
      const runsNeeded = (n) => Math.ceil(n / grp());

      // Live batch state driven by ACTUAL execution events (not the index widget),
      // so an image is only "done" once its run has finished the whole workflow.
      let batch = { active: false, total: 0, completed: 0, running: -1 };
      let awaiting = false;            // a queued run is in flight, not yet completed
      const currentGroup = () => (batch.running >= 0 ? batch.running : batch.completed);
      const startBatch = (total) => { batch = { active: true, total, completed: 0, running: -1 }; awaiting = false; render(); };
      const resetBatch = () => { batch = { active: false, total: 0, completed: 0, running: -1 }; awaiting = false; };
      const beginRun = () => { batch.running = batch.completed; awaiting = true; render(); };
      const completeRun = () => {
        if (!batch.active || !awaiting) return;
        awaiting = false;
        batch.completed = Math.min(batch.completed + 1, batch.total);
        batch.running = -1;
        if (batch.completed >= batch.total) {
          batch.active = false;
          const n = getNames().length;
          status(`Batch complete (${n} of ${n})`, "#8fae7e");
        }
        render();
      };
      const thumbURL = (name) => {
        const i = name.lastIndexOf("/");
        return api.apiURL(`/view?filename=${encodeURIComponent(i<0?name:name.slice(i+1))}&subfolder=${encodeURIComponent(i<0?"":name.slice(0,i))}&type=input`);
      };
      // Content-addressed naming: hash the bytes so identical images collapse to
      // one entry (no re-upload dupes) and distinct same-named files never collide.
      const fileKey = async (file) => {
        const buf = new Uint8Array(await file.arrayBuffer());
        let h = (0x811c9dc5 ^ (file.size >>> 0)) >>> 0;
        for (let i = 0; i < buf.length; i++) { h ^= buf[i]; h = Math.imul(h, 0x01000193); }
        return (h >>> 0).toString(16).padStart(8, "0");
      };
      const keyOf = (name) => (name.split("/").pop().match(/^([0-9a-f]{8})_/) || [])[1] || null;
      const labelOf = (name) => name.split("/").pop().replace(/^[0-9a-f]{8}_/, "");

      const C = {
        bg: "#353535", line: "#444444", line2: "#5a5a5a",
        text: "#dddddd", mut: "#999999", dim: "#777777",
        btn: "#222222", btnH: "#2f2f2f", run: "#5a8fd6",
      };

      const el = document.createElement("div");
      el.style.cssText = `display:flex;flex-direction:column;box-sizing:border-box;height:100%;min-height:0;
        background:${C.bg};border:1px solid ${C.line};border-radius:3px;color:${C.text};
        font-family:system-ui,-apple-system,'Segoe UI',sans-serif;font-size:12px;overflow:hidden;`;

      el.innerHTML = `
        <div style="display:flex;align-items:center;gap:6px;padding:8px 10px;border-bottom:1px solid ${C.line};">
          <span data-r="sub" style="color:${C.mut};">0 images</span><span data-r="prog" title="Images finished out of total" style="color:${C.dim};font-variant-numeric:tabular-nums;"></span><span title="Upload images with Add or by dragging files onto this panel, then press Run batch. Images run one at a time, with progress shown below. Hover any control for help." style="display:inline-flex;align-items:center;justify-content:center;width:13px;height:13px;border:1px solid ${C.line2};border-radius:50%;color:${C.mut};font-size:9px;line-height:1;cursor:help;flex:none;">?</span>
          <div style="flex:1"></div>
          <button data-r="add" title="Pick multiple images at once (Ctrl/Shift-click in the file picker). You can also drag files straight onto this panel." style="background:${C.btn};border:1px solid ${C.line2};color:${C.text};font-size:12px;padding:4px 12px;border-radius:3px;cursor:pointer;">Add…</button>
          <button data-r="reset" style="background:none;border:1px solid ${C.line};color:${C.mut};font-size:12px;padding:4px 10px;border-radius:3px;cursor:pointer;" title="Clears the done marks and the progress bar so the next Run batch starts again from image 1. Does NOT remove your images.">Reset progress</button>
          <button data-r="clear" style="background:none;border:1px solid ${C.line};color:${C.mut};font-size:12px;padding:4px 10px;border-radius:3px;cursor:pointer;" title="Remove all images">Clear</button>
        </div>

        <div data-r="grid" style="flex:1;min-height:120px;overflow-y:auto;overflow-x:hidden;display:grid;
          grid-template-columns:repeat(auto-fill,72px);grid-auto-rows:72px;gap:6px;
          align-content:start;justify-content:start;padding:10px;background:#222222;"></div>

        <div style="height:2px;background:${C.line};">
          <div data-r="pfill" style="height:100%;width:0%;background:${C.mut};transition:width .3s;"></div>
        </div>
        <div style="display:flex;align-items:center;gap:8px;padding:7px 10px;border-top:1px solid ${C.line};">
          <span data-r="stext" title="Current activity: which image/group is running and which workflow stage" style="flex:1;color:${C.mut};white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">Ready</span>
          <button data-r="run" title="Starts the whole batch from image 1: queues one run per image automatically. Blocked while runs are still queued, so it can never double-queue." style="background:${C.btn};border:1px solid ${C.line2};color:${C.text};
            font-size:12px;padding:5px 16px;border-radius:3px;cursor:pointer;">Run batch</button>
        </div>`;
      const R = (k) => el.querySelector(`[data-r="${k}"]`);

      el.querySelectorAll("button").forEach((b) => {
        b.onmouseenter = () => (b.style.background = C.btnH);
        b.onmouseleave = () => (b.style.background = b.dataset.r === "add" || b.dataset.r === "run" ? C.btn : "none");
      });

      function paintMode() {
        status("Ready");
      }

      function status(text, color) { R("stext").textContent = text; R("stext").style.color = color || C.mut; }
      function progress() {
        const names = getNames();
        const doneImgs = Math.min(batch.completed * grp(), names.length);
        R("pfill").style.width = names.length ? `${(doneImgs / names.length) * 100}%` : "0%";
        R("prog").textContent = names.length ? `${doneImgs} / ${names.length} done` : "";
        return doneImgs;
      }

      function render() {
        const names = getNames();
        R("sub").textContent = `${names.length} image${names.length === 1 ? "" : "s"}`;
        progress();
        const grid = R("grid");
        grid.innerHTML = "";
        if (!names.length) {
          grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;color:${C.dim};padding:40px 0;">
            Drop images here, or click Add…</div>`;
          return;
        }
        names.forEach((name, i) => {
          const gsz = grp();
          const done = i < batch.completed * gsz;
          const isCurrent = batch.active && Math.floor(i / gsz) === currentGroup();
          const running = isCurrent && batch.running >= 0;
          const next = isCurrent && batch.running < 0;
          const accent = running ? C.run : next ? C.mut : C.line;
          const card = document.createElement("div");
          card.style.cssText = `position:relative;width:72px;height:72px;border-radius:2px;overflow:hidden;
            border:1px solid ${accent};background:#1c1c1c;box-sizing:border-box;`;
          card.innerHTML = `
            <img src="${thumbURL(name)}" loading="lazy" style="position:absolute;inset:0;width:72px;height:72px;object-fit:cover;display:block;${done ? "opacity:.35;" : ""}">
            ${done ? `<span style="position:absolute;top:3px;left:3px;color:${C.text};font-size:10px;
              background:rgba(34,34,34,.9);padding:0 4px;border-radius:2px;">done</span>` : ""}
            ${running ? `<span style="position:absolute;top:3px;left:3px;color:#fff;background:${C.run};font-size:10px;
              padding:0 4px;border-radius:2px;">running</span>` : ""}
            ${next ? `<span style="position:absolute;top:3px;left:3px;color:#141414;background:${C.text};font-size:10px;
              padding:0 4px;border-radius:2px;">next</span>` : ""}
            <button data-x style="position:absolute;top:2px;right:2px;background:rgba(34,34,34,.9);
              border:1px solid ${C.line2};color:${C.mut};border-radius:2px;width:18px;height:18px;cursor:pointer;
              display:none;align-items:center;justify-content:center;font-size:10px;line-height:1;padding:0;">✕</button>
            <span style="position:absolute;bottom:0;left:0;right:0;background:rgba(26,26,26,.9);
              color:${C.dim};font-size:9px;padding:2px 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:none;">
              ${i + 1}  ${labelOf(name)}</span>`;
          const x = card.querySelector("[data-x]");
          card.onmouseenter = () => { card.style.borderColor = C.line2; x.style.display = "flex"; };
          card.onmouseleave = () => { card.style.borderColor = accent; x.style.display = "none"; };
          x.onclick = (e) => {
            e.stopPropagation();
            const n = getNames(); const p = n.indexOf(name);
            if (p >= 0) { n.splice(p, 1); setNames(n); }
          };
          grid.appendChild(card);
        });
      }

      let uploading = false;
      async function uploadFiles(fileList) {
        if (uploading) return;               // guard against double-fired drops
        const files = [...fileList].filter((f) => f.type.startsWith("image/"));
        if (!files.length) return;
        uploading = true;
        const seenKeys = new Set(getNames().map(keyOf).filter(Boolean));
        const uploaded = [];
        let done = 0, skipped = 0;
        try {
          for (const file of files) {
            done++;
            try {
              const key = await fileKey(file);
              if (seenKeys.has(key)) { skipped++; continue; }   // identical content already present
              seenKeys.add(key);
              const safe = file.name.replace(/[^\w.\-]+/g, "_");
              const finalName = `${key}_${safe}`;
              const body = new FormData();
              body.append("image", new File([file], finalName, { type: file.type }));
              body.append("subfolder", "multi_upload");
              body.append("overwrite", "true");
              const resp = await api.fetchApi("/upload/image", { method: "POST", body });
              if (resp.status === 200) {
                const d = await resp.json();
                uploaded.push((d.subfolder ? d.subfolder + "/" : "") + d.name);
              } else {
                console.error("upload failed:", file.name, resp.status);
              }
            } catch (e) { console.error("upload failed:", file.name, e); }
            status(`Uploading ${done} of ${files.length}…`);
          }
          if (uploaded.length) setNames([...getNames(), ...uploaded]);
          status(skipped ? `Ready — skipped ${skipped} duplicate${skipped === 1 ? "" : "s"}` : "Ready");
        } finally {
          uploading = false;
        }
      }

      let queueRemaining = 0;
      const onQueueStatus = ({ detail }) => {
        const q = detail?.exec_info?.queue_remaining;
        if (typeof q === "number") queueRemaining = q;
      };
      api.addEventListener("status", onQueueStatus);

      R("run").onclick = () => {
        const n = getNames().length;
        if (!n) return status("No images loaded", "#b86a64");
        if (queueRemaining > 0)
          return status(`${queueRemaining} runs still queued. Wait or clear the queue first`, "#b86a64");
        if (caf) caf.value = "increment";
        if (indexWidget) indexWidget.value = 0;
        const r = runsNeeded(n);
        startBatch(r);
        status(grp() > 1 ? `Running ${r} groups of ${grp()} (${n} images)` : `Running image 1 of ${n}`);
        app.queuePrompt(0, r);
      };
      R("add").onclick = () => {
        const input = document.createElement("input");
        input.type = "file"; input.multiple = true; input.accept = "image/*";
        input.onchange = () => uploadFiles(input.files);
        input.click();
      };
      R("reset").onclick = () => { if (indexWidget) indexWidget.value = 0; resetBatch(); status("Progress reset. Next run starts at image 1"); render(); };
      R("clear").onclick = () => { if (indexWidget) indexWidget.value = 0; resetBatch(); setNames([]); status("Ready"); };

      const grid = R("grid");
      ["dragenter","dragover"].forEach((ev) => el.addEventListener(ev, (e) => {
        e.preventDefault(); e.stopPropagation(); grid.style.background = "#2a2a2a";
      }));
      ["dragleave","drop"].forEach((ev) => el.addEventListener(ev, (e) => {
        e.preventDefault(); e.stopPropagation(); grid.style.background = "#222222";
      }));
      el.addEventListener("drop", (e) => { if (e.dataTransfer?.files?.length) uploadFiles(e.dataTransfer.files); });

      const onExecuting = ({ detail }) => {
        if (!alive()) return;
        if (detail == null) { completeRun(); return; }    // prompt fully finished
        // Our loader node is now executing → this group's run is genuinely in flight.
        if (batch.active && batch.running < 0 && String(detail) === String(node.id)) beginRun();
        if (batch.active) {
          const names = getNames();
          const g = currentGroup();
          const lo = g * grp(), hi = Math.min(lo + grp(), names.length);
          const n2 = app.graph.getNodeById(Number(detail));
          status(`Image${grp()>1?"s":""} ${Math.min(lo + 1, names.length)}${grp()>1?"-"+hi:""} of ${names.length}: ${n2?.title || n2?.type || "running"}`);
        }
        progress();
      };
      const onSuccess = () => { if (alive()) completeRun(); };
      const onError = () => {
        if (!alive() || !batch.active) return;
        batch.active = false; batch.running = -1; awaiting = false;
        status("Execution stopped before the batch finished", "#b86a64");
        render();
      };
      api.addEventListener("executing", onExecuting);
      api.addEventListener("execution_success", onSuccess);
      api.addEventListener("execution_error", onError);
      api.addEventListener("execution_interrupted", onError);
      const onRemovedPrev = node.onRemoved;
      node.onRemoved = function () {
        api.removeEventListener("executing", onExecuting);
        api.removeEventListener("execution_success", onSuccess);
        api.removeEventListener("execution_error", onError);
        api.removeEventListener("execution_interrupted", onError);
        api.removeEventListener("status", onQueueStatus);
        onRemovedPrev?.apply(this, arguments);
      };

      // --- Sizing: fixed panel, height locked to fit (no gap, no creep) ---
      // The panel is a fixed 360px, so the default computeSize reports a constant
      // fit-height (slots + panel + margins) that never depends on the live node
      // size. We clamp height to that on every resize and allow width-only
      // resizing (wider = more thumbnail columns).
      const w = node.addDOMWidget("multiupload_ui", "div", el, { serialize: false, hideOnZoom: false });
      w.computeSize = (width) => [width, 360];
      node.resizable = true;
      const fitHeight = () => node.computeSize()[1];
      const prevResize = node.onResize;
      node.onResize = function (size) { size[1] = fitHeight(); prevResize?.apply(this, arguments); };
      node.size = [Math.max(node.size[0] || 0, 380), fitHeight()];

      node.onDragOver = (e) => !!(e.dataTransfer && [...e.dataTransfer.items].some((i) => i.kind === "file"));
      node.onDragDrop = async (e) => { const f = e.dataTransfer?.files; if (!f?.length) return false; await uploadFiles(f); return true; };

      // On page reload the saved images_list is restored AFTER onNodeCreated runs,
      // so the first render() sees an empty list. Re-render once values are applied.
      const onConfigurePrev = node.onConfigure;
      node.onConfigure = function () { onConfigurePrev?.apply(this, arguments); render(); };
      requestAnimationFrame(() => { if (alive()) render(); });   // belt-and-suspenders

      paintMode();
      render();
    };
  },
});
