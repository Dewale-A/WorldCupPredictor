/* Central state keeps every section in sync after baseline and what-if updates. */
const state = {
  teams: [],
  groups: [],
  baselineOdds: [],
  currentOdds: [],
  groupOdds: {},
  sampleBracket: [],
  modalChampion: "",
  backtest: null,
  /* Admin gate: tracks whether live-result editing is locked and unlocked. */
  adminLocked: false,
  adminUnlocked: false,
  adminKey: "",
  selectsReady: false,
};

/*
  Team -> ISO 3166-1 alpha-2 country code. We render flags as SVG images from
  flagcdn.com rather than emoji, because emoji flags fail to render on many
  platforms (Windows, headless browsers) and look like plain letter blocks.
  SVG images are crisp and identical everywhere, which matters for a polished,
  shareable product. Sub-national sides (England, Scotland, Wales, N. Ireland)
  use flagcdn's GB-region codes; Kosovo uses xk.
*/
const FLAG_CODES = {
  Argentina: "ar", Algeria: "dz", Australia: "au", Austria: "at",
  Belgium: "be", Bolivia: "bo", "Bosnia and Herzegovina": "ba", Brazil: "br",
  Canada: "ca", "Cape Verde": "cv", Colombia: "co", Croatia: "hr",
  "Curaçao": "cw", "Czech Republic": "cz", "DR Congo": "cd", Denmark: "dk",
  Ecuador: "ec", Egypt: "eg", England: "gb-eng", France: "fr",
  Germany: "de", Ghana: "gh", Haiti: "ht", Iran: "ir",
  Iraq: "iq", Italy: "it", "Ivory Coast": "ci", Jamaica: "jm",
  Japan: "jp", Jordan: "jo", Kosovo: "xk", Mexico: "mx",
  Morocco: "ma", Netherlands: "nl", "New Caledonia": "nc", "New Zealand": "nz",
  "North Macedonia": "mk", "Northern Ireland": "gb-nir", Norway: "no", Panama: "pa",
  Paraguay: "py", Poland: "pl", Portugal: "pt", Qatar: "qa",
  "Republic of Ireland": "ie", Romania: "ro", "Saudi Arabia": "sa", Scotland: "gb-sct",
  Senegal: "sn", Slovakia: "sk", "South Africa": "za", "South Korea": "kr",
  Spain: "es", Suriname: "sr", Sweden: "se", Switzerland: "ch",
  Tunisia: "tn", Turkey: "tr", Ukraine: "ua", "United States": "us",
  Uruguay: "uy", Uzbekistan: "uz", Wales: "gb-wls",
};

function flagFor(team) {
  /*
    Return an <img> tag for the team's flag. width=20 keeps a fixed slot so the
    leaderboard stays perfectly aligned. loading=lazy avoids blocking render.
    Unknown teams get no image (empty string) rather than a broken icon.
  */
  const code = FLAG_CODES[team];
  if (!code) return "";
  return `<img class="flag" src="https://flagcdn.com/w40/${code}.png" ` +
    `srcset="https://flagcdn.com/w80/${code}.png 2x" alt="" width="20" height="15" loading="lazy" />`;
}

const leaderboardEl = document.getElementById("leaderboard");
const simCountEl = document.getElementById("simCount");
const groupsGridEl = document.getElementById("groupsGrid");
const whatifStatusEl = document.getElementById("whatifStatus");
const bracketEl = document.getElementById("bracket");
const bracketNoteEl = document.getElementById("bracketNote");
const backtestHeadlineEl = document.getElementById("backtestHeadline");
const backtestTableEl = document.getElementById("backtestTable");

const teamAEl = document.getElementById("teamA");
const teamBEl = document.getElementById("teamB");
const whatifHomeEl = document.getElementById("whatifHome");
const whatifAwayEl = document.getElementById("whatifAway");

const h2hResultEl = document.getElementById("h2hResult");
const h2hMetaEl = document.getElementById("h2hMeta");
const winAEl = document.getElementById("winA");
const drawEl = document.getElementById("draw");
const winBEl = document.getElementById("winB");
const labelAEl = document.getElementById("labelA");
const labelDEl = document.getElementById("labelD");
const labelBEl = document.getElementById("labelB");
const likelyScoreEl = document.getElementById("likelyScore");

