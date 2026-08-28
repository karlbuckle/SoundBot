const state = { guilds: [], sounds: [], seeking: false, applyingPlaybackControl: false };
const elements = {
  guild: document.querySelector("#guild-select"),
  channel: document.querySelector("#channel-select"),
  grid: document.querySelector("#sound-grid"),
  summary: document.querySelector("#sound-summary"),
  toast: document.querySelector("#toast"),
  nowPlaying: document.querySelector("#now-playing"),
  nowTitle: document.querySelector("#now-playing-title"),
  seekSlider: document.querySelector("#seek-slider"),
  seekTime: document.querySelector("#seek-time"),
  volumeSlider: document.querySelector("#volume-slider"),
  volumeLabel: document.querySelector("#volume-label"),
  normalizeToggle: document.querySelector("#normalize-toggle"),
};
let playbackPoller = null;

function apiKey() { return sessionStorage.getItem("soundboardApiKey") || ""; }

function clientIdentity() {
  let id = localStorage.getItem("soundboardClientId");
  if (!id) {
    id = globalThis.crypto?.randomUUID?.() || `browser-${Date.now().toString(36)}`;
    localStorage.setItem("soundboardClientId", id);
  }
  const fallback = `Web user ${id.slice(0, 6)}`;
  return { client_id: id, requested_by: localStorage.getItem("soundboardClientName") || fallback };
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", "X-API-Key": apiKey(), ...(options.headers || {}) },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.json();
}

function notify(message, isError = false) {
  elements.toast.textContent = message;
  elements.toast.classList.toggle("error", isError);
  elements.toast.classList.add("show");
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => elements.toast.classList.remove("show"), 3500);
}

function destination() {
  // Discord snowflakes are larger than JavaScript's safe integer range. Keep
  // them as strings; Pydantic converts them to Python integers server-side.
  const guildId = elements.guild.value;
  const channelId = elements.channel.value;
  if (!guildId || !channelId) throw new Error("Choose a Discord server and voice channel first.");
  return { guild_id: guildId, channel_id: channelId };
}

function setOptions(select, items, placeholder) {
  select.replaceChildren();
  const first = new Option(placeholder, "");
  select.add(first);
  items.forEach((item) => select.add(new Option(item.name, item.id)));
}

function updateChannels() {
  const guild = state.guilds.find((item) => item.id === elements.guild.value);
  setOptions(elements.channel, guild?.channels || [], "Choose channel");
  if (guild?.channels.length === 1) elements.channel.value = guild.channels[0].id;
  if (guild?.status) renderNowPlaying(guild.status);
  else elements.nowPlaying.hidden = true;
}

function formatClock(seconds) {
  const total = Math.max(0, Math.floor(seconds || 0));
  const mins = Math.floor(total / 60);
  const secs = `${total % 60}`.padStart(2, "0");
  return `${mins}:${secs}`;
}

function playbackOptions() {
  return {
    volume: Number(elements.volumeSlider.value),
    normalize: elements.normalizeToggle.checked,
  };
}

function renderNowPlaying(status) {
  elements.nowTitle.textContent = status.title;
  const volume = Number.isFinite(status.volume) ? Math.max(0, status.volume) : 1;
  elements.volumeSlider.value = volume.toFixed(2);
  elements.volumeLabel.textContent = `Volume ${Math.round(volume * 100)}%`;
  elements.normalizeToggle.checked = Boolean(status.normalize);
  const duration = Number.isFinite(status.duration_seconds) ? status.duration_seconds : 0;
  const position = Number.isFinite(status.position_seconds) ? status.position_seconds : 0;
  elements.seekSlider.disabled = !status.can_seek;
  elements.seekSlider.max = String(Math.max(duration, 0));
  if (!state.seeking) elements.seekSlider.value = String(Math.min(position, duration || position));
  elements.seekTime.textContent = `${formatClock(position)} / ${formatClock(duration)}`;
  elements.nowPlaying.hidden = false;
}

function renderSounds() {
  const query = document.querySelector("#sound-search").value.toLowerCase().trim();
  const filtered = state.sounds.filter((sound) =>
    `${sound.name} ${sound.collection} ${sound.relative_path}`.toLowerCase().includes(query)
  );
  elements.summary.textContent = `${filtered.length} sound${filtered.length === 1 ? "" : "s"}`;
  elements.grid.replaceChildren();
  if (!filtered.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = state.sounds.length ? "No sounds match your search." : "No audio files found in the configured directories.";
    elements.grid.append(empty);
    return;
  }
  filtered.forEach((sound) => {
    const button = document.createElement("button");
    button.className = "sound-card";
    const icon = document.createElement("span");
    icon.className = "play-symbol";
    icon.textContent = "▶";
    const title = document.createElement("strong");
    title.textContent = sound.name;
    const collection = document.createElement("small");
    collection.textContent = sound.collection;
    button.append(icon, title, collection);
    button.addEventListener("click", () => playSound(sound));
    elements.grid.append(button);
  });
}

