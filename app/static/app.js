const state = {
  guilds: [],
  sounds: [],
  soundFilters: { collection: "", folder: "" },
  seeking: false,
  applyingPlaybackControl: false,
};
const elements = {
  guild: document.querySelector("#guild-select"),
  channel: document.querySelector("#channel-select"),
  grid: document.querySelector("#sound-grid"),
  soundSearch: document.querySelector("#sound-search"),
  summary: document.querySelector("#sound-summary"),
  soundOverview: document.querySelector("#sound-overview"),
  collectionFilters: document.querySelector("#collection-filters"),
  folderFilters: document.querySelector("#folder-filters"),
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

function folderKey(sound) {
  const parts = sound.relative_path.split("/");
  return parts.length > 1 ? parts.slice(0, -1).join("/") : "";
}

function folderToken(sound) {
  return `${sound.collection}::${folderKey(sound)}`;
}

function displayFolder(folder) {
  return folder ? folder.split("/").join(" / ") : "Root";
}

function chip(label, active, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `filter-chip${active ? " active" : ""}`;
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

function availableFolders() {
  const source = state.soundFilters.collection
    ? state.sounds.filter((sound) => sound.collection === state.soundFilters.collection)
    : state.sounds;
  const folders = new Map();
  source.forEach((sound) => {
    const token = folderToken(sound);
    if (!folders.has(token)) {
      folders.set(token, {
        token,
        collection: sound.collection,
        folder: folderKey(sound),
      });
    }
  });
  return [...folders.values()].sort((left, right) =>
    `${left.collection}/${left.folder}`.localeCompare(`${right.collection}/${right.folder}`)
  );
}

function filteredSounds() {
  const query = elements.soundSearch.value.toLowerCase().trim();
  return state.sounds.filter((sound) => {
    if (state.soundFilters.collection && sound.collection !== state.soundFilters.collection) return false;
    if (state.soundFilters.folder && folderToken(sound) !== state.soundFilters.folder) return false;
    return `${sound.name} ${sound.collection} ${sound.relative_path}`.toLowerCase().includes(query);
  });
}

function renderSoundOverview(filtered) {
  const collections = new Set(filtered.map((sound) => sound.collection));
  const folders = new Set(filtered.map((sound) => folderToken(sound)));
  const metrics = [
    ["Visible sounds", filtered.length],
    ["Collections", collections.size],
    ["Folders", folders.size],
  ];
  elements.soundOverview.replaceChildren();
  metrics.forEach(([label, value]) => {
    const card = document.createElement("article");
    const number = document.createElement("strong");
    number.textContent = value;
    const caption = document.createElement("span");
    caption.textContent = label;
    card.append(number, caption);
    elements.soundOverview.append(card);
  });
}

function renderBrowseFilters() {
  const collections = [...new Set(state.sounds.map((sound) => sound.collection))].sort((left, right) => left.localeCompare(right));
  const folders = availableFolders();
  if (state.soundFilters.folder && !folders.some((folder) => folder.token === state.soundFilters.folder)) {
    state.soundFilters.folder = "";
  }
  elements.collectionFilters.replaceChildren(
    chip(`All (${state.sounds.length})`, !state.soundFilters.collection, () => {
      state.soundFilters.collection = "";
      state.soundFilters.folder = "";
      renderSounds();
    }),
  );
  collections.forEach((collection) => {
    const count = state.sounds.filter((sound) => sound.collection === collection).length;
    elements.collectionFilters.append(
      chip(`${collection} (${count})`, state.soundFilters.collection === collection, () => {
        state.soundFilters.collection = state.soundFilters.collection === collection ? "" : collection;
        state.soundFilters.folder = "";
        renderSounds();
      }),
    );
  });
  elements.folderFilters.replaceChildren(
    chip(`All (${folders.length})`, !state.soundFilters.folder, () => {
      state.soundFilters.folder = "";
      renderSounds();
    }),
  );
  folders.forEach((folder) => {
    const label = state.soundFilters.collection
      ? `${displayFolder(folder.folder)}`
      : `${folder.collection} / ${displayFolder(folder.folder)}`;
    const count = state.sounds.filter((sound) => folderToken(sound) === folder.token).length;
    elements.folderFilters.append(
      chip(`${label} (${count})`, state.soundFilters.folder === folder.token, () => {
        state.soundFilters.folder = state.soundFilters.folder === folder.token ? "" : folder.token;
        renderSounds();
      }),
    );
  });
}

function renderSounds() {
  const filtered = filteredSounds();
  renderBrowseFilters();
  renderSoundOverview(filtered);
  const folderCount = new Set(filtered.map((sound) => folderToken(sound))).size;
  elements.summary.textContent = `${filtered.length} sound${filtered.length === 1 ? "" : "s"} across ${folderCount} folder${folderCount === 1 ? "" : "s"}`;
  elements.grid.replaceChildren();
  if (!filtered.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = state.sounds.length ? "No sounds match the current search or folder filters." : "No audio files found in the configured directories.";
    elements.grid.append(empty);
    return;
  }
  const groups = new Map();
  filtered.forEach((sound) => {
    const token = folderToken(sound);
    if (!groups.has(token)) groups.set(token, []);
    groups.get(token).push(sound);
  });
  [...groups.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .forEach(([token, sounds]) => {
      const section = document.createElement("section");
      section.className = "sound-group";
      const heading = document.createElement("div");
      heading.className = "sound-group-heading";
      const titleWrap = document.createElement("div");
      const title = document.createElement("h2");
      title.textContent = displayFolder(folderKey(sounds[0]));
      const detail = document.createElement("small");
      detail.textContent = `${sounds[0].collection} · ${sounds.length} sound${sounds.length === 1 ? "" : "s"}`;
      titleWrap.append(title, detail);
      heading.append(titleWrap);
      section.append(heading);
      const groupGrid = document.createElement("div");
      groupGrid.className = "sound-grid";
      sounds.forEach((sound) => {
        const button = document.createElement("button");
        button.className = "sound-card";
        const icon = document.createElement("span");
        icon.className = "play-symbol";
        icon.textContent = "▶";
        const cardTitle = document.createElement("strong");
        cardTitle.textContent = sound.name;
        const collection = document.createElement("small");
        collection.textContent = sound.collection;
        const location = document.createElement("span");
        location.className = "sound-path";
        location.textContent = sound.relative_path;
        button.append(icon, cardTitle, collection, location);
        button.addEventListener("click", () => playSound(sound));
        groupGrid.append(button);
      });
      section.append(groupGrid);
      elements.grid.append(section);
    });
}

function refreshSounds(data) {
  state.sounds = data;
  const collections = new Set(data.map((sound) => sound.collection));
  if (state.soundFilters.collection && !collections.has(state.soundFilters.collection)) {
    state.soundFilters.collection = "";
    state.soundFilters.folder = "";
  }
  renderSounds();
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
    refreshSounds(sounds);
    setOptions(elements.guild, guilds, "Choose server");
    if (guilds.length === 1) elements.guild.value = guilds[0].id;
    updateChannels();
    startPlaybackPolling();
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
elements.soundSearch.addEventListener("input", renderSounds);
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
  if (elements.nowPlaying.hidden) return;
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