const resimBtnEl = document.getElementById("resimBtn");
const shareBtnEl = document.getElementById("shareBtn");
const themeToggleEl = document.getElementById("themeToggle");

const modalEl = document.getElementById("teamModal");
const modalBackdropEl = document.getElementById("teamModalBackdrop");
const modalCloseEl = document.getElementById("teamModalClose");
const modalContentEl = document.getElementById("teamModalContent");

function pct(value, digits = 1) {
  /* One formatter keeps percentages consistent across every data module. */
  return `${(Number(value || 0) * 100).toFixed(digits)}%`;
}

function plusMinusPct(ciLow, ciHigh) {
  /* Showing half-width gives users an immediate uncertainty cue without clutter. */
  if (ciLow == null || ciHigh == null) return "";
  const half = ((Number(ciHigh) - Number(ciLow)) / 2) * 100;
  return `±${half.toFixed(1)}`;
}

function spinner(label) {
  /* Shared inline spinner keeps loading feedback visually consistent app-wide. */
  return `<span class="inline-loading"><span class="spinner" aria-hidden="true"></span>${label}</span>`;
}

function populateTeamSelect(selectEl, teams, selectedIndex = 0) {
  /* Centralized select population prevents team lists drifting between controls. */
  selectEl.innerHTML = "";
  teams.forEach((team, index) => {
    const opt = document.createElement("option");
    opt.value = team;
    opt.textContent = team;
    if (index === selectedIndex) opt.selected = true;
    selectEl.appendChild(opt);
  });
}

function renderLeaderboard(odds) {
  /* Leaderboard is the hero data view, so it gets the strongest visual hierarchy. */
  leaderboardEl.innerHTML = "";
  odds.forEach((row, idx) => {
    const wrap = document.createElement("div");
    wrap.className = `odds-row${idx === 0 ? " leader" : ""}`;

    const rank = document.createElement("div");
    rank.className = "odds-rank";
    rank.textContent = idx + 1;

    const team = document.createElement("button");
    team.className = "odds-team team-link";
    team.type = "button";
    team.dataset.team = row.team;
    team.innerHTML = `${flagFor(row.team)}<span class="team-name">${row.team}</span>`;

    const track = document.createElement("div");
    track.className = "odds-track";
    const fill = document.createElement("div");
    fill.className = "odds-fill";
    track.appendChild(fill);

    const val = document.createElement("div");
    val.className = "odds-pct";
    const range = plusMinusPct(row.ci_low, row.ci_high);
    val.innerHTML = `${pct(row.prob)}${range ? ` <span class="ci-note">${range}</span>` : ""}`;

    wrap.appendChild(rank);
    wrap.appendChild(team);
    wrap.appendChild(track);
    wrap.appendChild(val);
    leaderboardEl.appendChild(wrap);

    /* Deferred width assignment preserves smooth bar transitions on updates. */
    requestAnimationFrame(() => {
      fill.style.width = `${Math.max(1.2, Number(row.prob || 0) * 100)}%`;
    });
  });
}

function renderGroups(groups) {
  /* Group cards prioritize quick scan value while surfacing advancement signal. */
  groupsGridEl.innerHTML = "";

  groups.forEach((group) => {
    const groupAdvance = group.teams
      .map((team) => ({ team, adv: Number(state.groupOdds?.[team]?.advance || 0) }))
      .sort((a, b) => b.adv - a.adv);
    const topAdvance = new Set(groupAdvance.slice(0, 2).map((row) => row.team));

    const card = document.createElement("article");
    card.className = "group-card";

    const title = document.createElement("h3");
    title.textContent = group.name;

    const list = document.createElement("ul");
    group.teams.forEach((teamName) => {
      const adv = Number(state.groupOdds?.[teamName]?.advance || 0);
      const li = document.createElement("li");
      if (topAdvance.has(teamName)) li.classList.add("likely-advance");

      li.innerHTML = `
        <button type="button" class="group-team-link team-link" data-team="${teamName}">
          ${flagFor(teamName)}<span class="team-name">${teamName}</span>
        </button>
        <div class="group-adv-wrap">
          <div class="group-adv-track"><div class="group-adv-fill" style="width:${(adv * 100).toFixed(1)}%"></div></div>
          <span class="group-adv-pct">${pct(adv)}</span>
        </div>
      `;
      list.appendChild(li);
    });

    card.appendChild(title);
    card.appendChild(list);
    groupsGridEl.appendChild(card);
  });
}