async function playSound(sound) {
  try {
    const result = await api("/api/play/sound", {
      method: "POST",
      body: JSON.stringify({ ...destination(), ...clientIdentity(), ...playbackOptions(), sound_id: sound.id }),
    });
    renderNowPlaying(result);
    startPlaybackPolling();
    notify(`Playing ${result.title}`);
  } catch (error) { notify(error.message, true); }
}

function statusPill(status) {
  const pill = document.createElement("span");
  pill.className = `status-pill ${status}`;
  pill.textContent = status;
  return pill;
}

function fillTable(id, rows, emptyText, columns) {
  const body = document.querySelector(`#${id}`);
  body.replaceChildren();
  if (!rows.length) {
    const row = body.insertRow();
    const cell = row.insertCell();
    cell.colSpan = columns;
    cell.className = "table-empty";
    cell.textContent = emptyText;
    return;
  }
  rows.forEach((cells) => {
    const row = body.insertRow();
    cells.forEach((value) => {
      const cell = row.insertCell();
      if (value instanceof Node) cell.append(value);
      else cell.textContent = value;
    });
  });
}

async function loadComponents() {
  try {
    const report = await api("/api/system");
    const grid = document.querySelector("#component-grid");
    grid.replaceChildren();
    report.components.forEach((component) => {
      const card = document.createElement("article");
      card.className = "component-card";
      const head = document.createElement("div");
      const name = document.createElement("strong");
      name.textContent = component.name;
      head.append(name, statusPill(component.status));
      const version = document.createElement("b");
      version.textContent = component.version;
      const detail = document.createElement("small");
      detail.textContent = component.detail;
      card.append(head, version, detail);
      grid.append(card);
    });
    fillTable("package-rows", report.packages.map((item) => [item.name, item.version, statusPill(item.status)]), "No package data.", 3);
    fillTable("directory-rows", report.directories.map((item) => [item.path, statusPill(item.status)]), "No audio directories configured.", 2);
  } catch (error) { notify(error.message, true); }
}

async function loadStats() {
  try {
    const report = await api("/api/stats");
    const metrics = [
      ["Total plays", report.totals.plays],
      ["Sound plays", report.totals.sounds],
      ["YouTube plays", report.totals.youtube],
      ["Active users", report.totals.users],
    ];
    const grid = document.querySelector("#metric-grid");
    grid.replaceChildren();
    metrics.forEach(([label, value]) => {
      const card = document.createElement("article");
      const number = document.createElement("strong");
      number.textContent = value;
      const caption = document.createElement("span");
      caption.textContent = label;
      card.append(number, caption);
      grid.append(card);
    });
    fillTable("top-sound-rows", report.top_sounds.map((item) => [item.title, item.plays]), "No sounds played yet.", 2);
    fillTable("top-user-rows", report.top_users.map((item) => [item.actor_name, item.plays, item.unique_items]), "No users recorded yet.", 3);
    fillTable("method-rows", report.methods.map((item) => [item.source, item.kind, item.plays]), "No usage recorded yet.", 3);
    fillTable("recent-rows", report.recent.map((item) => [
      new Date(item.occurred_at).toLocaleString(), item.title, item.actor_name,
      `${item.source} / ${item.kind}`, `${item.guild_name} / ${item.channel_name}`,
    ]), "No recent activity.", 5);
  } catch (error) { notify(error.message, true); }
}

async function load() {
  try {
    const [health, guilds, sounds] = await Promise.all([
      api("/api/health"), api("/api/discord/guilds"), api("/api/sounds"),
    ]);
    state.guilds = guilds;
    state.sounds = sounds;
    setOptions(elements.guild, guilds, "Choose server");
    if (guilds.length === 1) elements.guild.value = guilds[0].id;
    updateChannels();
    startPlaybackPolling();
    renderSounds();
    document.querySelector("#status-dot").classList.toggle("online", health.discord_ready);
    document.querySelector("#connection-title").textContent = health.discord_ready ? "Discord online" : "Discord offline";
    document.querySelector("#connection-copy").textContent = health.discord_ready ? `${guilds.length} server${guilds.length === 1 ? "" : "s"}` : "Check bot token";
  } catch (error) {
    notify(error.message, true);
    if (error.message.toLowerCase().includes("api key")) setApiKey();
  }

  async function refreshPlaybackStatus() {
    const guildId = elements.guild.value;
    if (!guildId) return;
    try {
      const status = await api(`/api/play/status/${guildId}`);
      if (status) renderNowPlaying(status);
      else elements.nowPlaying.hidden = true;
    } catch (_error) { /* polling is best effort */ }
  }

  function startPlaybackPolling() {
    clearInterval(playbackPoller);
    playbackPoller = setInterval(refreshPlaybackStatus, 1000);
    refreshPlaybackStatus();
  }
}

