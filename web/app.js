"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  let data = null;
  try { data = await r.json(); } catch { /* không phải JSON */ }
  if (!r.ok) throw new Error((data && (data.detail || data.message)) || `Lỗi ${r.status}`);
  return data;
}
const jsonOpts = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const STATUS = { queued: "Đang chờ", running: "Đang chạy", review: "Chờ duyệt", rendering: "Đang dựng", done: "Xong", error: "Lỗi", cancelled: "Đã huỷ" };
const PRESET_LABEL = { anime: "Anime / Donghua — chuẩn nguyên tác, 2 file SRT", film: "Phim / Drama có cốt truyện", show: "Show / Truyền hình", lecture: "Bài giảng / Podcast", game: "Game / Esports", vlog: "Vlog / Du lịch / Tài liệu" };
const TTS_LABEL = { omnivoice: "OmniVoice — clone giọng, chạy GPU (nhanh nhất, cần bật dịch vụ)", vieneu: "VieNeu — giọng Việt tự nhiên, tại máy (miễn phí)", piper: "Piper — chạy tại máy (miễn phí)", local_command: "Lệnh TTS tại máy (VieNeu…)", edge: "Edge online (miễn phí, cần mạng)", openai: "OpenAI (API key)", elevenlabs: "ElevenLabs (API key)" };
const WPM = { vi: 165, en: 150 };
let META = null;

// ---------------- tabs ----------------
function showTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
  $$("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  if (name !== "job") stopPolling();
  if (name === "jobs") loadJobs();
  if (name === "settings") loadSettings();
  if (name === "health") loadHealth();
}
$$("nav button").forEach((b) => b.addEventListener("click", () => { location.hash = b.dataset.tab; }));
window.addEventListener("hashchange", route);
function route() {
  const h = location.hash.slice(1) || "new";
  if (h.startsWith("job/")) { showTab("job"); openJob(h.slice(4)); }
  else showTab(h);
}

// ---------------- logo kênh ----------------
const LOGO_KEY = "recap_logo";
function logoLabel(f) { return f.replace(/\.[^.]+$/, ""); }
function fillLogos(selected) {
  const logos = META.logos || [];
  const opts = [`<option value="">${META.default_logo ? "Logo mặc định (trong Cài đặt)" : "Mặc định (Cài đặt chưa có logo → không gắn)"}</option>`,
    ...logos.map((f) => `<option value="${esc(f)}">Kênh: ${esc(logoLabel(f))}</option>`),
    `<option value="none">Không gắn logo</option>`];
  const sel = $("#logo");
  sel.innerHTML = opts.join("");
  sel.value = selected != null && [...sel.options].some((o) => o.value === selected) ? selected : "";
  showLogoPreview();
}
function showLogoPreview() {
  const v = $("#logo").value, img = $("#logoPreview");
  const src = v === "none" || (!v && !META.default_logo) ? "" : `/api/logos/${encodeURIComponent(v || "_default")}?t=${Date.now()}`;
  img.style.display = src ? "" : "none";
  if (src) img.src = src;
}
$("#logo").addEventListener("change", () => {
  try { localStorage.setItem(LOGO_KEY, $("#logo").value); } catch { /* bỏ qua */ }
  showLogoPreview();
});
$("#btnLogoAdd").addEventListener("click", () => { $("#logoFile").value = ""; $("#logoFile").click(); });
$("#logoFile").addEventListener("change", async (ev) => {
  const f = ev.target.files[0];
  if (!f) return;
  const name = prompt("Tên kênh dùng logo này (trùng tên kênh đã có thì thay logo cũ):", f.name.replace(/\.[^.]+$/, ""));
  if (name === null) return;
  const fd = new FormData();
  fd.append("name", name);
  fd.append("file", f);
  try {
    const r = await api("/api/logos", { method: "POST", body: fd });
    META.logos = r.logos;
    fillLogos(r.name);
    try { localStorage.setItem(LOGO_KEY, r.name); } catch { /* bỏ qua */ }
  } catch (e) { alert("Không thêm được logo: " + e.message); }
});