function renderBracket(sampleBracket, modalChampion) {
  /* Bracket visualization turns model output into an intuitive tournament story. */
  bracketEl.innerHTML = "";
  const roundLabels = ["Round of 32", "Round of 16", "Quarter-finals", "Semi-finals", "Final"];

  (sampleBracket || []).forEach((roundMatches, roundIndex) => {
    const col = document.createElement("div");
    col.className = "bracket-col";

    const head = document.createElement("h3");
    head.textContent = roundLabels[roundIndex] || `Round ${roundIndex + 1}`;
    col.appendChild(head);

    (roundMatches || []).forEach((match) => {
      const card = document.createElement("article");
      card.className = "match-card";

      const aWin = match.winner === match.a;
      const bWin = match.winner === match.b;

      card.innerHTML = `
        <div class="match-team ${aWin ? "winner" : "loser"}">${flagFor(match.a)}<span>${match.a || "TBD"}</span></div>
        <div class="match-team ${bWin ? "winner" : "loser"}">${flagFor(match.b)}<span>${match.b || "TBD"}</span></div>
      `;
      col.appendChild(card);
    });

    bracketEl.appendChild(col);
  });

  bracketNoteEl.textContent = modalChampion
    ? `One representative simulation ending in ${modalChampion} lifting the trophy.`
    : "One representative simulation from the model output.";
}

function renderBacktest(backtest) {
  /* Backtest is the trust anchor, so headline metric and evidence rows are explicit. */
  if (!backtest) {
    backtestHeadlineEl.textContent = "Backtest data unavailable.";
    backtestTableEl.innerHTML = "";
    return;
  }

  backtestHeadlineEl.innerHTML = `
    <p>
      The actual champion landed in this model's top 5 in
      <strong>${pct(backtest.winner_top5_rate, 0)}</strong>
      of past World Cups.
    </p>
  `;

  const editions = backtest.editions || [];
  backtestTableEl.innerHTML = `
    <div class="bt-row bt-head">
      <span>Year</span>
      <span>Actual winner</span>
      <span>Model rank</span>
      <span>Favorite</span>
    </div>
    ${editions.map((edition) => `
      <div class="bt-row">
        <span>${edition.year}</span>
        <span>${flagFor(edition.winner)} ${edition.winner}</span>
        <span>#${edition.winner_rank} of ${edition.field_size}</span>
        <span>${flagFor(edition.model_favorite)} ${edition.model_favorite}</span>
      </div>
      <p class="bt-note">Actual winner ${edition.winner} -> model ranked #${edition.winner_rank} of ${edition.field_size}.</p>
    `).join("")}
  `;
}

function applyTheme(theme) {
  /* Variable-driven theming avoids duplicated component CSS and keeps accents stable. */
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("wc-theme", theme);
  /* Use a half-circle glyph that renders reliably on every platform, unlike
     emoji which can fall back to blank boxes in some browsers and headless renders. */
  themeToggleEl.textContent = theme === "light" ? "◑" : "◐";
}

function initTheme() {
  /* Persisted preference respects user choice across refreshes and sessions. */
  const saved = localStorage.getItem("wc-theme");
  applyTheme(saved === "light" ? "light" : "dark");
}

function openModal(html) {
  /* Dedicated modal helpers keep accessibility states reliable across open/close paths. */
  modalContentEl.innerHTML = html;
  modalEl.classList.remove("hidden");
  modalEl.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
}

