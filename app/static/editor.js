// Clip editor: trim a saved clip and, when it has per-speaker tracks, change
// each person's level. Preview happens in the browser with Web Audio; saving
// asks the server to render the result from the original tracks.

const WAVEFORM_BUCKETS = 1200;
const MIN_SELECTION = 0.25; // seconds, matches the server
const HANDLE_HIT = 10; // CSS pixels

const editor = {
  name: "",
  ctx: null,
  output: null,
  tracks: [], // { key, name, isSoundboard, auto, gain, buffer, envelope, node }
  hasStems: false,
  duration: 0,
  start: 0,
  end: 0,
  sources: [],
  playing: false,
  playFrom: 0,
  playStartedAt: 0,
  position: 0,
  drag: null,
  frame: 0,
  saving: false,
};

const editorEls = {
  overlay: document.querySelector("#clip-editor"),
  title: document.querySelector("#editor-title"),
  canvas: document.querySelector("#editor-canvas"),
  start: document.querySelector("#editor-start"),
  end: document.querySelector("#editor-end"),
  length: document.querySelector("#editor-length"),
  play: document.querySelector("#editor-play"),
  position: document.querySelector("#editor-position"),
  levels: document.querySelector("#editor-levels"),
  levelsNote: document.querySelector("#editor-levels-note"),
  save: document.querySelector("#editor-save"),
  replace: document.querySelector("#editor-replace"),
  loading: document.querySelector("#editor-loading"),
};

function formatSeconds(seconds) {
  const safe = Math.max(0, seconds);
  const mins = Math.floor(safe / 60);
  return `${mins}:${(safe % 60).toFixed(1).padStart(4, "0")}`;
}

async function fetchAudio(url) {
  const response = await fetch(url, { headers: { "X-API-Key": apiKey() }, cache: "no-cache" });
  if (!response.ok) throw new Error(`Could not load audio (${response.status})`);
  return editor.ctx.decodeAudioData(await response.arrayBuffer());
}

function envelope(buffer) {
  const data = buffer.getChannelData(0);
  const size = Math.max(1, Math.ceil(data.length / WAVEFORM_BUCKETS));
  const peaks = new Float32Array(WAVEFORM_BUCKETS);
  for (let bucket = 0; bucket < WAVEFORM_BUCKETS; bucket += 1) {
    let peak = 0;
    const end = Math.min(data.length, (bucket + 1) * size);
    for (let index = bucket * size; index < end; index += 1) {
      const value = Math.abs(data[index]);
      if (value > peak) peak = value;
    }
    peaks[bucket] = peak;
  }
  return peaks;
}

async function openEditor(name) {
  closeEditor();
  editor.name = name;
  editorEls.title.textContent = name.replace(/\.(mp3|wav)$/i, "");
  editorEls.overlay.hidden = false;
  editorEls.loading.hidden = false;
  editorEls.levels.replaceChildren();
  editorEls.levelsNote.textContent = "";
  try {
    editor.ctx = new AudioContext();
    // Roughly matches the soft limiter the server applies when it mixes.
    editor.output = editor.ctx.createDynamicsCompressor();
    editor.output.threshold.value = -3;
    editor.output.ratio.value = 20;
    editor.output.connect(editor.ctx.destination);

    const manifest = await api(`/api/clips/${encodeURIComponent(name)}/stems`);
    editor.hasStems = manifest.speakers.length > 0;
    const sources = editor.hasStems
      ? manifest.speakers.map((speaker) => ({
        key: speaker.key, name: speaker.name, isSoundboard: speaker.is_soundboard,
        auto: speaker.auto_gain, gain: speaker.gain,
        url: `/api/clips/${encodeURIComponent(name)}/stems/${encodeURIComponent(speaker.file)}`,
      }))
      : [{ key: "mix", name: "Mix", isSoundboard: false, auto: 1, gain: 1, url: `/api/clips/${encodeURIComponent(name)}` }];
    const buffers = await Promise.all(sources.map((source) => fetchAudio(source.url)));
    if (editor.name !== name) return; // closed or reopened while loading
    editor.tracks = sources.map((source, index) => {
      const node = editor.ctx.createGain();
      node.gain.value = source.auto * source.gain;
      node.connect(editor.output);
      return { ...source, buffer: buffers[index], envelope: envelope(buffers[index]), node };
    });
    const longest = Math.max(...buffers.map((buffer) => buffer.duration));
    editor.duration = manifest.duration ? Math.min(manifest.duration, longest) : longest;
    editor.start = 0;
    editor.end = editor.duration;
    editor.position = 0;
    editorEls.start.max = editorEls.end.max = editor.duration.toFixed(1);
    renderEditorLevels();
    syncSelection();
    editorEls.loading.hidden = true;
  } catch (error) {
    notify(error.message, true);
    closeEditor();
  }
}

function closeEditor() {
  stopPreview();
  cancelAnimationFrame(editor.frame);
  if (editor.ctx) editor.ctx.close();
  Object.assign(editor, { name: "", ctx: null, output: null, tracks: [], drag: null, saving: false });
  editorEls.overlay.hidden = true;
}

