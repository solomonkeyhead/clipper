"use strict";

const STATUSES = ["ready", "posted", "submitted", "skipped"];
const LABEL = { ready: "Ready", posted: "Posted", submitted: "Submitted", skipped: "Skipped" };
const PLATFORM = { tiktok: "TikTok", instagram: "Instagram", instagram_reels: "Instagram Reels",
  youtube_shorts: "YouTube Shorts" };
const state = { overview: null, campaign: null, filter: "all" };

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (n) => (n == null ? "–" : Number(n).toLocaleString());

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" }, ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* not JSON */ }
    throw new Error(detail);
  }
  return res.json();
}

function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 2200);
}

async function copy(text, what) {
  try {
    await navigator.clipboard.writeText(text);
    toast(`${what} copied`);
  } catch (_) {
    toast("Could not copy; select the text and press Ctrl+C");
  }
}

/* ---------- sidebar ---------- */

function renderSidebar() {
  const current = currentCampaign();
  const item = (c) => {
    const posted = c.counts.posted + c.counts.submitted;
    return `<li><a href="#/c/${encodeURIComponent(c.name)}" class="${c.name === current ? "active" : ""}">
      <span>${esc(c.name)}</span>
      <span class="count" title="posted / clips">${posted}/${c.clips}</span></a></li>`;
  };
  const active = state.overview.campaigns.filter((c) => !c.archived);
  const archived = state.overview.campaigns.filter((c) => c.archived);
  $("#campaign-list").innerHTML = active.map(item).join("") ||
    `<li class="muted small" style="padding:8px 10px">No campaigns yet</li>`;
  $("#archived-list").innerHTML = archived.map(item).join("");
  $("#archived-wrap").hidden = archived.length === 0;
  const synced = state.overview.last_synced;
  $("#synced").textContent = synced ? `Stats from ${synced}` : "";
}

/* ---------- overview ---------- */

function renderOverview() {
  const campaigns = state.overview.campaigns.filter((c) => !c.archived);
  const total = (key) => campaigns.reduce((sum, c) => sum + (c.counts[key] || 0), 0);
  const views = campaigns.reduce((sum, c) => sum + (c.views || 0), 0);
  $("#main").innerHTML = `
    <h1>Overview</h1>
    <p class="muted">Every campaign's clips in one place. Pick a campaign to watch, copy captions and links, and track what's posted.</p>
    <div class="stat-row">
      <div class="card stat"><b>${total("ready")}</b><span class="muted small">ready to post</span></div>
      <div class="card stat"><b>${total("posted")}</b><span class="muted small">posted, not submitted</span></div>
      <div class="card stat"><b>${total("submitted")}</b><span class="muted small">submitted</span></div>
      <div class="card stat"><b>${num(views)}</b><span class="muted small">views on active campaigns</span></div>
    </div>
    <div class="overview-grid">
      ${campaigns.map((c) => `
        <a class="card" href="#/c/${encodeURIComponent(c.name)}">
          <h2>${esc(c.name)}</h2>
          <div class="chips">${c.platforms.map((p) => `<span class="chip">${esc(PLATFORM[p] || p)}</span>`).join("")}</div>
          <div class="stat-row">
            <div class="stat"><b>${c.counts.ready}</b><span class="muted small">ready</span></div>
            <div class="stat"><b>${c.counts.posted}</b><span class="muted small">posted</span></div>
            <div class="stat"><b>${c.counts.submitted}</b><span class="muted small">submitted</span></div>
            <div class="stat"><b>${num(c.views)}</b><span class="muted small">views</span></div>
          </div>
        </a>`).join("") || `<div class="empty">No campaigns yet. Add one under campaigns/ and run clipper.</div>`}
    </div>`;
}

/* ---------- campaign ---------- */

function autoPostLabel(value) {
  const global = state.overview.settings.auto_post === "1";
  if (value === null || value === undefined) return `Follow global (${global ? "on" : "off"})`;
  return value ? "On" : "Off";
}