function closeModal() {
  /* Clearing content on close avoids stale team state flashing on the next open. */
  modalEl.classList.add("hidden");
  modalEl.setAttribute("aria-hidden", "true");
  modalContentEl.innerHTML = "";
  document.body.classList.remove("modal-open");
}

function renderProgressionBars(progression) {
  /* Ordered round rendering gives a predictable "path" narrative for fans. */
  const ordered = [
    ["Group", 1],
    ["R32", Number(progression?.R32 ?? 0)],
    ["R16", Number(progression?.R16 ?? 0)],
    ["QF", Number(progression?.QF ?? 0)],
    ["SF", Number(progression?.SF ?? 0)],
    ["Final", Number(progression?.Final ?? 0)],
    ["Win", Number(progression?.Win ?? 0)],
  ];

  return ordered.map(([label, value]) => `
    <div class="prog-row">
      <span class="prog-label">${label}</span>
      <div class="prog-track"><div class="prog-fill" style="width:${Math.max(1, value * 100)}%"></div></div>
      <span class="prog-pct">${pct(value)}</span>
    </div>
  `).join("");
}

function renderTeamGroupOdds(groupOdds) {
  /* Showing finish distribution clarifies if strength comes from consistency or volatility. */
  const rows = [
    ["1st", Number(groupOdds?.first ?? 0)],
    ["2nd", Number(groupOdds?.second ?? 0)],
    ["3rd", Number(groupOdds?.third ?? 0)],
    ["Advance", Number(groupOdds?.advance ?? 0)],
  ];

  return rows.map(([label, value]) => `
    <div class="mini-row">
      <span>${label}</span>
      <div class="mini-track"><div class="mini-fill" style="width:${(value * 100).toFixed(1)}%"></div></div>
      <strong>${pct(value)}</strong>
    </div>
  `).join("");
}

function renderLikelyOpponents(opponents) {
  /* Opponent list gives fans practical matchup context beyond abstract probabilities. */
  if (!opponents || opponents.length === 0) {
    return '<p class="muted">No likely opponent data for this team.</p>';
  }

  return `
    <ul class="opp-list">
      ${opponents.slice(0, 6).map((opp) => `
        <li>
          <span>${flagFor(opp.team)} ${opp.team}</span>
          <strong>${pct(opp.prob)}</strong>
        </li>
      `).join("")}
    </ul>
  `;
}

async function showTeamDeepDive(teamName) {
  /* Live team fetch guarantees the card reflects latest what-if or baseline state. */
  openModal(`
    <div class="team-loading">${spinner("Loading team profile")}</div>
  `);

  try {
    const res = await fetch(`/api/team/${encodeURIComponent(teamName)}`);
    if (!res.ok) throw new Error("Team profile request failed.");
    const data = await res.json();

    const oddsPct = pct(data.odds?.prob || 0);
    const lowPct = ((Number(data.odds?.ci_low || 0) * 100).toFixed(1));
    const highPct = ((Number(data.odds?.ci_high || 0) * 100).toFixed(1));

    openModal(`
      <div class="team-card-head">
        <h3 id="teamModalTitle">${flagFor(data.team)} ${data.team}</h3>
        <span class="team-group-chip">${data.group || "Group -"}</span>
      </div>
      <p class="team-title-odds">Title odds: <strong>${oddsPct}</strong> <span>[${lowPct}-${highPct}]</span></p>

      <section class="team-section">
        <h4>Path to Glory</h4>
        <div class="progression-grid">${renderProgressionBars(data.progression || {})}</div>
      </section>

      <section class="team-section">
        <h4>Group finish odds</h4>
        <div class="mini-grid">${renderTeamGroupOdds(data.group_odds || {})}</div>
      </section>

      <section class="team-section">
        <h4>Likely first knockout opponents</h4>
        ${renderLikelyOpponents(data.likely_opponents || [])}
      </section>
    `);
  } catch (err) {
    openModal(`<p class="muted">Could not load team deep-dive right now.</p>`);
    console.error(err);
  }
}