// ---------------- tạo mới ----------------
async function initMeta() {
  META = await api("/api/meta");
  $("#ver").textContent = "v" + META.version;
  $("#content_type").innerHTML = META.presets.map((p) => `<option value="${esc(p)}">${esc(PRESET_LABEL[p] || p)}</option>`).join("");
  if (META.presets.includes("film")) $("#content_type").value = "film";  // giữ mặc định cũ; chọn Anime khi làm ngách anime
  $("#agent").innerHTML = META.agents.map((a) => `<option value="${esc(a)}">${esc(a === "agy" ? "Antigravity (agy)" : a === "codex" ? "Codex" : a === "claude" ? "Claude Code" : a)}</option>`).join("");
  $("#agent").value = META.defaults.agent || META.agents[0];
  onAgentChange();
  fillLogos(localStorage.getItem("recap_logo"));
  const ttsOpts = META.tts_providers.map((p) => {
    const st = (META.tts_status || {})[p] || { ok: true };
    return `<option value="${esc(p)}" ${st.ok ? "" : "disabled"}>${esc(TTS_LABEL[p] || p)}${st.ok ? "" : " — " + esc(st.note)}</option>`;
  }).join("");
  $("#tts_provider").innerHTML = ttsOpts;
  $("#rTtsProvider").innerHTML = ttsOpts;
  const def = META.defaults.tts_provider || "piper";
  const firstOk = META.tts_providers.find((p) => (META.tts_status || {})[p]?.ok !== false) || "edge";
  $("#tts_provider").value = (META.tts_status || {})[def]?.ok === false ? firstOk : def;
  fillVoiceSelect($("#tts_voice"), $("#tts_provider").value, META.defaults.tts_voice || "");
  $("#tts_rate").value = META.defaults.tts_rate || 1;
  $("#burn_subtitles").checked = META.defaults.burn_subtitles !== false;
}
async function loadModelList(provider, datalist) {
  try {
    const r = await api(`/api/agent-models?provider=${encodeURIComponent(provider)}`);
    $(datalist).innerHTML = r.models.map((m) => `<option value="${esc(m)}">`).join("");
  } catch { $(datalist).innerHTML = ""; }
}
function onAgentChange() {
  const a = $("#agent").value;
  $("#agent_model").value = (META.defaults.agent_models || {})[a] || "";
  $("#agent_reasoning").value = (a === "claude" ? META.defaults.claude_effort : META.defaults.codex_reasoning) || "";
  $("#agent_service_tier").value = META.defaults.codex_service_tier || "";
  $("#reasonBox").classList.toggle("hidden", a !== "codex" && a !== "claude");
  $("#tierBox").classList.toggle("hidden", a !== "codex");
  loadModelList(a, "#agentModelList");
}
$("#agent").addEventListener("change", onAgentChange);
$$("input[name=srcType]").forEach((r) => r.addEventListener("change", () => {
  const file = $("input[name=srcType]:checked").value === "file";
  $("#srcUrlBox").classList.toggle("hidden", file);
  $("#srcFileBox").classList.toggle("hidden", !file);
}));
// ---- thanh trượt: tóm tắt giữ lại 10% → 100% độ dài video nguồn ----
let SRC_MIN = null;   // độ dài video nguồn (phút), null = chưa biết
function fmtMin(m) { const t = Math.round(m * 60); return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`; }
function setSourceMinutes(mins, label) {
  SRC_MIN = mins || null;
  $("#srcDurInfo").textContent = mins ? `Video nguồn dài ${fmtMin(mins)}${label ? " · " + label : ""}`
    : (label || "Chưa biết độ dài video — chọn file hoặc dán link để xem số phút tương ứng");
  updateSlider();
}
function updateSlider() {
  const p = Number($("#target_percent").value);
  const mins = SRC_MIN ? ` ≈ ${fmtMin(SRC_MIN * p / 100)} phút` : "";
  $("#sliderVal").textContent = `${p}% video gốc${mins}${p === 100 ? " (giữ nguyên độ dài)" : ""}`;
}
$("#target_percent").addEventListener("input", updateSlider);
$("#length").addEventListener("change", () => {
  $("#customBox").classList.toggle("hidden", $("#length").value !== "custom");
  if ($("#length").value === "custom" && SRC_MIN === null) setSourceMinutes(null);
});
// File trên máy: đọc độ dài ngay trong trình duyệt
function probeFile(f) {
  if (!f) return;
  const v = document.createElement("video");
  v.preload = "metadata";
  v.onloadedmetadata = () => { setSourceMinutes(v.duration / 60); URL.revokeObjectURL(v.src); };
  v.onerror = () => setSourceMinutes(null, "Không đọc được độ dài file (định dạng lạ) — vẫn chọn được tay");
  v.src = URL.createObjectURL(f);
}
// Link: hỏi tool (yt-dlp) độ dài video
let probeTimer = null, probeSeq = 0;
$("#url").addEventListener("input", () => {
  clearTimeout(probeTimer);
  const url = $("#url").value.trim();
  if (!/^https?:\/\//.test(url)) return;
  probeTimer = setTimeout(async () => {
    const my = ++probeSeq;
    setSourceMinutes(null, "Đang lấy độ dài video từ link…");
    try {
      const r = await api(`/api/probe?url=${encodeURIComponent(url)}`);
      if (my === probeSeq) setSourceMinutes(r.duration ? r.duration / 60 : null, r.title || "");
    } catch (e) { if (my === probeSeq) setSourceMinutes(null, "Không lấy được độ dài từ link — vẫn chọn được tay"); }
  }, 700);
});
const drop = $("#drop");
["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((e) => drop.addEventListener(e, () => drop.classList.remove("over")));
drop.addEventListener("drop", (ev) => { ev.preventDefault(); if (ev.dataTransfer.files[0]) { $("#file").files = ev.dataTransfer.files; showFileName(); probeFile(ev.dataTransfer.files[0]); } });
$("#file").addEventListener("change", () => { showFileName(); probeFile($("#file").files[0]); });
function showFileName() {
  const f = $("#file").files[0];
  $("#fileName").textContent = f ? `${f.name} — ${(f.size / 1048576).toFixed(1)} MB` : "";
}

$("#btnCreate").addEventListener("click", () => {
  const useFile = $("input[name=srcType]:checked").value === "file";
  const opts = {
    url: useFile ? "" : $("#url").value.trim(),
    content_type: $("#content_type").value,
    length: $("#length").value === "custom" ? "" : $("#length").value,
    target_minutes: null,
    target_percent: $("#length").value === "custom" ? Number($("#target_percent").value) : null,
    aspect: $("#aspect").value,
    narration_language: $("#narration_language").value,
    narration_style: $("#narration_style").value,
    mode: $("#mode").value,
    agent: $("#agent").value,
    agent_model: $("#agent_model").value.trim(),
    agent_reasoning: ["codex", "claude"].includes($("#agent").value) ? $("#agent_reasoning").value : "",
    agent_service_tier: $("#agent").value === "codex" ? $("#agent_service_tier").value : "",
    tts_provider: $("#tts_provider").value,
    tts_voice: $("#tts_voice").value.trim(),
    tts_rate: Number($("#tts_rate").value) || 1,
    burn_subtitles: $("#burn_subtitles").checked,
    use_bgm: $("#use_bgm").checked,
    logo: $("#logo").value,
    instructions: $("#instructions").value,
  };
  const file = useFile ? $("#file").files[0] : null;
  if (!opts.url && !file) { $("#createMsg").textContent = "Hãy dán link hoặc chọn file video."; return; }
  const fd = new FormData();
  fd.append("options", JSON.stringify(opts));
  if (file) fd.append("file", file);
  const btn = $("#btnCreate");
  btn.disabled = true;
  // Dùng XHR để hiện % khi tải file lớn lên
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/jobs");
  xhr.upload.onprogress = (e) => { if (e.lengthComputable && file) $("#createMsg").textContent = `Đang đưa file vào tool… ${Math.round(e.loaded / e.total * 100)}%`; };
  xhr.onload = () => {
    btn.disabled = false;
    let data = {}; try { data = JSON.parse(xhr.responseText); } catch {}
    if (xhr.status >= 200 && xhr.status < 300) {
      $("#createMsg").textContent = "";
      $("#url").value = ""; $("#file").value = ""; showFileName();
      location.hash = "job/" + data.id;
    } else $("#createMsg").textContent = "Lỗi: " + (data.detail || xhr.status);
  };
  xhr.onerror = () => { btn.disabled = false; $("#createMsg").textContent = "Không kết nối được tới tool."; };
  $("#createMsg").textContent = "Đang tạo…";
  xhr.send(fd);
});

// ---------------- danh sách ----------------
async function loadJobs() {
  const jobs = await api("/api/jobs");
  if (!jobs.length) { $("#jobList").innerHTML = `<div class="card muted">Chưa có video nào.</div>`; return; }
  $("#jobList").innerHTML = `<table><thead><tr><th>Tên</th><th>Trạng thái</th><th>Bước</th><th>Tạo lúc</th></tr></thead><tbody>${
    jobs.map((j) => `<tr class="clickable" data-id="${esc(j.id)}"><td>${esc(j.title)}</td>
      <td><span class="badge ${esc(j.status)}">${esc(STATUS[j.status] || j.status)}</span>${j.queued_position ? ` <span class="muted">#${j.queued_position}</span>` : ""}</td>
      <td class="muted">${esc(j.stage || "")}</td><td class="muted">${new Date((j.created || 0) * 1000).toLocaleString("vi-VN")}</td></tr>`).join("")
  }</tbody></table>`;
  $$("#jobList tr.clickable").forEach((tr) => tr.addEventListener("click", () => { location.hash = "job/" + tr.dataset.id; }));
}

// ---------------- chi tiết job ----------------
let JOB = null, pollTimer = null, scriptLoadedFor = null, outputLoadedFor = null, SCRIPT = null, dirty = false, SYNC = {};
async function loadSync() {
  try { const r = await api(`/api/jobs/${JOB.id}/sync`); SYNC = Object.fromEntries((r.rows || []).map((x) => [x.id, x])); }
  catch { SYNC = {}; }
}
function syncBadge(id, words, lang) {
  const r = SYNC[id];
  if (!r) return `${words} chữ · đọc ~${(words / (WPM[lang] || 160) * 60).toFixed(1)}s`;
  const sp = `${r.measured ? "" : "~"}${r.speech.toFixed(1)}s`;
  const base = `${words} chữ · giọng ${sp}${r.measured ? "" : " (ước tính)"} · clip ${r.clip.toFixed(1)}s`;
  const tag = { ok: `<span class="sync ok">✓ khớp</span>`, auto: `<span class="sync auto">⚙ tool tự căn trong cảnh</span>`,
    short: `<span class="sync bad">⚠ thiếu ${(-r.diff).toFixed(1)}s hình</span>`, long: `<span class="sync bad">⚠ thừa ${r.diff.toFixed(1)}s hình</span>` }[r.status];
  return `${base} · ${tag}`;
}
function stopPolling() { clearTimeout(pollTimer); pollTimer = null; }
$("#backToList").addEventListener("click", (e) => { e.preventDefault(); location.hash = "jobs"; });

async function openJob(id) {
  if (!JOB || JOB.id !== id) { scriptLoadedFor = null; outputLoadedFor = null; dirty = false; $("#log").textContent = ""; }
  stopPolling();
  await refreshJob(id);
}

async function refreshJob(id) {
  try {
    JOB = await api(`/api/jobs/${id}`);
  } catch (e) { $("#jTitle").textContent = e.message; return; }
  $("#jTitle").textContent = JOB.title;
  const st = $("#jStatus");
  st.className = "badge " + JOB.status; st.textContent = STATUS[JOB.status] || JOB.status;
  $("#jStage").textContent = JOB.stage || "";
  $("#jError").classList.toggle("hidden", !JOB.error); $("#jError").textContent = JOB.error || "";
  const w = JOB.warnings || [];
  $("#jWarnings").classList.toggle("hidden", !w.length); $("#jWarnings").innerHTML = w.map((x) => "⚠ " + esc(x)).join("<br>");
  const busy = ["queued", "running", "rendering"].includes(JOB.status);
  $("#btnCancel").disabled = !busy;
  $("#btnRerun").disabled = busy;
  $("#btnDelete").disabled = JOB.is_current;

  const log = await api(`/api/jobs/${id}/log`);
  const pre = $("#log");
  if (pre.textContent !== log.log) {
    pre.textContent = log.log;
    if ($("#autoScroll").checked) pre.scrollTop = pre.scrollHeight;
  }

  // Kịch bản: hiện khi có script và job không chạy
  const showScript = JOB.has_script && !busy;
  $("#scriptBox").classList.toggle("hidden", !showScript);
  if (showScript && scriptLoadedFor !== id) await loadScript(id);
  $$("#scriptBox button").forEach((b) => (b.disabled = busy));

  // Video thành phẩm
  $("#outputBox").classList.toggle("hidden", !JOB.has_output || busy);
  if (JOB.has_output && !busy && outputLoadedFor !== JOB.updated) {
    const t = Date.now();
    $("#outVideo").src = `/api/jobs/${id}/file?path=output/final.mp4&t=${t}`;
    $("#dlMp4").href = `/api/jobs/${id}/file?path=output/final.mp4&download=1`;
    $("#dlSrt").href = `/api/jobs/${id}/file?path=output/final.srt&download=1`;
    $("#dlVoiceSrt").href = `/api/jobs/${id}/file?path=output/final.voice.srt&download=1`;
    $("#dlVoiceSrt").classList.toggle("hidden", !JOB.has_voice_srt);
    $("#btnVoiceSrt").classList.toggle("hidden", !JOB.has_voice_srt);
    outputLoadedFor = JOB.updated;
  }

  $("#editBox").classList.toggle("hidden", !JOB.has_output);
  if (JOB.has_output) loadOutputs();
  $("#btnFfRun").disabled = busy;

  if (busy) pollTimer = setTimeout(() => refreshJob(id), 2000);
  else if (JOB.has_output) pollTimer = setTimeout(async () => { try { await loadOutputs(); } catch {} 
    if (location.hash === "#job/" + id) refreshJob(id); }, 6000);
  else if (scriptLoadedFor === id && (JOB.status === "review" || JOB.status === "done")) { /* đứng yên */ }
}

$("#btnCancel").addEventListener("click", async () => { await api(`/api/jobs/${JOB.id}/cancel`, { method: "POST" }); refreshJob(JOB.id); });
$("#btnDelete").addEventListener("click", async () => {
  if (!confirm("Xoá job này cùng toàn bộ file (video nguồn, kịch bản, video thành phẩm)?")) return;
  await api(`/api/jobs/${JOB.id}`, { method: "DELETE" }); location.hash = "jobs";
});
$("#btnRerun").addEventListener("click", async () => {
  const ins = prompt("Cho agent viết lại kịch bản từ đầu (giữ video nguồn và dữ liệu đã chuẩn bị).\nGhi yêu cầu thêm nếu có:", JOB.options?.instructions || "");
  if (ins === null) return;
  try {
    await api(`/api/jobs/${JOB.id}/rerun`, jsonOpts("POST", { instructions: ins, new_script: true }));
    scriptLoadedFor = null; refreshJob(JOB.id);
  } catch (e) { alert(e.message); }
});

// Nạp lại SRT voice đã sửa tay: mỗi dòng = cách đọc của một đoạn
$("#btnVoiceSrt").addEventListener("click", () => {
  if (dirty && !confirm("Kịch bản đang có chỗ sửa chưa lưu — nạp SRT voice sẽ bỏ các chỗ sửa đó. Tiếp tục?")) return;
  $("#voiceSrtFile").value = "";
  $("#voiceSrtFile").click();
});
$("#voiceSrtFile").addEventListener("change", async (ev) => {
  const f = ev.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  try {
    const r = await api(`/api/jobs/${JOB.id}/voice-srt`, { method: "POST", body: fd });
    if (!r.changed.length) alert("SRT voice này giống hệt cách đọc hiện tại — không có đoạn nào thay đổi.");
    else alert(`Đã cập nhật cách đọc cho ${r.changed.length}/${r.total} đoạn (đoạn ${r.changed.slice(0, 20).join(", ")}${r.changed.length > 20 ? "…" : ""}).\nBấm "Lưu & Dựng video" ở khung kịch bản để tạo giọng mới — chỉ các đoạn này phải tạo lại giọng.`
      + (r.errors.length ? "\n\nKịch bản đang có lỗi: " + r.errors.slice(0, 3).join("; ") : ""));
    scriptLoadedFor = null; dirty = false; refreshJob(JOB.id);
  } catch (e) { alert("Không nạp được SRT voice: " + e.message); }
});

$("#btnPolish").addEventListener("click", () => {
  $("#polishStyle").value = JOB.options?.narration_style || "story";
  $("#polishNote").value = "";
  $("#polishDlg").showModal();
});
$("#polishClose").addEventListener("click", () => $("#polishDlg").close());
$("#polishGo").addEventListener("click", async () => {
  try {
    await api(`/api/jobs/${JOB.id}/rerun`, jsonOpts("POST", {
      polish: true, narration_style: $("#polishStyle").value, polish_note: $("#polishNote").value,
    }));
    $("#polishDlg").close();
    scriptLoadedFor = null; refreshJob(JOB.id);
  } catch (e) { alert(e.message); }
});

// ---------------- tự chỉnh bằng ffmpeg ----------------
let FF_PRESETS = [], OUT_SIG = "";
async function loadPresets() {
  if (FF_PRESETS.length) return;
  FF_PRESETS = await api("/api/ffmpeg/presets");
  $("#ffPreset").innerHTML = `<option value="">— chọn mẫu —</option>` + FF_PRESETS.map((p, i) => `<option value="${i}">${esc(p.name)}</option>`).join("");
}
$("#ffPreset").addEventListener("change", () => { const p = FF_PRESETS[$("#ffPreset").value]; if (p) $("#ffArgs").value = p.args; });
async function loadOutputs() {
  await loadPresets();
  const items = await api(`/api/jobs/${JOB.id}/outputs`);
  const sig = JSON.stringify(items);
  if (sig === OUT_SIG) return;
  OUT_SIG = sig;
  const cur = $("#ffInput").value;
  const vids = items.filter((x) => /\.(mp4|mov|mkv)$/i.test(x.name));
  $("#ffInput").innerHTML = vids.map((x) => `<option ${x.name === cur ? "selected" : ""}>${esc(x.name)}</option>`).join("");
  const curLc = $("#lcFile").value;
  $("#lcFile").innerHTML = vids.map((x) => `<option ${x.name === curLc ? "selected" : ""}>${esc(x.name)}</option>`).join("");
  if (!curLc) $("#lcFile").value = "final.mp4";
  if (!cur) $("#ffInput").value = "final.mp4";
  $("#outList").innerHTML = items.length ? `<table><thead><tr><th>File trong thư mục output</th><th>Dung lượng</th><th></th></tr></thead><tbody>${
    items.map((x) => `<tr><td>${esc(x.name)}</td><td class="muted">${(x.size / 1048576).toFixed(1)} MB</td>
      <td>${/\.(mp4|mov|mkv)$/i.test(x.name) ? `<a href="#" data-play="${esc(x.name)}">▶ Xem</a> · ` : ""}<a href="/api/jobs/${JOB.id}/file?path=output/${encodeURIComponent(x.name)}&download=1">Tải</a></td></tr>`).join("")
  }</tbody></table>` : "";
  $$("#outList a[data-play]").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault(); $("#outVideo").src = `/api/jobs/${JOB.id}/file?path=output/${encodeURIComponent(a.dataset.play)}&t=${Date.now()}`;
    $("#outVideo").scrollIntoView({ behavior: "smooth", block: "center" }); $("#outVideo").play();
  }));
}
$("#btnFfRun").addEventListener("click", async () => {
  try {
    const r = await api(`/api/jobs/${JOB.id}/ffmpeg`, jsonOpts("POST", { args: $("#ffArgs").value, input: $("#ffInput").value || "final.mp4" }));
    $("#ffMsg").textContent = `Đang chạy… kết quả sẽ là ${r.out}`; OUT_SIG = ""; refreshJob(JOB.id);
  } catch (e) { $("#ffMsg").textContent = "Lỗi: " + e.message; }
});
$("#btnLC").addEventListener("click", async () => {
  try {
    await api(`/api/jobs/${JOB.id}/open-losslesscut`, jsonOpts("POST", { file: $("#lcFile").value || "final.mp4" }));
    $("#lcMsg").textContent = "Đã mở LosslessCut. File xuất ra sẽ tự hiện trong bảng bên dưới.";
  } catch (e) {
    if (/chưa có LosslessCut/i.test(e.message)) {
      if (confirm("Máy chưa có LosslessCut. Tải và cài bản chính thức (~230 MB) ngay bây giờ?")) {
        $("#lcMsg").textContent = "Đang tải và cài LosslessCut… (vài phút)";
        try { const r = await api("/api/install/losslesscut", { method: "POST" }); $("#lcMsg").textContent = r.output;
          if (r.ok) $("#btnLC").click(); } catch (e2) { $("#lcMsg").textContent = "Lỗi: " + e2.message; }
      }
    } else $("#lcMsg").textContent = "Lỗi: " + e.message;
  }
});
$("#btnOpenFolder").addEventListener("click", async () => { try { await api(`/api/jobs/${JOB.id}/open-folder`, { method: "POST" }); } catch (e) { alert(e.message); } });
$("#btnOpenTerm").addEventListener("click", async () => { try { await api(`/api/jobs/${JOB.id}/open-terminal`, { method: "POST" }); } catch (e) { alert(e.message); } });