function setApiKey() {
  const value = window.prompt("Enter the web API key (leave blank if disabled):", apiKey());
  if (value !== null) {
    sessionStorage.setItem("soundboardApiKey", value);
    load();
  }
}

document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item === button));
  document.querySelectorAll(".page").forEach((page) => page.classList.toggle("active", page.id === `${button.dataset.page}-page`));
  history.replaceState(null, "", `#${button.dataset.page}`);
  if (button.dataset.page === "components") loadComponents();
  if (button.dataset.page === "stats") loadStats();
}));

elements.guild.addEventListener("change", updateChannels);
document.querySelector("#sound-search").addEventListener("input", renderSounds);
document.querySelector("#refresh-sounds").addEventListener("click", load);
document.querySelector("#settings-button").addEventListener("click", setApiKey);
document.querySelector("#refresh-components").addEventListener("click", loadComponents);
document.querySelector("#refresh-stats").addEventListener("click", loadStats);
document.querySelector("#client-name").value = clientIdentity().requested_by;
document.querySelector("#save-client-name").addEventListener("click", () => {
  const name = document.querySelector("#client-name").value.trim();
  if (!name) return notify("Enter a display name first.", true);
  localStorage.setItem("soundboardClientName", name);
  notify("Display name saved for future plays.");
});
document.querySelector("#play-youtube").addEventListener("click", async () => {
  try {
    const url = document.querySelector("#youtube-url").value;
    const result = await api("/api/play/youtube", {
      method: "POST", body: JSON.stringify({ ...destination(), ...clientIdentity(), ...playbackOptions(), url }),
    });
    renderNowPlaying(result);
    startPlaybackPolling();
    notify(`Playing ${result.title}`);
  } catch (error) { notify(error.message, true); }
});
document.querySelector("#youtube-url").addEventListener("keydown", (event) => {
  if (event.key === "Enter") document.querySelector("#play-youtube").click();
});
document.querySelector("#stop-button").addEventListener("click", async () => {
  try {
    const { guild_id } = destination();
    await api("/api/play/stop", { method: "POST", body: JSON.stringify({ guild_id }) });
    elements.nowPlaying.hidden = true;
    clearInterval(playbackPoller);
    notify("Playback stopped");
  } catch (error) { notify(error.message, true); }
});
document.querySelector("#leave-button").addEventListener("click", async () => {
  try {
    const { guild_id } = destination();
    await api("/api/play/leave", { method: "POST", body: JSON.stringify({ guild_id }) });
    elements.nowPlaying.hidden = true;
    clearInterval(playbackPoller);
    notify("Disconnected from voice");
  } catch (error) { notify(error.message, true); }
});
elements.volumeSlider.addEventListener("input", () => {
  const volume = Number(elements.volumeSlider.value);
  elements.volumeLabel.textContent = `Volume ${Math.round(volume * 100)}%`;
});
elements.volumeSlider.addEventListener("change", async () => {
  if (state.applyingPlaybackControl) return;
  state.applyingPlaybackControl = true;
  try {
    const result = await api("/api/play/volume", {
      method: "POST",
      body: JSON.stringify({ guild_id: destination().guild_id, volume: Number(elements.volumeSlider.value) }),
    });
    renderNowPlaying(result);
  } catch (error) { notify(error.message, true); }
  state.applyingPlaybackControl = false;
});
elements.normalizeToggle.addEventListener("change", async () => {
  if (state.applyingPlaybackControl) return;
  state.applyingPlaybackControl = true;
  try {
    const result = await api("/api/play/normalize", {
      method: "POST",
      body: JSON.stringify({ guild_id: destination().guild_id, normalize: elements.normalizeToggle.checked }),
    });
    renderNowPlaying(result);
  } catch (error) { notify(error.message, true); }
  state.applyingPlaybackControl = false;
});
elements.seekSlider.addEventListener("input", () => {
  state.seeking = true;
  const duration = Number(elements.seekSlider.max);
  const position = Number(elements.seekSlider.value);
  elements.seekTime.textContent = `${formatClock(position)} / ${formatClock(duration)}`;
});
elements.seekSlider.addEventListener("change", async () => {
  if (elements.seekSlider.disabled) {
    state.seeking = false;
    return;
  }
  try {
    const result = await api("/api/play/seek", {
      method: "POST",
      body: JSON.stringify({ guild_id: destination().guild_id, position_seconds: Number(elements.seekSlider.value) }),
    });
    renderNowPlaying(result);
  } catch (error) { notify(error.message, true); }
  state.seeking = false;
});

const route = location.hash.slice(1);
if (["sounds", "youtube", "components", "features", "stats"].includes(route)) document.querySelector(`[data-page="${route}"]`).click();
load();