async function loadPredictions() {
  /* Single baseline fetch seeds every section, reducing redundant API round-trips. */
  const res = await fetch("/api/predictions");
  if (!res.ok) throw new Error("Could not load predictions.");

  const data = await res.json();
  state.teams = data.wc_teams || [];
  state.groups = data.groups || [];
  state.baselineOdds = data.championship_odds || [];
  state.currentOdds = [...state.baselineOdds];
  state.groupOdds = data.group_odds || {};
  state.sampleBracket = data.sample_bracket || [];
  state.modalChampion = data.modal_champion || "";
  state.backtest = data.backtest || null;

  simCountEl.textContent = `${data.n_sims?.toLocaleString() || "0"} baseline simulations`;
  renderLeaderboard(state.currentOdds.slice(0, 20));
  renderGroups(state.groups);
  renderBracket(state.sampleBracket, state.modalChampion);
  renderBacktest(state.backtest);

  /* Populate the team dropdowns only once. loadPredictions runs again after each
     live result, and re-filling the selects would wipe the user's current choice. */
  if (!state.selectsReady) {
    populateTeamSelect(teamAEl, state.teams, 0);
    populateTeamSelect(teamBEl, state.teams, 1);
    populateTeamSelect(whatifHomeEl, state.teams, 0);
    populateTeamSelect(whatifAwayEl, state.teams, 1);
    populateTeamSelect(liveHomeEl, state.teams, 0);
    populateTeamSelect(liveAwayEl, state.teams, 1);
    state.selectsReady = true;
  }
}

async function runH2H() {
  /* H2H endpoint stays isolated so users can test matchups without changing global odds. */
  const teamA = teamAEl.value;
  const teamB = teamBEl.value;
  if (!teamA || !teamB || teamA === teamB) {
    alert("Choose two different teams.");
    return;
  }

  const res = await fetch(`/api/h2h?team_a=${encodeURIComponent(teamA)}&team_b=${encodeURIComponent(teamB)}`);
  if (!res.ok) {
    alert("Prediction failed. Try different teams.");
    return;
  }
  const data = await res.json();

  h2hResultEl.classList.remove("hidden");
  h2hMetaEl.innerHTML =
    `${flagFor(data.team_a)} ${data.team_a} <span style="opacity:.6">Elo ${data.elo_a}</span>` +
    ` &nbsp;•&nbsp; ` +
    `${flagFor(data.team_b)} ${data.team_b} <span style="opacity:.6">Elo ${data.elo_b}</span>`;

  winAEl.style.width = `${(Number(data.win_a || 0) * 100).toFixed(1)}%`;
  drawEl.style.width = `${(Number(data.draw || 0) * 100).toFixed(1)}%`;
  winBEl.style.width = `${(Number(data.win_b || 0) * 100).toFixed(1)}%`;

  labelAEl.textContent = `${data.team_a}: ${pct(data.win_a)}`;
  labelDEl.textContent = `Draw: ${pct(data.draw)}`;
  labelBEl.textContent = `${data.team_b}: ${pct(data.win_b)}`;
  likelyScoreEl.textContent = data.likely_score;
}

async function runWhatIf() {
  /* Explicit loading state reassures users during a potentially expensive resimulation. */
  const home = whatifHomeEl.value;
  const away = whatifAwayEl.value;
  const homeScore = Number(document.getElementById("homeScore").value);
  const awayScore = Number(document.getElementById("awayScore").value);

  if (!home || !away || home === away) {
    alert("Choose two different teams for what-if mode.");
    return;
  }

  whatifStatusEl.innerHTML = spinner("Simulating alternate timeline");
  resimBtnEl.disabled = true;

  try {
    const res = await fetch("/api/whatif", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        forced_results: [{ home, away, home_score: homeScore, away_score: awayScore }],
      }),
    });

    if (!res.ok) {
      whatifStatusEl.textContent = "What-if failed. Check team selection.";
      return;
    }

    const data = await res.json();
    state.currentOdds = data.championship_odds || [];
    renderLeaderboard(state.currentOdds.slice(0, 20));
    simCountEl.textContent = `${Number(data.n_sims || 0).toLocaleString()} what-if simulations`;
    whatifStatusEl.textContent = `Applied ${home} ${homeScore}:${awayScore} ${away}.`;
  } catch (err) {
    whatifStatusEl.textContent = "What-if failed. Please try again.";
    console.error(err);
  } finally {
    resimBtnEl.disabled = false;
  }
}

