self.onInit = function() {
  const container = self.ctx.$container[0];

  const regimes = [
    { id: 1, name: "BSP", slug: "bsp", desc: "Polna moč polnjenja", color: "#2D8659" },
    { id: 2, name: "Samooskrba", slug: "samooskrba", desc: "Polna moč praznjenja", color: "#B54A3F" },
    { id: 3, name: "Rezanje konic", slug: "rezanje_konic", desc: "Znižanje konic", color: "#C97D2E" }
  ];

  const days = [
    { id: 0, key: "monday", name: "Ponedeljek", short: "Pon" },
    { id: 1, key: "tuesday", name: "Torek", short: "Tor" },
    { id: 2, key: "wednesday", name: "Sreda", short: "Sre" },
    { id: 3, key: "thursday", name: "Četrtek", short: "Čet" },
    { id: 4, key: "friday", name: "Petek", short: "Pet" },
    { id: 5, key: "saturday", name: "Sobota", short: "Sob" },
    { id: 6, key: "sunday", name: "Nedelja", short: "Ned" }
  ];

  const attributeKey = "device_energy_protocol";
  let schedule = [null, null, null, null, null, null, null];
  let selectedRegimeId = null;
  let savedAttribute = {};
  let invalidAttribute = false;
  let editedDays = new Set();
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
      if (!editedDays.size && !saving) {
        schedule = days.map(day => {
          const regime = regimes.find(item => item.slug === value[day.key]);
          return regime ? regime.id : null;
        });
        renderWeek();
      }
    } catch (error) {
      if (!invalidAttribute) alert("Napaka: device_energy_protocol ni veljaven JSON objekt.");
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
    if (!selectedRegimeId) { alert("Najprej izberi režim na levi."); return; }
    for (let i = from; i <= to; i++) { schedule[i] = selectedRegimeId; editedDays.add(i); }
    renderWeek();
  }

  function assignToDay(dayId) {
    if (!selectedRegimeId) { alert("Najprej izberi režim na levi."); return; }
    schedule[dayId] = selectedRegimeId;
    editedDays.add(dayId);
    renderWeek();
  }

  function clearAll() {
    if (confirm("Počistim urnik?")) { schedule = [null, null, null, null, null, null, null]; days.forEach(day => editedDays.add(day.id)); renderWeek(); }
  }

  function sendSchedule() {
    if (saving) return;
    if (invalidAttribute) { alert("Napaka: neveljavnega atributa ni mogoče prepisati."); return; }
    if (!getAttributeEntry()) { alert("Napaka: v datasource dodaj shared attribute device_energy_protocol."); return; }
    const device = getDeviceInfo();
    if (!device || !device.entityId) { alert("Napaka: naprava ni določena na widgetu."); console.error("Manjka datasource / entityId na widgetu."); return; }

    const updated = Object.assign({}, savedAttribute);
    editedDays.forEach(id => {
      const day = days[id];
      const regime = regimes.find(item => item.id === schedule[id]);
      if (regime) updated[day.key] = regime.slug;
      else delete updated[day.key];
    });

    saving = true;
    self.ctx.http.post(
      "/api/plugins/telemetry/DEVICE/" + device.entityId + "/attributes/SHARED_SCOPE",
      { [attributeKey]: updated }
    ).subscribe(
      function() {
        saving = false;
        savedAttribute = updated;
        editedDays.clear();
        alert("Tedenski režimi so shranjeni.");
      },
      function(error) {
        saving = false;
        console.error("Napaka pri shranjevanju device_energy_protocol:", error);
        alert("Napaka pri shranjevanju režimov (" + error.status + ").");
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

  renderRegimes();
  renderWeek();
  readAttribute();
  self.onDataUpdated = readAttribute;
};

self.onDestroy = function() {};