function renderCampaign() {
  const data = state.campaign;
  const c = data.campaign;
  const clips = data.clips.filter((clip) => state.filter === "all" || clip.status === state.filter);
  const countOf = (s) => data.clips.filter((clip) => clip.status === s).length;
  const links = data.clips.flatMap((clip) => clip.posts.map((p) => p.url));
  const chips = [];
  (c.platforms || []).forEach((p) => chips.push(`<span class="chip">${esc(PLATFORM[p] || p)}</span>`));
  if (c.min_seconds) chips.push(`<span class="chip">${c.min_seconds}–${c.max_seconds}s</span>`);
  if (c.required_text) chips.push(`<span class="chip accent">caption must include ${esc(c.required_text)}</span>`);
  (c.hashtags || []).forEach((h) => chips.push(`<span class="chip">${esc(h)}</span>`));
  if (c.credit) chips.push(`<span class="chip accent">credit: ${esc(c.credit)}</span>`);
  if (c.original_audio) chips.push(`<span class="chip">original audio</span>`);

  const autoValue = data.auto_post === null || data.auto_post === undefined ? "" : String(data.auto_post);
  $("#main").innerHTML = `
    <div class="campaign-head">
      <div>
        <h1>${esc(c.name)}</h1>
        <div class="chips">${chips.join("")}</div>
      </div>
      <div class="head-actions">
        <label class="small muted">Auto-post
          <select id="auto-post" title="Whether approved clips post themselves">
            <option value="" ${autoValue === "" ? "selected" : ""}>${esc(autoPostLabel(null))}</option>
            <option value="1" ${autoValue === "1" ? "selected" : ""}>On</option>
            <option value="0" ${autoValue === "0" ? "selected" : ""}>Off</option>
          </select>
        </label>
        <button class="btn primary" id="copy-links" ${links.length ? "" : "disabled"}>Copy all links (${links.length})</button>
        <button class="btn ghost" id="archive">${data.archived ? "Unarchive" : "Archive"}</button>
      </div>
    </div>
    ${c.notes || c.authorization ? `<details class="brief"><summary>Brief notes</summary>
      <pre>${esc([c.notes, c.authorization ? "Authorization: " + c.authorization : ""].filter(Boolean).join("\n\n"))}</pre></details>` : ""}
    <div class="tabs" role="tablist">
      ${["all", ...STATUSES].map((s) => `<button class="tab ${state.filter === s ? "active" : ""}" data-filter="${s}" role="tab">
        ${s === "all" ? "All" : LABEL[s]} <span class="muted">${s === "all" ? data.clips.length : countOf(s)}</span></button>`).join("")}
    </div>
    <div class="clip-grid">
      ${clips.map(renderClip).join("") || `<div class="empty">No clips here.</div>`}
    </div>`;

  $("#copy-links").onclick = () => copy(links.join("\n"), `${links.length} link(s)`);
  $("#archive").onclick = async () => {
    await api(`/api/campaigns/${encodeURIComponent(c.name)}`, { method: "PATCH", body: { archived: !data.archived } });
    await refresh();
  };
  $("#auto-post").onchange = async (e) => {
    const v = e.target.value;
    await api(`/api/campaigns/${encodeURIComponent(c.name)}`, { method: "PATCH", body: { auto_post: v === "" ? null : v === "1" } });
    toast("Saved");
    await refresh();
  };
  document.querySelectorAll(".tab").forEach((tab) => tab.onclick = () => { state.filter = tab.dataset.filter; renderCampaign(); });
  bindClipActions();
}

function renderPost(p) {
  const bits = [`${num(p.views_latest)} views`];
  if (p.avg_watch_s != null) bits.push(`${p.avg_watch_s}s avg watch`);
  if (p.watched_full_pct != null) bits.push(`${p.watched_full_pct}% watched fully`);
  if (p.skip_rate_pct != null) bits.push(`${p.skip_rate_pct}% skipped in 3s`);
  if (p.likes) bits.push(`${num(p.likes)} likes`);
  return `<div class="post">
    <span class="platform">${esc(PLATFORM[p.platform] || p.platform)}</span>
    <a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.url.replace(/^https:\/\/(www\.)?/, ""))}</a>
    <span class="nums">${bits.join(" · ")}${p.posted_at ? ` · ${esc(p.posted_at)}` : ""}</span>
  </div>`;
}