function downloadShareCard() {
  /* Canvas export creates a portable brag artifact without adding dependencies. */
  const top = (state.currentOdds.length ? state.currentOdds : state.baselineOdds).slice(0, 5);
  if (!top.length) {
    alert("Predictions are still loading.");
    return;
  }

  const canvas = document.createElement("canvas");
  canvas.width = 1200;
  canvas.height = 630;
  const ctx = canvas.getContext("2d");

  if (!ctx) {
    alert("Could not generate share card.");
    return;
  }

  /* Dark gradient and disciplined accents match the product identity in social posts. */
  const grad = ctx.createLinearGradient(0, 0, 1200, 630);
  grad.addColorStop(0, "#0b1020");
  grad.addColorStop(1, "#11172e");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 1200, 630);

  ctx.fillStyle = "rgba(45, 212, 191, 0.15)";
  ctx.fillRect(54, 52, 1092, 526);
  ctx.strokeStyle = "rgba(231, 194, 101, 0.65)";
  ctx.lineWidth = 3;
  ctx.strokeRect(54, 52, 1092, 526);

  ctx.fillStyle = "#e7c265";
  ctx.font = "700 52px 'Segoe UI', Arial, sans-serif";
  ctx.fillText("World Cup 2026 Oracle", 90, 130);

  ctx.fillStyle = "#93a1bd";
  ctx.font = "500 26px 'Segoe UI', Arial, sans-serif";
  ctx.fillText("Top 5 Championship Probabilities", 90, 176);

  ctx.font = "600 34px 'Segoe UI', Arial, sans-serif";
  top.forEach((row, i) => {
    const y = 244 + (i * 68);
    ctx.fillStyle = i === 0 ? "#e7c265" : "#eaf0fb";
    ctx.fillText(`${i + 1}. ${row.team}`, 100, y);
    ctx.fillStyle = i === 0 ? "#e7c265" : "#2dd4bf";
    ctx.textAlign = "right";
    ctx.fillText(pct(row.prob), 1080, y);
    ctx.textAlign = "left";
  });

  ctx.fillStyle = "#93a1bd";
  ctx.font = "500 22px 'Segoe UI', Arial, sans-serif";
  ctx.fillText("Simulated from historical Elo and bracket Monte Carlo runs.", 90, 560);

  const link = document.createElement("a");
  link.download = "world-cup-2026-oracle-card.png";
  link.href = canvas.toDataURL("image/png");
  link.click();
}

/* Live Results element references, grouped so the feature is easy to maintain. */
const liveHomeEl = document.getElementById("liveHome");
const liveAwayEl = document.getElementById("liveAway");
const liveHomeScoreEl = document.getElementById("liveHomeScore");
const liveAwayScoreEl = document.getElementById("liveAwayScore");
const liveAddBtnEl = document.getElementById("liveAddBtn");
const liveStatusEl = document.getElementById("liveStatus");
const liveResultsListEl = document.getElementById("liveResultsList");

function renderLiveResults(entries) {
  /* Show the applied real results as removable chips so users can audit what is locked in. */
  if (!entries || entries.length === 0) {
    liveResultsListEl.innerHTML =
      '<p class="muted">No live results entered yet. The model uses the source data as-is.</p>';
    return;
  }

  /* A clear-all button lets users revert to the raw dataset in one action. */
  liveResultsListEl.innerHTML = `
    <div class="live-list-head">
      <span>${entries.length} live result${entries.length > 1 ? "s" : ""} applied</span>
      <button id="liveClearBtn" type="button" class="live-clear">Clear all</button>
    </div>
    <ul class="live-chips">
      ${entries.map((e) => `
        <li class="live-chip">
          ${flagFor(e.home)}<strong>${e.home}</strong> ${e.home_score} : ${e.away_score} <strong>${e.away}</strong>${flagFor(e.away)}
        </li>
      `).join("")}
    </ul>
  `;

  /* Bind the clear button each render since the node is recreated above. */
  const clearBtn = document.getElementById("liveClearBtn");
  if (clearBtn) clearBtn.addEventListener("click", clearLiveResults);
}

