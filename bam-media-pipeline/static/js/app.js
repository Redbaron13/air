// 1. WebSocket for real-time terminal logs with auto-reconnect
function connectWebSocket() {
  const ws = new WebSocket(`ws://${window.location.host}/ws/logs`);
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

// 2. Fetch the pending assets and populate the review board & telemetry
async function loadQueue() {
  try {
    const res = await fetch("/api/assets/pending");
    const assets = await res.json();
    
    document.getElementById("queue-status").innerText = `Queue: ${assets.length}`;
    
    if(assets.length > 0) {
      const asset = assets[0];
      document.getElementById("asset-id").innerText = asset.asset_id;
      
      // Populate JSON boxes
      document.getElementById("screener-json").innerText = JSON.stringify(asset.screener_output, null, 2);
      document.getElementById("verifier-json").innerText = JSON.stringify(asset.verifier_output, null, 2);
      document.getElementById("judge-json").innerText = JSON.stringify(asset.judge_output, null, 2);

      
      // Populate Expanded EXIF Metadata
      document.getElementById("meta-sensor").innerText = `Sensor: ${asset.sensor_model || 'N/A'}`;
      document.getElementById("meta-focal").innerText = `Focal: ${asset.focal_length || 'N/A'}`;
      document.getElementById("meta-pitch").innerText = `Pitch: ${asset.camera_pitch || 'N/A'}`;
      document.getElementById("meta-alt").innerText = `Alt: ${asset.altitude_meters ? `${parseFloat(asset.altitude_meters).toFixed(1)}m` : 'N/A'}`;
      document.getElementById("meta-lat").innerText = asset.latitude ? parseFloat(asset.latitude).toFixed(6) : 'N/A';
      document.getElementById("meta-lon").innerText = asset.longitude ? parseFloat(asset.longitude).toFixed(6) : 'N/A';

      // Calculate and color-code the score badge
      const scoreElement = document.getElementById("ai-score");
      const scoreValue = parseFloat(asset.agreement_score) || 0.0;
      const percentage = Math.round(scoreValue * 100);
      
      scoreElement.innerText = `${percentage}%`;
      scoreElement.className = "px-3 py-1 rounded font-bold text-sm border";
      
      if (percentage >= 80) {
        scoreElement.classList.add("bg-emerald-900/50", "text-emerald-400", "border-emerald-800");
      } else if (percentage >= 50) {
        scoreElement.classList.add("bg-amber-900/50", "text-amber-400", "border-amber-800");
      } else {
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
  try {
    await fetch(`/api/assets/${assetId}/approve`, { method: "POST" }).then(ensureSuccess);
    const terminalLine = document.createElement("div");
    terminalLine.textContent = `> [SYSTEM] Asset ${assetId} approved.`;
    document.getElementById("terminal").appendChild(terminalLine);
    await loadQueue();
  } catch (err) {
    console.error("Approve error:", err);
  }
}

async function ensureSuccess(response) {
  if (response.ok) return response;
  throw new Error(await response.text());
}

// Initialize page data on load
loadStaging();
loadQueue();