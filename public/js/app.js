// 1. WebSocket for real-time terminal logs with auto-reconnect
function connectWebSocket() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/ws/logs`);
  ws.onmessage = (e) => {
    const term = document.getElementById("terminal");
    const line = document.createElement("div");
    line.textContent = `> ${e.data}`;
    term.appendChild(line);
    term.scrollTop = term.scrollHeight; // Auto-scroll to latest log
  };
  ws.onclose = () => setTimeout(connectWebSocket, 2000); // Reconnect after 2 seconds if closed
}
connectWebSocket();

function coordinateText(lat, lon) {
  if (lat === null || lat === undefined || lat === "" || lon === null || lon === undefined || lon === "") {
    return { lat: "N/A", lon: "N/A" };
  }
  const la = Number(lat);
  const lo = Number(lon);
  if (!Number.isFinite(la) || !Number.isFinite(lo) || (la === 0 && lo === 0)) {
    return { lat: "N/A", lon: "N/A" };
  }
  return { lat: la.toFixed(6), lon: lo.toFixed(6) };
}
function showPre(id, value) {
  const el = document.getElementById(id);
  el.innerText = value === null || value === undefined ? "Waiting..." : typeof value === "string" ? value : JSON.stringify(value, null, 2);
}
function tagPhrases(value) {
  if (Array.isArray(value)) return value.map((tag) => String(tag)).map((tag) => tag.trim()).filter(Boolean);
  return String(value || "").split(",").map((tag) => tag.trim()).filter(Boolean);
}

// 2. Fetch the pending assets and populate the review board & telemetry
async function loadQueue() {
  try {
    const res = await fetch("/api/assets/pending");
    const assets = await res.json();
    
    document.getElementById("queue-status").innerText = `Queue: ${assets.length}`;
    
    if(assets.length > 0) {
      const asset = assets[0];
      document.getElementById("asset-id").innerText = asset.temp_id || asset.asset_id;
      const tags = tagPhrases(asset.tags || asset.service_tags);
      const gps = coordinateText(asset.latitude, asset.longitude);
      
      if (asset.journal) {
        showPre("screener-json", {
          media_kind: asset.media_kind,
          shot_characteristic: asset.shot_characteristic,
          location_id: asset.location_id,
          path: asset.path || asset.staging_path || asset.original_path || asset.source_path,
          approval_state: asset.approval_state,
        });
        showPre("verifier-json", { alt_text: asset.alt_text ?? null });
        showPre("judge-json", { tags, overrides: asset.overrides || [] });
      } else {
        showPre("screener-json", asset.screener_output);
        showPre("verifier-json", asset.verifier_output);
        showPre("judge-json", asset.judge_output);
      }

      
      // Populate Expanded EXIF Metadata
      const pitch = asset.gimbal_pitch ?? asset.camera_pitch;
      document.getElementById("meta-sensor").innerText = asset.journal
        ? `Shot: ${asset.shot_characteristic || "N/A"}`
        : `Sensor: ${asset.sensor_model || "N/A"}`;
      document.getElementById("meta-focal").innerText = asset.journal
        ? `Kind: ${asset.media_kind || "N/A"}`
        : `Focal: ${asset.focal_length || "N/A"}`;
      document.getElementById("meta-pitch").innerText = asset.journal
        ? `Location: ${asset.location_id || "N/A"}`
        : (pitch === null || pitch === undefined || pitch === "" ? "Pitch: N/A" : `Pitch: ${pitch}`);
      document.getElementById("meta-alt").innerText = asset.altitude_meters === null || asset.altitude_meters === undefined || asset.altitude_meters === ""
        ? "Alt: N/A"
        : `Alt: ${parseFloat(asset.altitude_meters).toFixed(1)}m`;
      document.getElementById("meta-lat").innerText = gps.lat;
      document.getElementById("meta-lon").innerText = gps.lon;

      // Calculate and color-code the score badge
      const scoreElement = document.getElementById("ai-score");
      const scoreMissing = asset.agreement_score === null || asset.agreement_score === undefined || asset.agreement_score === "";
      const scoreValue = scoreMissing ? null : parseFloat(asset.agreement_score);
      const percentage = scoreValue === null || Number.isNaN(scoreValue) ? null : Math.round(scoreValue * 100);
      
      scoreElement.className = "px-3 py-1 rounded font-bold text-sm border";
      if (percentage === null) {
        scoreElement.innerText = "--%";
        scoreElement.classList.add("bg-slate-800", "text-slate-400", "border-slate-700");
      } else if (percentage >= 80) {
        scoreElement.innerText = `${percentage}%`;
        scoreElement.classList.add("bg-emerald-900/50", "text-emerald-400", "border-emerald-800");
      } else if (percentage >= 50) {
        scoreElement.innerText = `${percentage}%`;
        scoreElement.classList.add("bg-amber-900/50", "text-amber-400", "border-amber-800");
      } else {
        scoreElement.innerText = `${percentage}%`;
        scoreElement.classList.add("bg-red-900/50", "text-red-400", "border-red-800");
      }
    } else {
      // Reset view if queue is empty
      document.getElementById("asset-id").innerText = "No Asset";
      document.getElementById("screener-json").innerText = "Waiting...";
      document.getElementById("verifier-json").innerText = "Waiting...";
      document.getElementById("judge-json").innerText = "Waiting...";
      document.getElementById("ai-score").innerText = "--%";
      document.getElementById("ai-score").className = "px-3 py-1 rounded font-bold text-sm bg-slate-800 text-slate-400 border border-slate-700";
      document.getElementById("meta-sensor").innerText = "N/A";
      document.getElementById("meta-focal").innerText = "N/A";
      document.getElementById("meta-pitch").innerText = "N/A";
      document.getElementById("meta-lat").innerText = "N/A";
      document.getElementById("meta-lon").innerText = "N/A";
      document.getElementById("meta-alt").innerText = "N/A";
    }
  } catch (err) {
    console.error("Failed to load queue:", err);
  }
}

// 3. Staging Queue Explorer Logic
async function loadStaging() {
  try {
    const res = await fetch("/api/storage/staged");
    const files = await res.json();
    const list = document.getElementById("staging-list");

    if(files.length === 0) {
       list.innerHTML = `<div class="text-xs text-slate-500 italic">No files in active folder.</div>`;
       return;
    }

    // Group files by subfolder
    const folders = {};
    files.forEach(f => {
      if(!folders[f.folder]) folders[f.folder] = [];
      folders[f.folder].push(f);
    });

    list.replaceChildren();
    for(const [folder, folderFiles] of Object.entries(folders)) {
      const folderName = folder === '.' ? 'Root Directory' : folder;
      const group = document.createElement("div");
      const folderLabel = document.createElement("label");
      folderLabel.className = "flex items-center gap-2 text-xs text-blue-400 font-bold mb-1 cursor-pointer";
      const folderCheckbox = document.createElement("input");
      folderCheckbox.type = "checkbox";
      folderCheckbox.className = "folder-cb rounded bg-slate-800 border-slate-700 text-emerald-500";
      folderCheckbox.addEventListener("change", () => toggleFolder(folderCheckbox, folder));
      const folderText = document.createElement("span");
      folderText.textContent = `Folder: ${folderName}`;
      folderLabel.append(folderCheckbox, folderText);

      const filesContainer = document.createElement("div");
      filesContainer.className = "pl-4 border-l border-slate-800 ml-1 space-y-1";
      folderFiles.forEach(f => {
        const fileLabel = document.createElement("label");
        fileLabel.className = "flex items-center gap-2 text-xs text-slate-300 cursor-pointer hover:text-white";
        const fileCheckbox = document.createElement("input");
        fileCheckbox.type = "checkbox";
        fileCheckbox.dataset.folder = folder;
        fileCheckbox.className = "staged-file-cb rounded bg-slate-800 border-slate-700 text-emerald-500";
        fileCheckbox.value = f.path;
        const fileName = document.createElement("span");
        fileName.className = "truncate";
        fileName.title = f.name;
        fileName.textContent = f.name;
        fileLabel.append(fileCheckbox, fileName);
        filesContainer.appendChild(fileLabel);
      });
      group.append(folderLabel, filesContainer);
      list.appendChild(group);
    }
  } catch (err) {
    console.error("Failed to load staging files:", err);
  }
}

function toggleAllStaged(masterCheckbox) {
  document.querySelectorAll(".staged-file-cb, .folder-cb").forEach(cb => cb.checked = masterCheckbox.checked);
}

function toggleFolder(folderCheckbox, folderName) {
  document.querySelectorAll(`.staged-file-cb[data-folder="${folderName}"]`).forEach(cb => cb.checked = folderCheckbox.checked);
}

async function ingestSelected(btn) {
  const cbs = document.querySelectorAll(".staged-file-cb:checked");
  const paths = Array.from(cbs).map(cb => cb.value);
  
  if(paths.length === 0) return alert("Please select at least one file to ingest.");
  
  const origText = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = "Ingesting...";
  btn.classList.add("opacity-50");
  
  try {
    await fetch("/api/pipeline/ingest", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ paths })
    }).then(ensureSuccess);
    await loadStaging();
    await loadQueue();
  } catch (err) {
    console.error("Ingest error:", err);
  } finally {
    btn.disabled = false;
    btn.innerHTML = origText;
    btn.classList.remove("opacity-50");
  }
}

// 4. VRAM Toggle Control
async function toggleModel(modelName, checkboxElement) {
  const action = checkboxElement.checked ? 'load' : 'unload';
  checkboxElement.disabled = true;
  try {
    await fetch(`/api/system/vram/${action}/${modelName}`, { method: 'POST' }).then(ensureSuccess);
  } catch (error) {
    console.error("VRAM API Error:", error);
    checkboxElement.checked = !checkboxElement.checked; // Revert on failure
  }
  checkboxElement.disabled = false;
}

// 5. Trigger Orchestrator Batch
async function runOrchestrator(btn) {
  const origContent = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="animate-pulse">Running...</span>`;
  btn.classList.add("opacity-50", "cursor-not-allowed");
  
  try {
    await fetch("/api/pipeline/run-orchestrator", { method: 'POST' }).then(ensureSuccess);
    await loadQueue();
  } catch (error) {
    console.error("Orchestrator Error:", error);
  } finally {
    btn.disabled = false;
    btn.innerHTML = origContent;
    btn.classList.remove("opacity-50", "cursor-not-allowed");
  }
}

// 6. Approve Asset Action
async function approveAsset() {
  const assetId = document.getElementById("asset-id").innerText;
  if (assetId === "No Asset") return;
  window.location.href = `/review.html?asset_id=${encodeURIComponent(assetId)}`;
}

async function ensureSuccess(response) {
  if (response.ok) return response;
  throw new Error(await response.text());
}

// Initialize page data on load
loadStaging();
loadQueue();
