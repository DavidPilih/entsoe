self.onInit = function() {
  const container = self.ctx.$container[0];

  const regimes = [
    { id: 1, name: "BSP", slug: "bsp", desc: "Polna moč polnjenja", color: "#2D8659" },
    { id: 2, name: "Samooskrba", slug: "samooskrba", desc: "Polna moč praznjenja", color: "#B54A3F" },
    { id: 3, name: "Rezanje konic", slug: "rezanje_konic", desc: "Znižanje konic", color: "#C97D2E" }
  ];

  const days = [
    { id: 0, key: "pon", legacyKey: "monday", name: "Ponedeljek", short: "Pon" },
    { id: 1, key: "tor", legacyKey: "tuesday", name: "Torek", short: "Tor" },
    { id: 2, key: "sre", legacyKey: "wednesday", name: "Sreda", short: "Sre" },
    { id: 3, key: "cet", legacyKey: "thursday", name: "Četrtek", short: "Čet" },
    { id: 4, key: "pet", legacyKey: "friday", name: "Petek", short: "Pet" },
    { id: 5, key: "sob", legacyKey: "saturday", name: "Sobota", short: "Sob" },
    { id: 6, key: "ned", legacyKey: "sunday", name: "Nedelja", short: "Ned" },
    { id: 7, key: "prazniki", legacyKey: "holidays", name: "Prazniki", short: "Prazniki" }
  ];

  const attributeKey = "device_energy_protocol";
  let schedule = days.map(() => null);
  let selectedRegimeId = null;
  let savedAttribute = {};
  let invalidAttribute = false;
  let editedDays = new Set();
  let collectiveLeaves = [];
  let leavesEdited = false;
  let editingLeaveIndex = null;
  let saving = false;

  function getAttributeEntry() {
    return (self.ctx.data || []).find(item => item.dataKey && item.dataKey.name === attributeKey);
  }

  function readAttribute() {
    const entry = getAttributeEntry();
    if (!entry || !entry.data || !entry.data.length) return;
    const raw = entry.data[entry.data.length - 1][1];
    try {
      const value = typeof raw === "string" ? JSON.parse(raw) : raw;
      if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Atribut ni JSON objekt.");
      savedAttribute = value;
      invalidAttribute = false;
      if (!editedDays.size && !leavesEdited && !saving) {
        const permanent = value.stalno && typeof value.stalno === "object" ? value.stalno : value;
        schedule = days.map(day => {
          const regime = regimes.find(item => item.slug === (permanent[day.key] || permanent[day.legacyKey]));
          return regime ? regime.id : null;
        });
        collectiveLeaves = readCollectiveLeaves(value);
        renderWeek();
        renderLeaves();
      }
    } catch (error) {
      invalidAttribute = true;
      console.error("Neveljaven device_energy_protocol:", error);
    }
  }

  function getDeviceInfo() {
    if (!self.ctx.datasources || !self.ctx.datasources.length) return null;
    const ds = self.ctx.datasources[0];
    return { entityId: ds.entityId, entityName: ds.entityName, entityType: ds.entityType };
  }

  function renderRegimes() {
    const list = container.querySelector("#regimeList");
    if (!list) return;

    list.innerHTML = regimes.map(r => `
      <div class="regime ${selectedRegimeId === r.id ? "active" : ""}" data-id="${r.id}">
        <div class="regime-color" style="background:${r.color}"></div>
        <div class="regime-info">
          <div class="regime-name">${r.name}</div>
          <div class="regime-meta">${r.desc}</div>
        </div>
      </div>
    `).join("");

    list.querySelectorAll(".regime").forEach(el => {
      el.addEventListener("click", () => { selectedRegimeId = Number(el.dataset.id); renderRegimes(); });
    });
  }

  function renderWeek() {
    const grid = container.querySelector("#weekGrid");
    if (!grid) return;

    grid.innerHTML = days.map(d => {
      const reg = regimes.find(r => r.id === schedule[d.id]);

      if (reg) {
        return `
          <div class="day-card">
            <div class="day-name">${d.short}</div>
            <div class="day-regime">
              <div class="assigned" data-day="${d.id}" style="background:${reg.color}18;border-left:3px solid ${reg.color}">
                <div class="assigned-name" style="color:${reg.color}">${reg.name}</div>
              </div>
            </div>
          </div>
        `;
      }

      return `
        <div class="day-card">
          <div class="day-name">${d.short}</div>
          <div class="day-regime"><div class="empty-slot" data-day="${d.id}"></div></div>
        </div>
      `;
    }).join("");

    grid.querySelectorAll(".assigned, .empty-slot").forEach(el => {
      el.addEventListener("click", () => assignToDay(Number(el.dataset.day)));
    });
  }

  function applyToRange(from, to) {
    if (!selectedRegimeId) return;
    for (let i = from; i <= to; i++) { schedule[i] = selectedRegimeId; editedDays.add(i); }
    renderWeek();
  }

  function assignToDay(dayId) {
    if (!selectedRegimeId) return;
    schedule[dayId] = selectedRegimeId;
    editedDays.add(dayId);
    renderWeek();
  }

  function clearAll() {
    schedule = days.map(() => null);
    days.forEach(day => editedDays.add(day.id));
    renderWeek();
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function formatDate(value) {
    const parts = value.split("-");
    return parts.length === 3 ? parts[2] + ". " + parts[1] + ". " + parts[0] : value;
  }

  function timestampToDate(timestamp) {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: "Europe/Ljubljana", year: "numeric", month: "2-digit", day: "2-digit"
    }).formatToParts(new Date(Number(timestamp)));
    const values = {};
    parts.forEach(part => { values[part.type] = part.value; });
    return values.year + "-" + values.month + "-" + values.day;
  }

  function dateToTimestamp(date) {
    const parts = date.split("-").map(Number);
    const noonUtc = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2], 12));
    const zone = new Intl.DateTimeFormat("en-US", {
      timeZone: "Europe/Ljubljana", timeZoneName: "shortOffset"
    }).formatToParts(noonUtc).find(part => part.type === "timeZoneName").value;
    const match = zone.match(/GMT([+-])(\d{1,2})(?::(\d{2}))?/);
    const minutes = match ? (Number(match[2]) * 60 + Number(match[3] || 0)) * (match[1] === "+" ? 1 : -1) : 0;
    return Date.UTC(parts[0], parts[1] - 1, parts[2]) - minutes * 60000;
  }

  function readCollectiveLeaves(value) {
    if (Array.isArray(value.zacasno)) {
      const groups = {};
      value.zacasno.forEach((item, index) => {
        if (!item || !regimes.some(regime => regime.slug === item.rezim)) return;
        const date = item.datum || (item.timestamp != null ? timestampToDate(item.timestamp) : null);
        if (!date) return;
        const groupId = item.skupina || "dopust_" + index;
        if (!groups[groupId]) {
          groups[groupId] = {
            groupId,
            name: item.naziv || "Kolektivni dopust",
            from: date,
            to: date,
            regime: item.rezim
          };
        } else {
          if (date < groups[groupId].from) groups[groupId].from = date;
          if (date > groups[groupId].to) groups[groupId].to = date;
        }
      });
      return Object.keys(groups).map(key => groups[key]).sort((a, b) => a.from.localeCompare(b.from));
    }

    if (Array.isArray(value.collective_leave)) {
      return value.collective_leave
        .filter(item => item && item.from && item.to && regimes.some(regime => regime.slug === item.regime))
        .map((item, index) => Object.assign({}, item, { groupId: item.groupId || "dopust_migracija_" + index }));
    }
    return [];
  }

  function expandCollectiveLeaves() {
    const result = [];
    collectiveLeaves.forEach(leave => {
      const cursor = new Date(leave.from + "T12:00:00Z");
      const end = new Date(leave.to + "T12:00:00Z");
      while (cursor <= end) {
        const date = cursor.toISOString().slice(0, 10);
        result.push({
          skupina: leave.groupId,
          naziv: leave.name,
          datum: date,
          timestamp: dateToTimestamp(date),
          rezim: leave.regime
        });
        cursor.setUTCDate(cursor.getUTCDate() + 1);
      }
    });
    return result.sort((a, b) => a.datum.localeCompare(b.datum));
  }

  function setLeaveMessage(message) {
    const el = container.querySelector("#leaveMessage");
    if (el) el.textContent = message || "";
  }

  function resetLeaveForm() {
    container.querySelector("#leaveName").value = "";
    container.querySelector("#leaveFrom").value = "";
    container.querySelector("#leaveTo").value = "";
    container.querySelector("#leaveRegime").value = "";
    container.querySelector("#btnAddLeave").textContent = "Dodaj obdobje";
    container.querySelector("#btnCancelLeave").hidden = true;
    editingLeaveIndex = null;
    setLeaveMessage("");
  }

  function renderLeaves() {
    const list = container.querySelector("#leaveList");
    if (!list) return;

    if (!collectiveLeaves.length) {
      list.innerHTML = '<div class="leave-empty">Ni dodanih obdobij.</div>';
      return;
    }

    list.innerHTML = collectiveLeaves.map((leave, index) => {
      const regime = regimes.find(item => item.slug === leave.regime);
      return `
        <div class="leave-item">
          <div class="leave-color" style="background:${regime.color}"></div>
          <div class="leave-main">
            <div class="leave-name">${escapeHtml(leave.name || "Kolektivni dopust")}</div>
            <div class="leave-dates">${formatDate(leave.from)}–${formatDate(leave.to)}</div>
          </div>
          <div class="leave-regime">${regime.name}</div>
          <button class="leave-action" data-action="edit" data-index="${index}">Uredi</button>
          <button class="leave-action leave-delete" data-action="delete" data-index="${index}">Izbriši</button>
        </div>
      `;
    }).join("");

    list.querySelectorAll(".leave-action").forEach(button => {
      button.addEventListener("click", () => {
        const index = Number(button.dataset.index);
        if (button.dataset.action === "edit") editLeave(index);
        else deleteLeave(index);
      });
    });
  }

  function editLeave(index) {
    const leave = collectiveLeaves[index];
    if (!leave) return;
    container.querySelector("#leaveName").value = leave.name || "";
    container.querySelector("#leaveFrom").value = leave.from;
    container.querySelector("#leaveTo").value = leave.to;
    container.querySelector("#leaveRegime").value = leave.regime;
    container.querySelector("#btnAddLeave").textContent = "Posodobi";
    container.querySelector("#btnCancelLeave").hidden = false;
    editingLeaveIndex = index;
    setLeaveMessage("");
  }

  function deleteLeave(index) {
    collectiveLeaves.splice(index, 1);
    leavesEdited = true;
    resetLeaveForm();
    renderLeaves();
    sendSchedule();
  }

  function saveLeaveForm() {
    const name = container.querySelector("#leaveName").value.trim();
    const from = container.querySelector("#leaveFrom").value;
    const to = container.querySelector("#leaveTo").value;
    const regime = container.querySelector("#leaveRegime").value;

    if (!from || !to || !regime) { setLeaveMessage("Izberi oba datuma in režim."); return; }
    if (from > to) { setLeaveMessage("Končni datum mora biti za začetnim datumom."); return; }

    const overlaps = collectiveLeaves.some((item, index) => index !== editingLeaveIndex && from <= item.to && to >= item.from);
    if (overlaps) { setLeaveMessage("To obdobje se prekriva z že dodanim obdobjem."); return; }

    const groupId = editingLeaveIndex === null
      ? "dopust_" + Date.now()
      : collectiveLeaves[editingLeaveIndex].groupId;
    const leave = { groupId, name: name || "Kolektivni dopust", from, to, regime };
    if (editingLeaveIndex === null) collectiveLeaves.push(leave);
    else collectiveLeaves[editingLeaveIndex] = leave;
    collectiveLeaves.sort((a, b) => a.from.localeCompare(b.from));
    leavesEdited = true;
    resetLeaveForm();
    renderLeaves();
    sendSchedule();
  }

  function sendSchedule() {
    if (saving) return;
    if (invalidAttribute) return;
    if (!getAttributeEntry()) { console.error("V datasource dodaj shared attribute device_energy_protocol."); return; }
    const device = getDeviceInfo();
    if (!device || !device.entityId) { console.error("Manjka datasource / entityId na widgetu."); return; }

    const updated = Object.assign({}, savedAttribute, { schema_version: 2, stalno: {}, zacasno: expandCollectiveLeaves() });
    days.forEach(day => {
      const regime = regimes.find(item => item.id === schedule[day.id]);
      updated.stalno[day.key] = regime ? regime.slug : null;
      delete updated[day.key];
      delete updated[day.legacyKey];
    });
    delete updated.collective_leave;

    saving = true;
    self.ctx.http.post(
      "/api/plugins/telemetry/DEVICE/" + device.entityId + "/attributes/SHARED_SCOPE",
      { [attributeKey]: updated }
    ).subscribe(
      function() {
        saving = false;
        savedAttribute = updated;
        editedDays.clear();
        leavesEdited = false;
        setLeaveMessage("Shranjeno.");
      },
      function(error) {
        saving = false;
        console.error("Napaka pri shranjevanju device_energy_protocol:", error);
        setLeaveMessage("Shranjevanje ni uspelo.");
      }
    );
  }

  function bind(id, event, handler) {
    const el = container.querySelector("#" + id);
    if (el) el.addEventListener(event, handler);
  }

  bind("btnClear", "click", clearAll);
  bind("btnMonFri", "click", () => applyToRange(0, 4));
  bind("btnSatSun", "click", () => applyToRange(5, 6));
  bind("btnAll", "click", () => applyToRange(0, 6));
  bind("btnSave", "click", sendSchedule);
  bind("btnSaveFooter", "click", sendSchedule);
  bind("btnAddLeave", "click", saveLeaveForm);
  bind("btnCancelLeave", "click", resetLeaveForm);

  const leaveRegime = container.querySelector("#leaveRegime");
  leaveRegime.innerHTML = '<option value="">Izberi režim</option>' + regimes.map(regime =>
    `<option value="${regime.slug}">${regime.name}</option>`
  ).join("");

  renderRegimes();
  renderWeek();
  renderLeaves();
  readAttribute();
  self.onDataUpdated = readAttribute;
};

self.onDestroy = function() {};