// ---------------- trình sửa kịch bản ----------------
async function loadScript(id) {
  const data = await api(`/api/jobs/${id}/script`);
  SCRIPT = data.script; scriptLoadedFor = id; dirty = false;
  const o = JOB.options || {};
  const prov = o.tts_provider || META.defaults.tts_provider || "piper";
  const okProv = (META.tts_status || {})[prov]?.ok !== false;
  $("#rTtsProvider").value = okProv ? prov : (META.tts_providers.find((p) => (META.tts_status || {})[p]?.ok !== false) || "edge");
  fillVoiceSelect($("#rTtsVoice"), $("#rTtsProvider").value, okProv ? (o.tts_voice || "") : "");
  $("#rTtsRate").value = o.tts_rate || META.defaults.tts_rate || 1;
  $("#rAspect").value = o.aspect || "16:9";
  $("#rSubs").checked = o.burn_subtitles !== false;
  $("#rBgm").checked = o.use_bgm !== false;
  const cur = o.logo || "", names = META.logos || [];
  $("#rLogo").innerHTML = `<option value="">Logo mặc định (Cài đặt)</option>`
    + names.map((f) => `<option value="${esc(f)}">Kênh: ${esc(logoLabel(f))}</option>`).join("")
    + (cur && cur !== "none" && !names.includes(cur) ? `<option value="${esc(cur)}">⚠ ${esc(logoLabel(cur))} (đã mất file)</option>` : "")
    + `<option value="none">Không gắn logo</option>`;
  $("#rLogo").value = cur;
  $("#srcVideo").src = `/api/jobs/${id}/source`;
  await loadSync();
  renderScript(); showScriptErrors(data.errors, data.warnings);
}
const clipsToText = (clips) => (clips || []).map((c) => `${+(+c.start).toFixed(2)}-${+(+c.end).toFixed(2)}`).join("; ");
function textToClips(t) {
  return t.split(/[;\n]+/).map((x) => x.trim()).filter(Boolean).map((x) => {
    const m = x.match(/^(\d+(?:[.,]\d+)?)\s*[-–]\s*(\d+(?:[.,]\d+)?)$/);
    if (!m) throw new Error(`Clip không đúng dạng: "${x}" (cần dạng 12.5-17)`);
    return { start: parseFloat(m[1].replace(",", ".")), end: parseFloat(m[2].replace(",", ".")) };
  });
}
function wordsOf(s) { return (s.trim().match(/\S+/g) || []).length; }
function renderScript() {
  $("#scriptTitle").value = SCRIPT.title || "";
  const lang = SCRIPT.language || "vi";
  $("#segments").innerHTML = SCRIPT.segments.map((s, i) => {
    const words = wordsOf(s.narration || "");
    return `<div class="seg" data-i="${i}">
      <div class="seg-top"><b>Đoạn ${i + 1}</b>
        <span class="meta">${syncBadge(s.id, words, lang)}</span>
        <span class="seg-tools"><button data-act="up" title="Lên">↑</button><button data-act="down" title="Xuống">↓</button><button data-act="del" title="Xoá đoạn">✕</button></span></div>
      <textarea class="narr" rows="2">${esc(s.narration)}</textarea>
      <input class="speak" placeholder="Đọc là (không bắt buộc) — cách đọc cho giọng máy khi có từ tiếng Anh, vd: phe ra ri. SRT hiển thị vẫn giữ lời bình ở trên, ô này đi vào SRT voice" value="${esc(s.speak || "")}">
      ${SYNC[s.id] && SYNC[s.id].note ? `<div class="seen ${SYNC[s.id].note.matches === false ? "bad" : ""}">👁 AI thấy trên hình: ${esc(SYNC[s.id].note.on_screen)}</div>` : ""}
      <div class="seg-row">
        <label>Clip gốc <input class="clips" value="${esc(clipsToText(s.clips))}"></label>
        <label>Âm gốc (0–1) <input class="vol" type="number" min="0" max="1" step="0.05" value="${s.original_audio_volume || 0}"></label>
        <button data-act="play">▶ Xem clip</button>
        <button data-act="preview" title="Xem đúng như khi dựng: clip đã căn thời gian + giọng đọc">🔊 Xem + nghe</button>
      </div></div>`;
  }).join("");
  const words = SCRIPT.segments.reduce((a, s) => a + wordsOf(s.narration || ""), 0);
  $("#scriptStats").textContent = `· ${SCRIPT.segments.length} đoạn · ${words} chữ · ước tính ~${(words / (WPM[lang] || 160)).toFixed(1)} phút`;
  const tp = JOB && JOB.options && JOB.options.target_percent;
  $("#lengthReason").textContent = tp ? `Độ dài đã chọn: ${tp}% video gốc` : SCRIPT.target_minutes ? `AI chọn độ dài ~${SCRIPT.target_minutes} phút${SCRIPT.length_reason ? ": " + SCRIPT.length_reason : ""}` : "";
}
function collectScript() {
  const segs = $$("#segments .seg").map((el, i) => ({
    id: i + 1,
    narration: $(".narr", el).value.trim(),
    speak: $(".speak", el).value.trim() || null,
    clips: textToClips($(".clips", el).value),
    original_audio_volume: Math.max(0, Math.min(1, Number($(".vol", el).value) || 0)),
  }));
  return { ...SCRIPT, title: $("#scriptTitle").value, segments: segs };
}
$("#segments").addEventListener("input", () => { dirty = true; });
$("#segments").addEventListener("change", () => { try { SCRIPT = collectScript(); renderScript(); } catch (e) { showScriptErrors([e.message], []); } });
$("#segments").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button"); if (!btn) return;
  const seg = btn.closest(".seg"); const i = Number(seg.dataset.i);
  let cur; try { cur = collectScript(); } catch (e) { showScriptErrors([e.message], []); return; }
  SCRIPT = cur;
  const a = btn.dataset.act;
  if (a === "play") { playClips(SCRIPT.segments[i].clips); return; }
  if (a === "preview") { previewSegment(SCRIPT.segments[i]); return; }
  if (a === "up" && i > 0) [SCRIPT.segments[i - 1], SCRIPT.segments[i]] = [SCRIPT.segments[i], SCRIPT.segments[i - 1]];
  if (a === "down" && i < SCRIPT.segments.length - 1) [SCRIPT.segments[i + 1], SCRIPT.segments[i]] = [SCRIPT.segments[i], SCRIPT.segments[i + 1]];
  if (a === "del") { if (!confirm("Xoá đoạn này?")) return; SCRIPT.segments.splice(i, 1); }
  dirty = true; renderScript();
});
$("#btnAddSeg").addEventListener("click", () => {
  try { SCRIPT = collectScript(); } catch (e) { showScriptErrors([e.message], []); return; }
  const last = SCRIPT.segments[SCRIPT.segments.length - 1];
  const t = last ? last.clips[last.clips.length - 1].end : 0;
  SCRIPT.segments.push({ id: SCRIPT.segments.length + 1, narration: "Lời bình mới…", clips: [{ start: t, end: t + 4 }], original_audio_volume: 0 });
  dirty = true; renderScript();
});

