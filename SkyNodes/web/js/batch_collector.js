import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

app.registerExtension({
  name: "skynodes.batchcollector",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "BatchImageCollector") return;
    const onCreated = nodeType.prototype.onNodeCreated;

    nodeType.prototype.onNodeCreated = function () {
      onCreated?.apply(this, arguments);
      const node = this;

      let images = [];      // [{filename, subfolder, type}]
      let uid = String(node.id);

      const C = {
        bg: "#353535", line: "#444444", line2: "#5a5a5a",
        text: "#dddddd", mut: "#999999", dim: "#777777",
        btn: "#222222", btnH: "#2f2f2f",
      };

      const thumbURL = (info) =>
        api.apiURL(`/view?filename=${encodeURIComponent(info.filename)}&subfolder=${encodeURIComponent(info.subfolder || "")}&type=${encodeURIComponent(info.type || "temp")}`);

      const el = document.createElement("div");
      el.style.cssText = `display:flex;flex-direction:column;box-sizing:border-box;height:100%;min-height:0;
        background:${C.bg};border:1px solid ${C.line};border-radius:3px;color:${C.text};
        font-family:system-ui,-apple-system,'Segoe UI',sans-serif;font-size:12px;overflow:hidden;`;

      el.innerHTML = `
        <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border-bottom:1px solid ${C.line};">
          <span data-r="sub" style="color:${C.mut};">0 images</span>
          <div style="flex:1"></div>
          <button data-r="zip" title="Download all collected images as a single .zip" style="background:${C.btn};border:1px solid ${C.line2};color:${C.text};font-size:12px;padding:4px 12px;border-radius:3px;cursor:pointer;">Save ZIP</button>
          <button data-r="save" title="Download every collected image individually to your computer" style="background:${C.btn};border:1px solid ${C.line2};color:${C.text};font-size:12px;padding:4px 12px;border-radius:3px;cursor:pointer;">Save single</button>
          <button data-r="clear" title="Remove all collected images from the gallery. Files already written to the output folder stay on disk." style="background:none;border:1px solid ${C.line};color:${C.mut};font-size:12px;padding:4px 12px;border-radius:3px;cursor:pointer;">Clear</button>
        </div>

        <div data-r="grid" style="flex:1;min-height:120px;overflow-y:auto;overflow-x:hidden;display:grid;
          grid-template-columns:repeat(auto-fill,72px);grid-auto-rows:72px;gap:6px;
          align-content:start;justify-content:start;padding:10px;background:#222222;"></div>

        <div style="display:flex;align-items:center;gap:8px;padding:7px 10px;border-top:1px solid ${C.line};">
          <span data-r="stext" style="flex:1;color:${C.mut};white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">Waiting for a run…</span>
        </div>`;
      const R = (k) => el.querySelector(`[data-r="${k}"]`);

      el.querySelectorAll("button").forEach((b) => {
        b.onmouseenter = () => (b.style.background = C.btnH);
        b.onmouseleave = () => (b.style.background = b.dataset.r === "clear" ? "none" : C.btn);
      });

      const status = (t, color) => { R("stext").textContent = t; R("stext").style.color = color || C.mut; };

      function render() {
        R("sub").textContent = `${images.length} image${images.length === 1 ? "" : "s"}`;
        const grid = R("grid");
        grid.innerHTML = "";
        if (!images.length) {
          grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;color:${C.dim};padding:40px 0;">
            Collected images will appear here after you Run batch</div>`;
          return;
        }
        images.forEach((info, i) => {
          const url = thumbURL(info);
          const card = document.createElement("div");
          card.style.cssText = `position:relative;width:72px;height:72px;border-radius:2px;overflow:hidden;
            border:1px solid ${C.line};background:#1c1c1c;box-sizing:border-box;cursor:pointer;`;
          card.title = "Click to open full size";
          card.innerHTML = `
            <img src="${url}" loading="lazy" style="position:absolute;inset:0;width:72px;height:72px;object-fit:cover;display:block;">
            <span style="position:absolute;bottom:0;left:0;right:0;background:rgba(26,26,26,.9);
              color:${C.dim};font-size:9px;padding:1px 4px;text-align:center;pointer-events:none;">${i + 1}</span>`;
          card.onmouseenter = () => (card.style.borderColor = C.line2);
          card.onmouseleave = () => (card.style.borderColor = C.line);
          card.onclick = () => window.open(url, "_blank");
          grid.appendChild(card);
        });
      }

      R("clear").onclick = async () => {
        images = [];
        render();
        status("Cleared");
        try { await api.fetchApi(`/skynodes/collector/reset?uid=${encodeURIComponent(uid)}`); } catch (e) { /* ignore */ }
      };

      R("zip").onclick = () => {
        if (!images.length) return status("Nothing collected yet", "#b86a64");
        const a = document.createElement("a");
        a.href = api.apiURL(`/skynodes/collector/zip?uid=${encodeURIComponent(uid)}`);
        a.download = "batch_collected.zip";
        document.body.appendChild(a); a.click(); a.remove();
        status(`Downloading ZIP of ${images.length} image${images.length === 1 ? "" : "s"}…`);
      };

      R("save").onclick = async () => {
        if (!images.length) return status("Nothing collected yet", "#b86a64");
        status(`Downloading ${images.length} image${images.length === 1 ? "" : "s"}…`);
        let ok = 0;
        for (let i = 0; i < images.length; i++) {
          try {
            const resp = await fetch(thumbURL(images[i]));
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `image_${String(i + 1).padStart(3, "0")}.png`;
            document.body.appendChild(a); a.click(); a.remove();
            URL.revokeObjectURL(url);
            ok++;
            await new Promise((r) => setTimeout(r, 150));   // avoid browser download throttling
          } catch (e) { console.error("download failed:", e); }
        }
        status(`Downloaded ${ok} image${ok === 1 ? "" : "s"}`, "#8fae7e");
      };

      // Primary, reliable path: pull the buffer state from the server. Works
      // regardless of per-node event quirks, and restores after a page reload.
      async function refreshFromServer() {
        try {
          const resp = await api.fetchApi(`/skynodes/collector/state?uid=${encodeURIComponent(uid)}`);
          if (resp.status !== 200) return;
          const d = await resp.json();
          if (Array.isArray(d.gallery)) {
            images = d.gallery;
            status(images.length ? `${d.collected} collected` : "Waiting for a run…", images.length ? "#8fae7e" : undefined);
            render();
          }
        } catch (e) { /* ignore */ }
      }

      // Fast path: this node's own execution payload (if the event delivers it).
      const onExecuted = ({ detail }) => {
        if (String(detail?.node) !== String(node.id)) return;
        const out = detail.output || {};
        if (Array.isArray(out.gallery)) { images = out.gallery; render(); }
        if (out.uid?.[0] != null) uid = String(out.uid[0]);
      };
      // Whole prompt finished → refresh from server (covers every case).
      const onSuccess = () => refreshFromServer();
      // Live status while the graph runs (no clearing — gallery stays put).
      const onExecStart = () => status("Running…");
      const onExecuting = ({ detail }) => {
        if (detail == null) return;
        const n2 = app.graph.getNodeById(Number(detail));
        status(`Running ${n2?.title || n2?.type || "…"}`);
      };
      api.addEventListener("executed", onExecuted);
      api.addEventListener("execution_success", onSuccess);
      api.addEventListener("execution_start", onExecStart);
      api.addEventListener("executing", onExecuting);
      // After a page reload ComfyUI restores the real node id during configure;
      // refresh once with the correct uid so the gallery comes back.
      const onConfigurePrev = node.onConfigure;
      node.onConfigure = function () { onConfigurePrev?.apply(this, arguments); uid = String(node.id); refreshFromServer(); };
      const onRemovedPrev = node.onRemoved;
      node.onRemoved = function () {
        api.removeEventListener("executed", onExecuted);
        api.removeEventListener("execution_success", onSuccess);
        api.removeEventListener("execution_start", onExecStart);
        api.removeEventListener("executing", onExecuting);
        onRemovedPrev?.apply(this, arguments);
      };
      requestAnimationFrame(refreshFromServer);   // restore gallery after page reload

      // --- Sizing: fixed panel, height locked to fit (no gap, no creep) ---
      const w = node.addDOMWidget("batchcollector_ui", "div", el, { serialize: false, hideOnZoom: false });
      w.computeSize = (width) => [width, 320];
      node.resizable = true;
      const fitHeight = () => node.computeSize()[1];
      const prevResize = node.onResize;
      node.onResize = function (size) { size[1] = fitHeight(); prevResize?.apply(this, arguments); };
      node.size = [Math.max(node.size[0] || 0, 360), fitHeight()];

      render();
    };
  },
});
