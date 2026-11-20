"use strict";

const el = id => document.getElementById(id);
const followed = new Map(JSON.parse(localStorage.getItem("glitch-followed") || "[]"));
let rosterOffset = 0;
let siteOffset = 0;
let eventOffset = 0;
let roster = new Map();
let eventLoad = 0;

async function api(path, body) {
  const response = await fetch(path, body ? {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  } : {});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "The world is unavailable.");
  return data;
}

function appendRow(list, label, detail, detailTag = "span") {
  const row = document.createElement("li");
  const text = document.createElement("span");
  const other = document.createElement(detailTag);
  text.textContent = label;
  other.textContent = detail;
  row.append(text, other);
  el(list).append(row);
  return other;
}

function updateTimelineSelector() {
  const selected = el("timeline").value;
  el("timeline").replaceChildren(new Option("All residents", ""));
  for (const [id, label] of new Map([...followed, ...roster])) {
    el("timeline").append(new Option(followed.has(id) ? label + " · Following" : label, id));
  }
  if ([...el("timeline").options].some(option => option.value === selected)) {
    el("timeline").value = selected;
  }
}

function setFollowing(id, name, follow) {
  if (follow) followed.set(id, name);
  else followed.delete(id);
  localStorage.setItem("glitch-followed", JSON.stringify([...followed]));
  updateTimelineSelector();
}

async function loadRoster(reset) {
  if (reset) {
    roster = new Map();
    rosterOffset = 0;
    el("roster").replaceChildren();
  }
  const state = await api("/api/status?offset=" + rosterOffset);
  el("world-status").textContent = state.name + " · World day " + state.turn;
  el("birth-form").hidden = !state.birth_enabled;
  if (!state.birth_enabled) {
    el("birth-status").textContent = "Birth admission is disabled in this read-only LAN preview.";
  }
  for (const resident of state.residents) {
    roster.set(resident.id, resident.name);
    const label = resident.site ? resident.name + " · " + resident.site : resident.name;
    const button = appendRow("roster", label, followed.has(resident.id) ? "Unfollow" : "Follow", "button");
    button.type = "button";
    button.setAttribute("aria-label", button.textContent + " " + resident.name);
    button.onclick = () => {
      setFollowing(resident.id, resident.name, !followed.has(resident.id));
      button.textContent = followed.has(resident.id) ? "Unfollow" : "Follow";
      button.setAttribute("aria-label", button.textContent + " " + resident.name);
    };
  }
  updateTimelineSelector();
  rosterOffset += state.residents.length;
  el("more-residents").hidden = rosterOffset >= state.total;
  document.querySelectorAll(".priority").forEach((select, index) => {
    if (!select.options.length) {
      for (const priority of state.priorities) select.add(new Option(priority, priority));
      select.selectedIndex = index;
    }
  });
}

async function loadSites(reset) {
  if (reset) {
    siteOffset = 0;
    el("sites").replaceChildren();
  }
  const page = await api("/api/map?offset=" + siteOffset);
  if (page.origin) {
    el("origin-title").textContent = (page.origin.name || "Crossing") + " · " + page.origin.id;
    el("inscription").textContent = page.origin.inscription || page.origin.terrain;
  }
  for (const site of page.sites) {
    appendRow("sites", site.id, site.finding ? site.terrain + " · " + site.finding : site.terrain);
  }
  siteOffset += page.sites.length;
  el("more-sites").hidden = siteOffset >= page.total;
}

async function loadEvents(reset) {
  const version = ++eventLoad;
  const resident = el("timeline").value;
  if (reset) {
    eventOffset = 0;
    el("events").replaceChildren();
  }
  const filter = "resident=" + encodeURIComponent(resident);
  const [page, digest] = await Promise.all([
    api("/api/chronicle?" + filter + "&offset=" + eventOffset),
    reset ? api("/api/digest?" + filter) : Promise.resolve(null),
  ]);
  if (version !== eventLoad) return;
  if (digest) {
    const count = type => digest.counts[type] || 0;
    el("digest").textContent = digest.sample_size ?
      "Recent activity · " + count("journey.moved") + " moves · " + count("site.discovered") +
      " discoveries · " + count("research.notebook") + " notebooks" : "No recorded activity.";
  }
  for (const event of page.events) appendRow("events", event.message, "Day " + event.turn, "time");
  el("older-events").hidden = page.next_offset === null;
  eventOffset = page.next_offset;
}

function saveKey(pair) {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open("glitch-creator", 1);
    open.onupgradeneeded = () => open.result.createObjectStore("keys");
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const db = open.result;
      const transaction = db.transaction("keys", pair ? "readwrite" : "readonly");
      const store = transaction.objectStore("keys");
      const request = pair ? store.put(pair, "creator") : store.get("creator");
      transaction.oncomplete = () => { db.close(); resolve(pair || request.result); };
      transaction.onabort = () => { db.close(); reject(transaction.error); };
    };
  });
}

function base64(buffer) {
  return btoa(String.fromCharCode(...new Uint8Array(buffer)));
}

el("birth-form").onsubmit = async event => {
  event.preventDefault();
  el("birth-submit").disabled = true;
  try {
    const name = el("name").value.trim();
    const priorities = [...document.querySelectorAll(".priority")].map(select => select.value);
    if (new Set(priorities).size !== 3) throw new Error("Choose three different priorities.");
    if (!globalThis.crypto?.subtle) throw new Error("Birth signing requires a secure browser context.");
    let pair = await saveKey();
    if (!pair) pair = await saveKey(await crypto.subtle.generateKey("Ed25519", false, ["sign", "verify"]));
    const key = base64(await crypto.subtle.exportKey("spki", pair.publicKey));
    const challenge = await api("/api/birth/challenge", {name, priorities, key});
    const bytes = new TextEncoder().encode(challenge.payload);
    const signature = base64(await crypto.subtle.sign("Ed25519", pair.privateKey, bytes));
    const {resident} = await api("/api/birth/admit", {id: challenge.id, signature});
    setFollowing(resident, name, true);
    el("timeline").value = resident;
    el("birth-status").textContent = name + " was born. You’re following their timeline.";
    el("name").value = "";
    await refresh();
  } catch (error) {
    el("birth-status").textContent = error.message;
  } finally {
    el("birth-submit").disabled = false;
  }
};

async function refresh() {
  await Promise.all([loadRoster(true), loadSites(true)]);
  await loadEvents(true);
}

function action(id, run, property = "onclick") {
  el(id)[property] = () => run().catch(error => { el("world-status").textContent = error.message; });
}

action("refresh", refresh);
action("older-events", () => loadEvents(false));
action("more-residents", () => loadRoster(false));
action("more-sites", () => loadSites(false));
action("timeline", () => loadEvents(true), "onchange");
refresh().catch(error => { el("world-status").textContent = error.message; });