const previewAudio = new Audio();
function previewSegment(seg) {
  const r = SYNC[seg.id];
  if (!r || !r.audio) { alert("Đoạn này chưa có giọng đọc. Bấm Lưu & Dựng video một lần (hoặc để agent chạy sync) rồi thử lại."); return; }
  const v = $("#srcVideo");
  const clips = (r.fitted || seg.clips.map((c) => [c.start, c.end])).map(([s, e]) => ({ start: s, end: e }));
  v.muted = true; v.playbackRate = r.speed ? 1 / r.speed : 1;
  previewAudio.src = `/api/jobs/${JOB.id}/file?path=${encodeURIComponent(r.audio)}`;
  previewAudio.currentTime = 0; previewAudio.play();
  playClips(clips, () => { v.muted = false; v.playbackRate = 1; });
  $("#previewInfo").textContent = `Đang xem + nghe đoạn ${seg.id} (đúng như khi dựng)`;
}
let playQueue = [], playTimer = null;
function playClips(clips, onDone) {
  const v = $("#srcVideo"); playQueue = [...clips]; clearInterval(playTimer);
  if (!onDone) { previewAudio.pause(); v.muted = false; v.playbackRate = 1; }
  const next = () => {
    const c = playQueue.shift();
    if (!c) { v.pause(); $("#previewInfo").textContent = ""; clearInterval(playTimer); if (onDone) onDone(); return; }
    $("#previewInfo").textContent = `Đang xem ${c.start}s → ${c.end}s`;
    v.currentTime = c.start; v.play();
    clearInterval(playTimer);
    playTimer = setInterval(() => { if (v.currentTime >= c.end || v.paused) { clearInterval(playTimer); if (!v.paused) next(); } }, 100);
  };
  next();
}