function renderEditorLevels() {
  editorEls.levels.replaceChildren();
  editorEls.levelsNote.textContent = editor.hasStems
    ? "Each person was evened out automatically when the clip was saved. Adjust anyone who still sounds off; you'll hear changes straight away."
    : "This clip was saved before per-person tracks were kept, so it can be trimmed but individual voices can't be adjusted.";
  if (!editor.hasStems) return;
  editor.tracks.forEach((track) => {
    const row = document.createElement("label");
    row.className = "speaker-row";
    const name = document.createElement("span");
    name.className = "speaker-name";
    name.textContent = track.isSoundboard ? "🔊 Soundboard" : track.name;
    const slider = document.createElement("input");
    slider.type = "range";
    slider.min = "0";
    slider.max = "4";
    slider.step = "0.05";
    slider.value = String(track.gain);
    slider.setAttribute("aria-label", `Level for ${track.name}`);
    const value = document.createElement("b");
    value.textContent = `${Math.round(track.gain * 100)}%`;
    slider.addEventListener("input", () => {
      track.gain = Number(slider.value);
      track.node.gain.value = track.auto * track.gain;
      value.textContent = `${Math.round(track.gain * 100)}%`;
      drawEditor();
    });
    row.append(name, slider, value);
    editorEls.levels.append(row);
  });
}

function syncSelection() {
  editorEls.start.value = editor.start.toFixed(1);
  editorEls.end.value = editor.end.toFixed(1);
  editorEls.length.textContent = `${formatSeconds(editor.end - editor.start)} selected of ${formatSeconds(editor.duration)}`;
  editorEls.position.textContent = formatSeconds(editor.position);
  drawEditor();
}

function setSelection(start, end) {
  const clampedStart = Math.min(Math.max(0, start), editor.duration);
  const clampedEnd = Math.min(Math.max(0, end), editor.duration);
  editor.start = Math.min(clampedStart, clampedEnd);
  editor.end = Math.max(clampedStart, clampedEnd);
  if (editor.position < editor.start || editor.position > editor.end) editor.position = editor.start;
  syncSelection();
}

function drawEditor() {
  const canvas = editorEls.canvas;
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.round(canvas.clientWidth * dpr));
  const height = Math.max(1, Math.round(canvas.clientHeight * dpr));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const draw = canvas.getContext("2d");
  const styles = getComputedStyle(document.documentElement);
  const accent = styles.getPropertyValue("--accent").trim() || "#a97cf8";
  const muted = styles.getPropertyValue("--line").trim() || "#302c39";
  draw.clearRect(0, 0, width, height);
  if (!editor.duration) return;

  const x = (seconds) => (seconds / editor.duration) * width;
  const selStart = x(editor.start);
  const selEnd = x(editor.end);
  draw.fillStyle = "#a97cf814";
  draw.fillRect(selStart, 0, selEnd - selStart, height);

  const middle = height / 2;
  for (let column = 0; column < width; column += 1) {
    const bucket = Math.min(WAVEFORM_BUCKETS - 1, Math.floor((column / width) * WAVEFORM_BUCKETS));
    let amplitude = 0;
    editor.tracks.forEach((track) => { amplitude += track.envelope[bucket] * track.auto * track.gain; });
    const bar = Math.max(dpr, Math.min(1, amplitude) * (middle - 2 * dpr));
    draw.fillStyle = column >= selStart && column <= selEnd ? accent : muted;
    draw.fillRect(column, middle - bar, 1, bar * 2);
  }

  draw.fillStyle = "#f5f2fa";
  [selStart, selEnd].forEach((edge) => {
    draw.fillRect(edge - dpr, 0, 2 * dpr, height);
    draw.fillRect(edge - 4 * dpr, 0, 8 * dpr, 6 * dpr);
    draw.fillRect(edge - 4 * dpr, height - 6 * dpr, 8 * dpr, 6 * dpr);
  });
  if (editor.playing || editor.position > editor.start) {
    draw.fillStyle = "#56d6a1";
    draw.fillRect(x(editor.position) - dpr / 2, 0, dpr, height);
  }
}

function timeAt(event) {
  const rect = editorEls.canvas.getBoundingClientRect();
  return ((event.clientX - rect.left) / rect.width) * editor.duration;
}

function handleAt(event) {
  const rect = editorEls.canvas.getBoundingClientRect();
  const px = event.clientX - rect.left;
  const startPx = (editor.start / editor.duration) * rect.width;
  const endPx = (editor.end / editor.duration) * rect.width;
  if (Math.abs(px - startPx) <= HANDLE_HIT) return "start";
  if (Math.abs(px - endPx) <= HANDLE_HIT) return "end";
  return null;
}

editorEls.canvas.addEventListener("pointerdown", (event) => {
  if (!editor.duration) return;
  const handle = handleAt(event);
  const time = timeAt(event);
  editor.drag = handle
    ? { mode: handle }
    : { mode: "new", anchor: time, previous: [editor.start, editor.end], moved: false };
  editorEls.canvas.setPointerCapture(event.pointerId);
});