async function loadLiveResults() {
  /* Fetch currently applied results on boot so the list reflects server state. */
  try {
    const res = await fetch("/api/live-results");
    if (!res.ok) return;
    const data = await res.json();
    renderLiveResults(data.live_results || []);
  } catch (err) {
    console.error(err);
  }
}

async function initAdminGate() {
  /*
    Ask the server whether live-result editing is locked behind an admin key.
    When locked, viewers see the results but the Save controls are disabled until
    the owner unlocks with the key. The key is kept only in memory for this tab,
    and also remembered in localStorage so the owner does not re-enter it each visit.
  */
  try {
    const res = await fetch("/api/admin-status");
    if (!res.ok) return;
    const { locked } = await res.json();
    state.adminLocked = !!locked;

    if (!locked) {
      // Open deployment (or local dev): editing is freely allowed.
      state.adminUnlocked = true;
      return;
    }

    // Try a remembered key so the owner stays unlocked across visits.
    const saved = localStorage.getItem("wc-admin-key") || "";
    if (saved) {
      const ok = await verifyAdminKey(saved);
      if (ok) return; // verifyAdminKey sets the unlocked state and key.
    }
    // Still locked: reflect that in the UI so viewers know it is read-only.
    applyAdminUiState();
  } catch (err) {
    console.error(err);
  }
}

async function verifyAdminKey(key) {
  /* Confirm a key with the server before trusting it for writes. */
  try {
    const res = await fetch("/api/admin-verify", {
      method: "POST",
      headers: { "X-Admin-Key": key },
    });
    const data = await res.json();
    if (data.ok) {
      state.adminKey = key;
      state.adminUnlocked = true;
      localStorage.setItem("wc-admin-key", key);
      applyAdminUiState();
      return true;
    }
    return false;
  } catch (err) {
    console.error(err);
    return false;
  }
}

function applyAdminUiState() {
  /*
    Enable or disable the Save controls based on unlock state. Viewers keep a
    clean read-only view; the owner gets the full editing controls plus an
    "unlocked" cue. An unlock button appears when locked and not yet unlocked.
  */
  const locked = state.adminLocked && !state.adminUnlocked;
  if (liveAddBtnEl) liveAddBtnEl.disabled = locked;
  if (liveHomeEl) liveHomeEl.disabled = locked;
  if (liveAwayEl) liveAwayEl.disabled = locked;
  if (liveHomeScoreEl) liveHomeScoreEl.disabled = locked;
  if (liveAwayScoreEl) liveAwayScoreEl.disabled = locked;

  // Show an unlock prompt only when editing is locked.
  let gate = document.getElementById("adminGate");
  if (locked) {
    if (!gate) {
      gate = document.createElement("div");
      gate.id = "adminGate";
      gate.className = "admin-gate";
      gate.innerHTML = `
        <span>Editing results is owner-only.</span>
        <button type="button" id="adminUnlockBtn" class="admin-unlock">Unlock</button>
      `;
      // Place the gate just above the entry controls.
      liveStatusEl.parentNode.insertBefore(gate, liveStatusEl);
      document.getElementById("adminUnlockBtn").addEventListener("click", promptAdminUnlock);
    }
  } else if (gate) {
    gate.remove();
  }
}

async function promptAdminUnlock() {
  /* Simple prompt keeps the unlock flow lightweight for a single owner. */
  const key = prompt("Enter the owner key to edit live results:");
  if (!key) return;
  const ok = await verifyAdminKey(key.trim());
  if (!ok) alert("That key was not correct.");
}