function showScriptErrors(errors, warnings) {
  $("#scriptErrors").innerHTML =
    (errors || []).map((e) => `<div class="error">✕ ${esc(e)}</div>`).join("") +
    (warnings || []).map((w) => `<div class="warn">⚠ ${esc(w)}</div>`).join("");
}
async function saveScript() {
  let data; try { data = collectScript(); } catch (e) { showScriptErrors([e.message], []); return null; }
  const r = await api(`/api/jobs/${JOB.id}/script`, jsonOpts("PUT", data));
  SCRIPT = data; dirty = false; await loadSync(); renderScript(); showScriptErrors(r.errors, r.warnings);
  return r;
}
$("#btnSave").addEventListener("click", async () => { try { await saveScript(); } catch (e) { showScriptErrors([e.message], []); } });
$("#btnRender").addEventListener("click", async () => {
  try {
    const r = await saveScript(); if (!r || r.errors.length) return;
    const bad = Object.values(SYNC).filter((x) => x.status === "short" || x.status === "long");
    if (bad.length && !confirm(`${bad.length} đoạn lời bình chưa khớp với độ dài clip (đánh dấu ⚠). `
      + `Dựng luôn thì những đoạn thiếu hình sẽ bị đứng khung cuối. Vẫn dựng?`)) return;
    await api(`/api/jobs/${JOB.id}/render`, jsonOpts("POST", {
      tts_provider: $("#rTtsProvider").value, tts_voice: $("#rTtsVoice").value.trim(),
      tts_rate: Number($("#rTtsRate").value) || 1, aspect: $("#rAspect").value,
      burn_subtitles: $("#rSubs").checked, use_bgm: $("#rBgm").checked, logo: $("#rLogo").value,
    }));
    refreshJob(JOB.id);
  } catch (e) { showScriptErrors([e.message], []); }
});
window.addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });

// ---------------- cài đặt ----------------
const FIELDS = [
  ["Agent", [
    ["agent.provider", "Agent mặc định", "select", ["codex", "agy"]],
    ["agent.timeout_minutes", "Giới hạn thời gian mỗi lần chạy agent (phút, 0 = không giới hạn)", "number"],
    ["agent.max_retries", "Số lần cho agent làm lại khi kịch bản lỗi", "number"],
    ["agent.models.codex", "Model mặc định của Codex (trống = mặc định của Codex; gõ đúng tên model, vd gpt-5.5)", "text"],
    ["agent.codex_reasoning", "Mức suy luận mặc định của Codex", "select", ["", "low", "medium", "high", "xhigh"]],
    ["agent.codex_service_tier", "Chế độ tốc độ mặc định của Codex (fast = Fast mode)", "select", ["", "fast"]],
    ["agent.models.claude", "Model mặc định của Claude Code (trống = mặc định; vd opus, sonnet)", "text"],
    ["agent.claude_effort", "Mức suy luận mặc định của Claude Code", "select", ["", "low", "medium", "high", "xhigh", "max"]],
    ["agent.models.agy", "Model mặc định của Antigravity (trống = mặc định; xem danh sách bằng lệnh agy models)", "text"],
    ["agent.commands", "Lệnh gọi agent (JSON — {prompt} là chỗ chèn yêu cầu)", "json"],
  ]],
  ["Nhận dạng lời thoại", [
    ["transcribe.engine", "Nhận dạng bằng (local = tại máy; groq / openai = gửi âm thanh lên API, rất nhanh)", "select", ["local", "groq", "openai"]],
    ["transcribe.use_source_subtitles", "Dùng phụ đề có sẵn của video nếu có (bỏ qua nhận dạng)", "bool"],
    ["transcribe.api_keys.groq", "Groq API key", "password"],
    ["transcribe.api_keys.openai", "OpenAI API key (nhận dạng)", "password"],
    ["scenes.max_scene_seconds", "Chia nhỏ cảnh dài hơn (giây, 0 = không chia)", "number"],
    ["transcribe.model", "Model Whisper", "select", ["large-v3-turbo", "large-v3", "medium", "small", "base"]],
    ["transcribe.cpu_model", "Model dùng khi máy KHÔNG có GPU (trống = như trên; small = nhanh hơn ~4 lần, kém chính xác hơn)", "select", ["", "small", "base", "medium"]],
    ["transcribe.device", "Thiết bị", "select", ["auto", "cuda", "cpu"]],
    ["transcribe.speed", "Tốc độ nhận dạng (fast = nhanh, accurate = kỹ hơn)", "select", ["fast", "accurate"]],
    ["transcribe.batch_size", "Batch (0 = tự chọn; giảm xuống 8 nếu GPU báo hết bộ nhớ)", "number"],
    ["transcribe.language", "Ngôn ngữ nguồn (trống = tự nhận)", "text"],
  ]],
  ["Giọng đọc", [
    ["tts.provider", "Nhà cung cấp mặc định", "select", ["omnivoice", "vieneu", "piper", "local_command", "edge", "openai", "elevenlabs"]],
    ["tts.providers.omnivoice.voice", "OmniVoice: giọng mặc định (tên giọng clone, vd Hải Đăng)", "text"],
    ["tts.providers.omnivoice.num_step", "OmniVoice: số bước sinh (32 = chuẩn; 16 = nhanh gấp ~2, nghe thử trước)", "number"],
    ["tts.providers.omnivoice.group", "OmniVoice: số đoạn gửi mỗi lượt (24; GPU tự chia lô theo VRAM)", "number"],
    ["tts.providers.omnivoice.url", "OmniVoice: địa chỉ dịch vụ", "text"],
    ["tts.providers.vieneu.voice", "VieNeu: giọng mặc định (vd Hải Đăng)", "text"],
    ["tts.providers.vieneu.workers", "VieNeu: số tiến trình chạy song song (máy nhiều nhân: 2–3; mỗi tiến trình tốn thêm ~2 GB RAM)", "number"],
    ["tts.providers.vieneu.mode", "VieNeu: model (v3turbo = hay nhất; v3nano = nhẹ, cho máy CPU yếu)", "select", ["v3turbo", "v3nano"]],
    ["tts.voice", "Giọng mặc định", "text"],
    ["tts.rate", "Tốc độ đọc", "number"],
    ["tts.providers.piper.model", "Piper: file model .onnx", "text"],
    ["tts.providers.local_command.command", "Lệnh TTS tại máy (JSON — dùng {text_file}, {out_file}, {voice}, {python})", "json"],
    ["tts.providers.edge.voice", "Edge: giọng (vd vi-VN-HoaiMyNeural, vi-VN-NamMinhNeural)", "text"],
    ["tts.providers.openai.api_key", "OpenAI API key", "password"],
    ["tts.providers.openai.voice", "OpenAI: giọng", "text"],
    ["tts.providers.elevenlabs.api_key", "ElevenLabs API key", "password"],
    ["tts.providers.elevenlabs.voice", "ElevenLabs: giọng mặc định (tên, vd Bao Ngoc, hoặc voice ID)", "text"],
  ]],
  ["Khung hình cho agent xem", [
    ["frames.sheet_interval_seconds", "Bảng ảnh: cố định bao nhiêu giây một khung (0 = tự tính; 0.5 = 2 khung/giây)", "number"],
    ["frames.sheet_min_interval", "Bảng ảnh khi tự tính: dày nhất bao nhiêu giây một khung", "number"],
    ["frames.sheet_max_cells", "Bảng ảnh khi tự tính: tối đa bao nhiêu khung cho cả video (video dài sẽ thưa hơn)", "number"],
    ["frames.sheet_cols", "Bảng ảnh: số cột", "number"],
    ["frames.sheet_rows", "Bảng ảnh: số hàng", "number"],
    ["frames.check_step_seconds", "Ảnh đối chiếu từng đoạn: bao nhiêu giây một khung", "number"],
    ["frames.check_max_frames", "Ảnh đối chiếu: tối đa số khung mỗi đoạn", "number"],
    ["frames.check_cell_width", "Ảnh đối chiếu: bề ngang mỗi khung (px; nhỏ hơn = ít token hơn)", "number"],
    ["frames.overview", "Bảng tổng quan thưa cho agent xem hết, bảng chi tiết chỉ mở khi cần (tiết kiệm token)", "bool"],
    ["frames.overview_max_cells", "Bảng tổng quan: tối đa bao nhiêu khung cho cả video", "number"],
    ["frames.overview_min_interval", "Bảng tổng quan: dày nhất bao nhiêu giây một khung", "number"],
    ["frames.overview_cols", "Bảng tổng quan: số cột", "number"],
    ["frames.overview_rows", "Bảng tổng quan: số hàng", "number"],
    ["frames.overview_cell_width", "Bảng tổng quan: bề ngang mỗi khung (px)", "number"],
  ]],
  ["Dựng video", [
    ["render.resolution", "Độ phân giải (cạnh ngắn)", "select", [1080, 720]],
    ["render.encoder", "Bộ mã hoá", "select", ["auto", "h264_nvenc", "libx264"]],
    ["render.parallel", "Số đoạn dựng cùng lúc (0 = tự chọn: GPU 4, CPU 6)", "number"],
    ["render.burn_subtitles", "Gắn phụ đề mặc định", "bool"],
    ["render.subtitle_font", "Font phụ đề (tên font; tool tìm trong assets/fonts và font đã cài trên máy, không có thì dùng font dự phòng Montserrat)", "text"],
    ["render.subtitle_effect", "Kiểu phụ đề (caps = IN HOA tô vàng từng từ; italic = nghiêng tô vàng từng từ; auto = mỗi video một kiểu; plain = kiểu cũ)", "select", ["auto", "caps", "italic", "plain"]],
    ["render.bgm_folder", "Thư mục nhạc nền (đường dẫn trên máy)", "text"],
    ["render.bgm_volume", "Âm lượng nhạc nền (0–1)", "number"],
    ["render.logo_file", "Logo góc trái trên (đường dẫn file PNG; trống = không gắn)", "text"],
    ["render.logo_opacity", "Độ đậm logo (0–1; 0.7 = mờ 30%)", "number"],
    ["render.logo_width", "Chiều rộng logo (tỉ lệ khung ngang; 0.1875 = 360px ở 1080p)", "number"],
    ["render.max_clip_seconds", "Độ dài tối đa mỗi clip gốc (giây)", "number"],
    ["editors.losslesscut_path", "Đường dẫn LosslessCut.exe (trống = tool tự tìm)", "text"],
    ["script.one_sentence_per_segment", "Khớp chặt: mỗi đoạn lời bình đúng 1 câu", "bool"],
    ["script.visual_check", "Agent tự soát ảnh đối chiếu lời-hình (tốn thêm hạn mức)", "bool"],
    ["script.auto_min_minutes", "AI tự quyết độ dài: tối thiểu (phút)", "number"],
    ["script.auto_max_minutes", "AI tự quyết độ dài: tối đa (phút, 0 = không giới hạn, tới hết video gốc)", "number"],
    ["render.gap_seconds", "Khoảng nghỉ giữa hai câu lời bình (giây, 0.15–0.3 nghe tự nhiên)", "number"],
    ["render.max_speed_change", "Đổi tốc độ hình tối đa (0.06 = ±6%; càng nhỏ càng ít khựng)", "number"],
    ["render.interpolate_fps", "Nội suy thêm khung hình: dựng video gốc 24/30 fps thành bao nhiêu fps (0 = tắt; 60 = mượt gấp đôi, dựng chậm hơn nhiều)", "select", [0, 60, 48]],
    ["render.interpolate_mode", "Kiểu nội suy (high = bù chuyển động, đẹp nhưng chậm; blend = trộn khung, nhanh)", "select", ["high", "blend"]],
    ["render.quality", "Mức nén (high = nén kỹ, chậm hơn, hình mịn hơn; fast = nhanh)", "select", ["high", "fast"]],
    ["render.crf", "Chất lượng hình (CRF: 16–18 = rất đẹp, 20 = khá, càng nhỏ file càng nặng)", "number"],
    ["download.max_height", "Tải video nguồn tối đa (độ cao, px)", "select", [1080, 1440, 2160, 720]],
    ["render.fps", "Số khung hình/giây (auto = theo video gốc, gốc 60 fps thì dựng 60 fps cho mượt)", "select", ["auto", 60, 30, 25, 24]],
    ["render.narration_lead", "Lời đọc vào sau mép cắt hình (giây; trống = bằng khoảng nghỉ giữa câu)", "number"],
    ["render.smooth_speed", "Khi đổi tốc độ hình: trộn khung cho mượt (thay vì lặp/bỏ khung)", "bool"],
    ["tts.use_pronunciation", "Đọc từ tiếng Anh / viết tắt theo từ điển phát âm bên dưới", "bool"],
    ["tts.pronunciation", "Từ điển phát âm (mỗi dòng: từ = cách đọc, vd Ferrari = phe ra ri). Viết tắt viết HOA thì chỉ thay khi viết hoa đúng như vậy. Đã có sẵn ~50 từ phổ biến (YouTube, AI, OK, laptop, km…); dòng ở đây ghi đè từ có sẵn", "dict"],
    ["tts.trim_silence", "Cắt khoảng lặng thừa đầu/cuối mỗi câu giọng đọc", "bool"],
    ["script.extend_across_scenes", "Khi clip ngắn hơn lời: cho kéo dài sang cảnh kế bên (thay vì làm chậm / đứng hình)", "bool"],
  ]],
  ["Tải video từ link", [
    ["download.cookies_from_browser", "Lấy cookie từ trình duyệt (chrome / edge / firefox — trống = không dùng)", "text"],
    ["download.cookies_file", "Hoặc file cookies.txt", "text"],
  ]],
  ["Lưu trữ", [
    ["retention.enabled", "Tự xoá job cũ", "bool"],
    ["retention.days", "Xoá sau bao nhiêu ngày", "number"],
    ["limits.max_source_hours", "Giới hạn độ dài video nguồn (giờ)", "number"],
  ]],
];
let SETTINGS = null;
const getPath = (o, p) => p.split(".").reduce((a, k) => (a == null ? undefined : a[k]), o);
function setPath(o, p, v) { const ks = p.split("."); let cur = o; ks.slice(0, -1).forEach((k) => { if (typeof cur[k] !== "object" || cur[k] === null) cur[k] = {}; cur = cur[k]; }); cur[ks[ks.length - 1]] = v; }
async function loadSettings() {
  SETTINGS = await api("/api/settings");
  $("#settingsForm").innerHTML = FIELDS.map(([group, fields]) => `<div class="card settings-group"><h3>${esc(group)}</h3><div class="grid">${
    fields.map(([path, label, type, opts0]) => {
      let opts = opts0;
      const v = getPath(SETTINGS, path);
      let input;
      if (path === "agent.provider") opts = Object.keys(SETTINGS.agent?.commands || { codex: 1, agy: 1 });
      if (type === "select") input = `<select data-path="${path}" data-type="${typeof opts[0] === "number" ? "number" : "text"}">${opts.map((o) => `<option ${String(o) === String(v) ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
      else if (type === "bool") return `<label class="check"><input type="checkbox" data-path="${path}" data-type="bool" ${v ? "checked" : ""}> ${esc(label)}</label>`;
      else if (type === "dict") return `<label class="full">${esc(label)}<textarea rows="5" data-path="${path}" data-type="dict" spellcheck="false" placeholder="Ferrari = phe ra ri&#10;Shopee = sóp pi">${esc(Object.entries(v || {}).map(([a, b]) => `${a} = ${b}`).join("\n"))}</textarea></label>`;
      else if (type === "json") return `<label class="full">${esc(label)}<textarea rows="4" data-path="${path}" data-type="json" spellcheck="false">${esc(JSON.stringify(v, null, 1))}</textarea></label>`;
      else input = `<input data-path="${path}" data-type="${type === "number" ? "number" : "text"}" type="${type === "password" ? "password" : type === "number" ? "number" : "text"}" step="any" value="${esc(v ?? "")}">`;
      return `<label>${esc(label)}${input}</label>`;
    }).join("")}</div></div>`).join("");
  $("#settingsRaw").value = JSON.stringify(SETTINGS, null, 2);
  initVoiceTry();
}
$("#btnSaveSettings").addEventListener("click", async () => {
  try {
    const s = JSON.parse(JSON.stringify(SETTINGS));
    $$("#settingsForm [data-path]").forEach((el) => {
      const t = el.dataset.type; let v;
      if (t === "bool") v = el.checked;
      else if (t === "number") v = el.value === "" ? null : Number(el.value);
      else if (t === "dict") {
        v = {};
        el.value.split(/\r?\n/).forEach((line) => {
          const m = line.match(/^\s*(.+?)\s*[=:→]\s*(.+?)\s*$/);
          if (m) v[m[1]] = m[2];
        });
      }
      else if (t === "json") { try { v = JSON.parse(el.value); } catch { throw new Error("JSON không hợp lệ ở: " + el.dataset.path); } }
      else v = el.value;
      setPath(s, el.dataset.path, v);
    });
    await api("/api/settings", jsonOpts("PUT", s));
    $("#settingsMsg").textContent = "Đã lưu."; await loadSettings(); await initMeta();
  } catch (e) { $("#settingsMsg").textContent = "Lỗi: " + e.message; }
});
$("#btnSaveRaw").addEventListener("click", async () => {
  try { await api("/api/settings", jsonOpts("PUT", JSON.parse($("#settingsRaw").value))); $("#settingsMsg").textContent = "Đã lưu JSON."; await loadSettings(); await initMeta(); }
  catch (e) { $("#settingsMsg").textContent = "Lỗi: " + e.message; }
});

// ---------------- kiểm tra máy ----------------
async function loadHealth() {
  $("#healthList").innerHTML = `<span class="muted">Đang kiểm tra…</span>`;
  const items = await api("/api/health");
  $("#healthList").innerHTML = items.map((it) => `<div class="health-item">
    <span class="${it.ok ? "ok" : it.ok === false ? "bad" : "na"}">${it.ok ? "✓" : it.ok === false ? "✕" : "–"}</span>
    <b>${esc(it.name)}</b><span>${esc(it.detail)}${it.ok === false && it.fix ? `<br><span class="hint">→ ${esc(it.fix)}</span>` : ""}</span></div>`).join("");
}
$("#btnHealth").addEventListener("click", loadHealth);
async function runUpdate(path, btn) {
  btn.disabled = true; const out = $("#updateOut"); out.classList.remove("hidden"); out.textContent = "Đang cập nhật…";
  try { const r = await api(path, { method: "POST" }); out.textContent = (r.ok ? "✓ Xong\n\n" : "✕ Lỗi\n\n") + r.output; }
  catch (e) { out.textContent = "Lỗi: " + e.message; }
  btn.disabled = false; loadHealth();
}
$("#btnUpdYtdlp").addEventListener("click", (e) => runUpdate("/api/update/ytdlp", e.target));
$("#btnUpdTool").addEventListener("click", (e) => runUpdate("/api/update/tool", e.target));
$("#btnInstLC").addEventListener("click", (e) => runUpdate("/api/install/losslesscut", e.target));
$("#btnInstVieneu").addEventListener("click", async (e) => { await runUpdate("/api/install/vieneu", e.target); await initMeta(); });
// ---------------- danh sách giọng ----------------
// Giọng có sẵn của VieNeu-TTS v3 Turbo: [tên, giới tính, vùng, kiểu đọc]
const VIENEU_KNOWN = [
  ["Anh Khôi", "nam", "Bắc", "kể chuyện"], ["Thanh Bình", "nam", "Bắc", "kể chuyện"],
  ["Thái Sơn", "nam", "Nam", "kể chuyện"], ["Đức Trí", "nam", "Nam", "kể chuyện"],
  ["Ngọc Linh", "nữ", "Bắc", "kể chuyện"], ["Quỳnh Anh", "nữ", "Bắc", "kể chuyện"],
  ["Kim Thanh", "nữ", "Nam", "kể chuyện"], ["Mỹ Duyên", "nữ", "Nam", "kể chuyện"], ["Thục Đoan", "nữ", "Nam", "kể chuyện"],
  ["Minh Quân", "nam", "Bắc", "tự nhiên"], ["Mạnh Dũng", "nam", "Bắc", "tự nhiên"], ["Phạm Tuyên", "nam", "Bắc", "tự nhiên"],
  ["Xuân Vĩnh", "nam", "Bắc", "tự nhiên"], ["Ngọc Huyền", "nữ", "Bắc", "tự nhiên"], ["Trúc Ly", "nữ", "Bắc", "tự nhiên"],
  ["Đoan Trang", "nữ", "Bắc", "tự nhiên"], ["Quang Sơn", "nam", "Trung", "tự nhiên"], ["Ngọc Trân", "nữ", "Trung", "tự nhiên"],
  ["Adam", "nam", "Nam", "tự nhiên"],
  ["Minh Đức", "nam", "Bắc", "đọc tin"], ["Mai Anh", "nữ", "Bắc", "đọc tin"], ["Minh Triết", "nam", "Nam", "đọc tin"],
  ["Thùy Dung", "nữ", "Nam", "đọc tin"],
];
const VN_META = Object.fromEntries(VIENEU_KNOWN.map(([n, g, r, k]) => [n, { g, r, k }]));
const VOICE_LISTS = {
  edge: [["vi-VN-NamMinhNeural", "nam"], ["vi-VN-HoaiMyNeural", "nữ"]],
  openai: [["onyx", "nam, trầm"], ["echo", "nam"], ["ash", "nam"], ["fable", "kể chuyện"], ["alloy", "trung tính"],
           ["nova", "nữ"], ["shimmer", "nữ"], ["coral", "nữ"], ["sage", "nữ"]],
};
let OMNI_VOICES = null;    // giọng clone đang có trong dịch vụ OmniVoice
let OMNI_LOADING = null;
function loadOmniVoices() {
  if (OMNI_LOADING) return OMNI_LOADING;
  OMNI_LOADING = api("/api/omnivoice/voices").then((r) => { OMNI_VOICES = r.voices || []; })
    .catch(() => { OMNI_VOICES = []; }).finally(() => refreshVoiceSelects());
  return OMNI_LOADING;
}
let EL_VOICES = null;      // giọng trong tài khoản ElevenLabs (lấy qua API key trong Cài đặt)
let EL_LOADING = null;
function loadElevenVoices() {
  if (EL_LOADING) return EL_LOADING;
  EL_LOADING = api("/api/elevenlabs/voices").then((r) => { EL_VOICES = r.voices || []; })
    .catch(() => { EL_VOICES = []; }).finally(() => refreshVoiceSelects());
  return EL_LOADING;
}
let VN_INSTALLED = null;   // danh sách giọng VieNeu thật trên máy (lấy từ tool, có thể mất vài chục giây lần đầu)
let VN_LOADING = null;
let VN_DONE = false;
function loadVieneuVoices() {
  if (VN_LOADING) return VN_LOADING;
  VN_LOADING = api("/api/vieneu/voices").then((r) => { VN_INSTALLED = (r.voices || []).length ? r.voices : null; })
    .catch(() => {}).finally(() => { VN_DONE = true; refreshVoiceSelects(); });
  return VN_LOADING;
}
function voiceOptionsHtml(provider, current) {
  let html = `<option value="">(giọng mặc định trong Cài đặt)</option>`;
  const seen = new Set([""]);
  const opt = (v, label) => { seen.add(v); return `<option value="${esc(v)}">${esc(label)}</option>`; };
  if (provider === "omnivoice") {
    if (OMNI_VOICES === null) html += `<optgroup label="(đang hỏi dịch vụ OmniVoice…)"></optgroup>`;
    else if (!OMNI_VOICES.length) html += `<optgroup label="(dịch vụ OmniVoice chưa chạy — bật START-OMNIVOICE.bat)"></optgroup>`;
    else html += `<optgroup label="Giọng clone trên máy">` + OMNI_VOICES.map((n) => opt(n, n)).join("") + `</optgroup>`;
  } else if (provider === "elevenlabs") {
    if (EL_VOICES === null) html += `<optgroup label="(đang lấy giọng ElevenLabs…)"></optgroup>`;
    else if (!EL_VOICES.length) html += `<optgroup label="(chưa có API key ElevenLabs trong Cài đặt)"></optgroup>`;
    else {
      const g = { "Tiếng Việt": [], "Giọng của tôi (clone / tự tạo)": [], "Thư viện ElevenLabs": [] };
      EL_VOICES.forEach((v) => (v.lang === "vi" ? g["Tiếng Việt"] : ["cloned", "generated"].includes(v.category)
        ? g["Giọng của tôi (clone / tự tạo)"] : g["Thư viện ElevenLabs"]).push(v));
      Object.entries(g).forEach(([k, arr]) => {
        if (arr.length) html += `<optgroup label="${esc(k)}">` + arr.map((v) => opt(v.id, `${v.name}${v.gender ? " — " + v.gender : ""}`)).join("") + `</optgroup>`;
      });
    }
  } else if (provider === "vieneu") {
    const names = VN_INSTALLED || VIENEU_KNOWN.map((x) => x[0]);
    const groups = { "kể chuyện": [], "tự nhiên": [], "đọc tin": [], "khác": [] };
    names.forEach((n) => { const m = VN_META[n]; (groups[m ? m.k : "khác"] || groups["khác"]).push(n); });
    const title = { "kể chuyện": "Kể chuyện — hợp video recap/review", "tự nhiên": "Tự nhiên", "đọc tin": "Đọc tin (trang trọng)", "khác": "Giọng khác có trên máy" };
    Object.entries(groups).forEach(([k, arr]) => {
      if (!arr.length) return;
      html += `<optgroup label="${esc(title[k])}">` + arr.map((n) => {
        const m = VN_META[n]; return opt(n, m ? `${n} — ${m.g}, giọng ${m.r}` : n);
      }).join("") + `</optgroup>`;
    });
    if (!VN_INSTALLED && !VN_DONE) html += `<optgroup label="(đang lấy danh sách giọng thật trên máy…)"></optgroup>`;
  } else {
    (VOICE_LISTS[provider] || []).forEach(([v, d]) => { html += opt(v, d ? `${v} — ${d}` : v); });
  }
  if (current && !seen.has(current)) html += opt(current, current);
  html += `<option value="__custom__">Khác (tự gõ tên / mã giọng)…</option>`;
  return html;
}
function fillVoiceSelect(sel, provider, current) {
  sel.dataset.provider = provider;
  sel.innerHTML = voiceOptionsHtml(provider, current || "");
  sel.value = current || "";
  sel.dataset.prev = sel.value;
  if (provider === "vieneu" && !VN_DONE) loadVieneuVoices();
  if (provider === "omnivoice" && OMNI_VOICES === null) loadOmniVoices();
  if (provider === "elevenlabs" && EL_VOICES === null) loadElevenVoices();
}
function refreshVoiceSelects() {
  ["#tts_voice", "#rTtsVoice", "#pvVoice"].forEach((id) => {
    const sel = $(id); if (!sel || !sel.dataset.provider) return;
    fillVoiceSelect(sel, sel.dataset.provider, sel.value === "__custom__" ? "" : sel.value);
  });
}
function onVoiceSelectChange(sel) {
  if (sel.value !== "__custom__") { sel.dataset.prev = sel.value; return true; }
  const v = (prompt("Nhập tên hoặc mã giọng:", "") || "").trim();
  if (!v) { sel.value = sel.dataset.prev || ""; return false; }
  fillVoiceSelect(sel, sel.dataset.provider, v);
  return true;
}
$("#tts_provider").addEventListener("change", () => fillVoiceSelect($("#tts_voice"), $("#tts_provider").value, ""));
$("#rTtsProvider").addEventListener("change", () => fillVoiceSelect($("#rTtsVoice"), $("#rTtsProvider").value, ""));
$("#tts_voice").addEventListener("change", (e) => onVoiceSelectChange(e.target));
$("#rTtsVoice").addEventListener("change", (e) => onVoiceSelectChange(e.target));

// ---------------- nghe thử giọng ----------------
const PV_TEXT = "Các bạn có tin được không, cậu thiếu niên yếu ớt nhất làng lại chính là người duy nhất đứng dậy khi con quái vật xuất hiện. Và đây mới chỉ là khởi đầu.";
async function tryVoice(provider, voice, rate, text, audioEl, msgEl, btn) {
  if (btn) btn.disabled = true;
  if (msgEl) msgEl.textContent = `Đang tạo giọng ${voice || "mặc định"}…`;
  try {
    const r = await api("/api/tts/preview", jsonOpts("POST", { provider, voice, rate, text }));
    audioEl.src = r.url + "?t=" + Date.now();
    await audioEl.play().catch(() => {});
    if (msgEl) msgEl.textContent = `Đang phát: ${voice || "giọng mặc định"} (${provider})`;
  } catch (e) {
    if (msgEl) msgEl.textContent = "Lỗi: " + e.message; else alert(e.message);
  } finally { if (btn) btn.disabled = false; }
}
function initVoiceTry() {
  $("#pvProvider").innerHTML = $("#tts_provider").innerHTML;
  const t = (SETTINGS && SETTINGS.tts) || {};
  const prov = t.provider || $("#tts_provider").value;
  $("#pvProvider").value = prov;
  if ($("#pvProvider").value !== prov) $("#pvProvider").value = $("#tts_provider").value;
  fillVoiceSelect($("#pvVoice"), $("#pvProvider").value, t.voice || ((t.providers || {})[prov] || {}).voice || "");
  if (t.rate) $("#pvRate").value = t.rate;
  if (!$("#pvText").value) $("#pvText").value = PV_TEXT;
}
$("#pvProvider").addEventListener("change", () => fillVoiceSelect($("#pvVoice"), $("#pvProvider").value, ""));
$("#pvVoice").addEventListener("change", (e) => { if (onVoiceSelectChange(e.target)) $("#pvPlay").click(); });
$("#pvPlay").addEventListener("click", (e) => tryVoice($("#pvProvider").value, $("#pvVoice").value.trim(),
  Number($("#pvRate").value) || 1, $("#pvText").value.trim(), $("#pvAudio"), $("#pvMsg"), e.target));
$("#pvSetDefault").addEventListener("click", async () => {
  try {
    const s = await api("/api/settings");
    const prov = $("#pvProvider").value, voice = $("#pvVoice").value.trim();
    s.tts = s.tts || {}; s.tts.provider = prov; s.tts.voice = voice; s.tts.rate = Number($("#pvRate").value) || 1;
    s.tts.providers = s.tts.providers || {}; s.tts.providers[prov] = s.tts.providers[prov] || {};
    if (voice) s.tts.providers[prov].voice = voice;
    await api("/api/settings", jsonOpts("PUT", s));
    await loadSettings(); await initMeta();
    $("#pvMsg").textContent = `Đã đặt mặc định: ${voice || "giọng mặc định"} (${prov}), tốc độ ${s.tts.rate}. Job mới sẽ dùng giọng này.`;
  } catch (e) { $("#pvMsg").textContent = "Lỗi: " + e.message; }
});
$("#btnVoiceTry").addEventListener("click", (e) => tryVoice($("#tts_provider").value, $("#tts_voice").value.trim(),
  Number($("#tts_rate").value) || 1, "", $("#voiceTryAudio"), null, e.target));

initMeta().then(route).catch((e) => { document.body.insertAdjacentHTML("afterbegin", `<div class="error">Không kết nối được tới tool: ${esc(e.message)}</div>`); });