editorEls.canvas.addEventListener("pointermove", (event) => {
  if (!editor.duration) return;
  if (!editor.drag) {
    editorEls.canvas.style.cursor = handleAt(event) ? "ew-resize" : "crosshair";
    return;
  }
  const time = timeAt(event);
  if (editor.drag.mode === "start") setSelection(Math.min(time, editor.end - MIN_SELECTION), editor.end);
  else if (editor.drag.mode === "end") setSelection(editor.start, Math.max(time, editor.start + MIN_SELECTION));
  else {
    editor.drag.moved = true;
    setSelection(editor.drag.anchor, time);
  }
});

editorEls.canvas.addEventListener("pointerup", (event) => {
  const drag = editor.drag;
  editor.drag = null;
  if (!drag || drag.mode !== "new") return;
  if (!drag.moved || editor.end - editor.start < MIN_SELECTION) {
    // A click rather than a drag: keep the selection and play from that point.
    setSelection(...drag.previous);
    const time = timeAt(event);
    if (time >= editor.start && time < editor.end) startPreview(time);
  }
});

function stopPreview() {
  editor.sources.forEach((source) => {
    source.onended = null;
    try { source.stop(); } catch (_error) { /* already stopped */ }
  });
  editor.sources = [];
  editor.playing = false;
  editorEls.play.textContent = "▶ Play selection";
}

function startPreview(from = editor.start) {
  if (!editor.ctx || !editor.tracks.length) return;
  stopPreview();
  editor.ctx.resume();
  const length = editor.end - from;
  if (length <= 0) return;
  const startAt = editor.ctx.currentTime + 0.05;
  editor.sources = editor.tracks.map((track) => {
    const source = editor.ctx.createBufferSource();
    source.buffer = track.buffer;
    source.connect(track.node);
    source.start(startAt, from, length);
    return source;
  });
  editor.sources[0].onended = () => {
    stopPreview();
    editor.position = editor.start;
    syncSelection();
  };
  editor.playing = true;
  editor.playFrom = from;
  editor.playStartedAt = startAt;
  editorEls.play.textContent = "❚❚ Pause";
  const tick = () => {
    if (!editor.playing) return;
    editor.position = Math.min(editor.end, editor.playFrom + Math.max(0, editor.ctx.currentTime - editor.playStartedAt));
    editorEls.position.textContent = formatSeconds(editor.position);
    drawEditor();
    editor.frame = requestAnimationFrame(tick);
  };
  editor.frame = requestAnimationFrame(tick);
}

function togglePreview() {
  if (editor.playing) {
    stopPreview();
    syncSelection();
  } else {
    const resume = editor.position > editor.start && editor.position < editor.end - 0.05;
    startPreview(resume ? editor.position : editor.start);
  }
}

async function saveEdit(replace) {
  if (editor.saving || !editor.name) return;
  if (replace && !window.confirm(`Replace ${editor.name} with this edit? The original can't be recovered.`)) return;
  editor.saving = true;
  [editorEls.save, editorEls.replace].forEach((button) => { button.disabled = true; });
  try {
    const gains = Object.fromEntries(editor.tracks.map((track) => [track.key, track.gain]));
    const clip = await api(`/api/clips/${encodeURIComponent(editor.name)}/edit`, {
      method: "POST",
      body: JSON.stringify({ start: editor.start, end: editor.end, gains: editor.hasStems ? gains : {}, replace }),
    });
    notify(replace ? `Updated ${clip.name}` : `Saved ${clip.name}`);
    closeEditor();
    loadClips();
  } catch (error) {
    notify(error.message, true);
    editor.saving = false;
  }
  [editorEls.save, editorEls.replace].forEach((button) => { button.disabled = false; });
}

editorEls.start.addEventListener("change", () => setSelection(Math.min(Number(editorEls.start.value), editor.end - MIN_SELECTION), editor.end));
editorEls.end.addEventListener("change", () => setSelection(editor.start, Math.max(Number(editorEls.end.value), editor.start + MIN_SELECTION)));
editorEls.play.addEventListener("click", togglePreview);
editorEls.save.addEventListener("click", () => saveEdit(false));
editorEls.replace.addEventListener("click", () => saveEdit(true));
document.querySelector("#editor-close").addEventListener("click", closeEditor);
document.querySelector("#editor-cancel").addEventListener("click", closeEditor);
editorEls.overlay.addEventListener("click", (event) => { if (event.target === editorEls.overlay) closeEditor(); });
window.addEventListener("resize", () => { if (!editorEls.overlay.hidden) drawEditor(); });
document.addEventListener("keydown", (event) => {
  if (editorEls.overlay.hidden) return;
  if (event.key === "Escape") closeEditor();
  else if (event.key === " " && !["INPUT", "BUTTON"].includes(document.activeElement?.tagName)) {
    event.preventDefault();
    togglePreview();
  }
});