async function addLiveResult() {
  /* Saving a real score rebuilds the whole model server-side, so we reload predictions after. */
  const home = liveHomeEl.value;
  const away = liveAwayEl.value;
  const homeScore = Number(liveHomeScoreEl.value);
  const awayScore = Number(liveAwayScoreEl.value);

  if (!home || !away || home === away) {
    alert("Choose two different teams.");
    return;
  }

  liveStatusEl.innerHTML = spinner("Applying result and refreshing every prediction");
  liveAddBtnEl.disabled = true;

  try {
    const res = await fetch("/api/live-results", {
      method: "POST",
      /* Send the admin key so the server authorizes this write. */
      headers: { "Content-Type": "application/json", "X-Admin-Key": state.adminKey || "" },
      body: JSON.stringify({ home, away, home_score: homeScore, away_score: awayScore }),
    });

    if (res.status === 401) {
      liveStatusEl.textContent = "Editing is owner-only. Unlock with the owner key first.";
      return;
    }
    if (!res.ok) {
      liveStatusEl.textContent = "Could not apply that result. Check the teams.";
      return;
    }

    const data = await res.json();
    renderLiveResults(data.live_results || []);
    liveStatusEl.textContent = `Saved ${home} ${homeScore}:${awayScore} ${away}. Odds updated.`;
    /* Reload baseline predictions so the leaderboard, groups, and bracket all reflect it. */
    await loadPredictions();
  } catch (err) {
    liveStatusEl.textContent = "Something went wrong applying the result.";
    console.error(err);
  } finally {
    liveAddBtnEl.disabled = false;
  }
}

async function clearLiveResults() {
  /* Reverting removes every manual entry and rebuilds predictions from the raw dataset. */
  liveStatusEl.innerHTML = spinner("Reverting to source data");
  try {
    const res = await fetch("/api/live-results", {
      method: "DELETE",
      /* Send the admin key so the server authorizes this clear. */
      headers: { "X-Admin-Key": state.adminKey || "" },
    });
    if (res.status === 401) {
      liveStatusEl.textContent = "Editing is owner-only. Unlock with the owner key first.";
      return;
    }
    if (!res.ok) {
      liveStatusEl.textContent = "Could not clear results.";
      return;
    }
    renderLiveResults([]);
    liveStatusEl.textContent = "Cleared. Back to the source data.";
    await loadPredictions();
  } catch (err) {
    liveStatusEl.textContent = "Something went wrong clearing results.";
    console.error(err);
  }
}

function handleTeamClick(event) {
  /* Event delegation covers dynamic leaderboard and group renders efficiently. */
  const trigger = event.target.closest(".team-link");
  if (!trigger?.dataset?.team) return;
  showTeamDeepDive(trigger.dataset.team);
}

function bindEvents() {
  /* Central binding makes startup predictable and easier to audit for regressions. */
  document.getElementById("predictBtn").addEventListener("click", runH2H);
  document.getElementById("resimBtn").addEventListener("click", runWhatIf);
  shareBtnEl.addEventListener("click", downloadShareCard);

  themeToggleEl.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme") || "dark";
    applyTheme(current === "dark" ? "light" : "dark");
  });

  liveAddBtnEl.addEventListener("click", addLiveResult);

  leaderboardEl.addEventListener("click", handleTeamClick);
  groupsGridEl.addEventListener("click", handleTeamClick);
  modalBackdropEl.addEventListener("click", closeModal);
  modalCloseEl.addEventListener("click", closeModal);

  document.addEventListener("keydown", (event) => {
    /* Escape shortcut mirrors common modal UX expectations and boosts accessibility. */
    if (event.key === "Escape" && !modalEl.classList.contains("hidden")) closeModal();
  });
}

async function boot() {
  /* Boot order ensures theme applies before heavy render to reduce visual jumps. */
  initTheme();
  bindEvents();

  try {
    await loadPredictions();
    /* Load any already-applied live results so the list reflects server state on open. */
    await loadLiveResults();
    /* Determine whether editing is locked, and auto-unlock with a saved owner key. */
    await initAdminGate();
  } catch (err) {
    console.error(err);
    simCountEl.textContent = "Failed to load baseline predictions.";
  }
}

boot();