function renderClip(clip) {
  const title = clip.title || clip.hook || clip.clip_id;
  const len = clip.duration_s ? `${Math.round(clip.duration_s)}s` : "";
  return `<article class="card clip" data-id="${clip.id}">
    ${clip.exists ? `<video src="${clip.video}" poster="/thumb/${clip.id}" preload="none" controls playsinline></video>`
      : `<div class="empty small">File missing</div>`}
    <div class="clip-body">
      <div class="row"><span class="status ${clip.status}">${LABEL[clip.status]}</span>
        <span class="clip-meta">${esc(len)}${clip.source_title ? " · " + esc(clip.source_title) : ""}</span></div>
      <div class="clip-title">${esc(title)}</div>
      ${clip.hook && clip.hook !== title ? `<div class="clip-meta">On screen: ${esc(clip.hook)}</div>` : ""}
      ${clip.caption ? `<div class="caption">${esc(clip.caption)}</div>` : ""}
      <div class="row">
        ${clip.caption ? `<button class="btn small" data-act="copy-caption">Copy caption</button>` : ""}
        <button class="btn small ghost" data-act="reveal" title="Show the file in Explorer, e.g. to drag it into an upload page">Show file</button>
        <select class="small" data-act="status" aria-label="Status">
          ${STATUSES.map((s) => `<option value="${s}" ${clip.marked === s ? "selected" : ""}>${LABEL[s]}</option>`).join("")}
        </select>
      </div>
      ${clip.posts.length ? `<div class="posts">${clip.posts.map(renderPost).join("")}</div>` : ""}
      <textarea class="small note" data-act="notes" placeholder="Add a note…" rows="1"
        aria-label="Note">${esc(clip.notes)}</textarea>
    </div>
  </article>`;
}

function fitNote(el) {
  el.style.height = "auto";
  el.style.height = `${el.scrollHeight + 2}px`;
}

function bindClipActions() {
  document.querySelectorAll(".note").forEach((el) => { fitNote(el); el.oninput = () => fitNote(el); });
  document.querySelectorAll(".clip").forEach((card) => {
    const id = Number(card.dataset.id);
    const clip = state.campaign.clips.find((c) => c.id === id);
    card.querySelectorAll("[data-act]").forEach((el) => {
      const act = el.dataset.act;
      if (act === "copy-caption") el.onclick = () => copy(clip.caption, "Caption");
      if (act === "reveal") el.onclick = async () => {
        try { await api(`/api/clips/${id}/reveal`, { method: "POST" }); } catch (e) { toast(e.message); }
      };
      if (act === "notes") el.onchange = async () => {
        await api(`/api/clips/${id}`, { method: "PATCH", body: { notes: el.value } });
        clip.notes = el.value;
        toast("Note saved");
      };
      if (act === "status") el.onchange = async () => {
        await api(`/api/clips/${id}`, { method: "PATCH", body: { status: el.value } });
        toast(`Marked ${LABEL[el.value].toLowerCase()}`);
        await refresh();
      };
    });
  });
}

/* ---------- routing ---------- */

function currentCampaign() {
  const m = location.hash.match(/^#\/c\/(.+)$/);
  return m ? decodeURIComponent(m[1]) : null;
}

async function refresh() {
  state.overview = await api("/api/overview");
  const name = currentCampaign();
  if (name) {
    try {
      state.campaign = await api(`/api/campaigns/${encodeURIComponent(name)}`);
    } catch (e) {
      location.hash = "#/";
      return;
    }
  }
  renderSidebar();
  if (name) renderCampaign(); else renderOverview();
}

window.addEventListener("hashchange", () => { state.filter = "all"; refresh(); });

$("#sync-btn").onclick = async () => {
  const btn = $("#sync-btn");
  btn.disabled = true;
  btn.textContent = "Syncing…";
  try {
    const result = await api("/api/sync", { method: "POST" });
    toast(result.problems.length ? result.problems.join("; ") : "Stats updated");
    await refresh();
  } catch (e) {
    toast(`Sync failed: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Sync now";
  }
};

$("#settings-btn").onclick = () => {
  $("#auto-post-global").checked = state.overview.settings.auto_post === "1";
  $("#settings").showModal();
};
$("#auto-post-global").onchange = async (e) => {
  await api("/api/settings", { method: "PUT", body: { auto_post: e.target.checked ? "1" : "0" } });
  toast("Saved");
  await refresh();
};

refresh().catch((e) => { $("#main").innerHTML = `<div class="empty">Could not load: ${esc(e.message)}</div>`; });
