// Prosojnost podatkov desno od modre črte: 0 = nevidno, 1 = polno vidno.
var FUTURE_DATA_OPACITY = 0.55;
var FULL_BATTERY_INTERVALS = 8;
var SOC_INTERVAL_MS = 15 * 60 * 1000;

function canonicalSchedule(items) {
    var sorted = items.map(function (iv) {
        return { type: iv.type, from: Number(iv.xMin), to: Number(iv.xMax) };
    }).sort(function (a, b) { return a.type.localeCompare(b.type) || a.from - b.from; });
    var result = [];
    sorted.forEach(function (iv) {
        var previous = result[result.length - 1];
        if (previous && previous.type === iv.type && iv.from <= previous.to) previous.to = Math.max(previous.to, iv.to);
        else result.push(iv);
    });
    return result.sort(function (a, b) { return a.from - b.from || a.type.localeCompare(b.type); });
}

// Enaka oblika kot vhodni schedule_auto: [Unix čas v ms, -1 / 0 / 1].
function encodeManualSchedule(intervals, original, timeWindow) {
    var source = validTelemetryPoints(original);
    var from = source.length ? source[0][0] : Number(timeWindow && timeWindow.minTime);
    var to = source.length ? source[source.length - 1][0] + SOC_INTERVAL_MS : Number(timeWindow && timeWindow.maxTime);
    intervals.forEach(function (iv) {
        from = isFinite(from) ? Math.min(from, iv.from) : iv.from;
        to = isFinite(to) ? Math.max(to, iv.to) : iv.to;
    });
    if (!isFinite(from) || !isFinite(to) || to <= from) return [];
    var timestamps = new Set([from, to]);
    source.forEach(function (p) { timestamps.add(p[0]); });
    for (var t = from; t < to; t += SOC_INTERVAL_MS) timestamps.add(t);
    intervals.forEach(function (iv) { timestamps.add(iv.from); timestamps.add(iv.to); });
    return Array.from(timestamps).sort(function (a, b) { return a - b; }).map(function (ts) {
        var active = intervals.find(function (iv) { return iv.from <= ts && ts < iv.to; });
        return [ts, active ? (active.type === 'polnjenje' ? 1 : -1) : 0];
    });
}

function telemetryMatches(key, names) {
    return key && names.some(function (name) {
        return [key.name, key.label].some(function (value) {
            return String(value || '').trim().toLowerCase() === name;
        });
    });
}

function validTelemetryPoints(values) {
    return (values || []).filter(function (p) {
        return p && p[0] != null && p[1] != null && String(p[1]).trim() !== '' &&
            isFinite(Number(p[0])) && isFinite(Number(p[1]));
    }).map(function (p) { return [Number(p[0]), Number(p[1])]; })
        .sort(function (a, b) { return a[0] - b[0]; });
}

function mergeSocMeasurements(history, live, min, max) {
    var byTime = new Map();
    validTelemetryPoints(history).concat(validTelemetryPoints(live)).forEach(function (p) {
        if ((!isFinite(min) || p[0] >= min) && (!isFinite(max) || p[0] <= max)) byTime.set(p[0], p);
    });
    return Array.from(byTime.values()).sort(function (a, b) { return a[0] - b[0]; });
}

// Čista računska funkcija: urnik in sonce veljata do naslednje spremembe.
function calculateSocForecast(soc, sun, schedule, end, now) {
    var measured = validTelemetryPoints(soc).filter(function (p) { return p[0] <= now; });
    if (!measured.length || !(FULL_BATTERY_INTERVALS > 0)) return [];
    var last = measured[measured.length - 1];
    var time = last[0], value = Math.max(0, Math.min(100, last[1]));
    if (!(end > time)) return [];
    var solar = validTelemetryPoints(sun);
    var boundaries = [time, end];
    for (var t = (Math.floor(time / SOC_INTERVAL_MS) + 1) * SOC_INTERVAL_MS; t < end; t += SOC_INTERVAL_MS) boundaries.push(t);
    solar.forEach(function (p) { if (p[0] > time && p[0] < end) boundaries.push(p[0]); });
    schedule.forEach(function (iv) {
        [iv.xMin, iv.xMax].forEach(function (ts) { if (ts > time && ts < end) boundaries.push(ts); });
    });
    boundaries.sort(function (a, b) { return a - b; });
    var points = [{ x: time, y: value }], sunIndex = -1;
    for (var i = 1; i < boundaries.length; i++) {
        var next = boundaries[i];
        if (next <= time) continue;
        while (sunIndex + 1 < solar.length && solar[sunIndex + 1][0] <= time) sunIndex++;
        var active = schedule.find(function (iv) { return iv.xMin <= time && time < iv.xMax; });
        var rate = 0;
        if (active && active.type === 'polnjenje') {
            if (sunIndex < 0 || solar[sunIndex][1] < 0 || solar[sunIndex][1] > 100) break;
            rate = (100 / FULL_BATTERY_INTERVALS) * solar[sunIndex][1] / 100;
        } else if (active && active.type === 'praznjenje') rate = -100 / FULL_BATTERY_INTERVALS;
        var delta = rate * (next - time) / SOC_INTERVAL_MS;
        var raw = value + delta;
        // Dodamo trenutek dosežene meje, da graf pravilno pokaže plato.
        if (rate && (raw > 100 || raw < 0)) {
            var limit = raw > 100 ? 100 : 0;
            var hit = time + (limit - value) / rate * SOC_INTERVAL_MS;
            if (hit > time && hit < next) points.push({ x: hit, y: limit });
        }
        value = Math.max(0, Math.min(100, raw));
        points.push({ x: next, y: value });
        time = next;
    }
    return points;
}

// Add future percentage telemetry keys to this list (values are already 0–100).
var PERCENT_DATA_KEYS = ['sun_percent', 'sun_data', 'SOC[%]', 'soc'];

function isPercentDataKey(dataKey) {
    if (!dataKey) return false;
    var name = String(dataKey.name == null ? '' : dataKey.name).trim();
    var key = (name || String(dataKey.label || '').trim()).toLowerCase();
    return PERCENT_DATA_KEYS.some(function (entry) {
        return String(entry).trim().toLowerCase() === key;
    });
}

// Poišče vrednost na dejanskem času, ne na skupnem indeksu različnih serij.
function telemetryAtTime(dataset, time) {
    var points = dataset.data || [];
    if (!points.length || !isFinite(time) || time < Number(points[0].x) ||
        time > Number(points[points.length - 1].x)) return null;
    var low = 0, high = points.length - 1;
    while (low < high) {
        var mid = Math.ceil((low + high) / 2);
        if (Number(points[mid].x) <= time) low = mid;
        else high = mid - 1;
    }
    var a = points[low], b = points[low + 1];
    if (a.y == null || !isFinite(Number(a.y))) return null;
    var value = Number(a.y), index = low;
    if (b && time > Number(a.x)) {
        if (b.y == null || !isFinite(Number(b.y))) return null;
        var fraction = (time - Number(a.x)) / (Number(b.x) - Number(a.x));
        if (!dataset.stepped) value += (Number(b.y) - value) * fraction;
        else if (dataset.stepped === 'after' || (dataset.stepped === 'middle' && fraction >= 0.5)) value = Number(b.y);
        if (fraction > 0.5) index++;
    }
    return { value: value, index: index };
}

self.onInit = function () {
    Chart.Interaction.modes.socTimeCursor = function (chart, event) {
        var area = chart.chartArea;
        if (!area || event.x < area.left || event.x > area.right ||
            event.y < area.top || event.y > area.bottom) return [];
        var time = chart.scales.x.getValueForPixel(event.x);
        chart._tooltipTime = time;
        var selected = [];
        chart.data.datasets.forEach(function (dataset, datasetIndex) {
            if (!chart.isDatasetVisible(datasetIndex)) return;
            var sample = telemetryAtTime(dataset, time);
            if (!sample) return;
            var element = chart.getDatasetMeta(datasetIndex).data[sample.index];
            if (element) selected.push({ element: element, datasetIndex: datasetIndex, index: sample.index });
        });
        return selected;
    };
    var ctx = self.ctx;
    var container = ctx.$container[0];
    container.innerHTML =
        '<div style="display:flex; flex-direction:column; height:100%; gap:6px;">' +
        '  <div style="display:flex; align-items:center;">' +
        '    <button id="saveSchedule" type="button" style="display:none; background:#1976d2; color:white; border:0; border-radius:4px; padding:6px 14px; cursor:pointer;">Shrani</button>' +
        '    <span id="saveScheduleStatus" role="status" style="font-size:12px; margin-left:8px;"></span>' +
        '    <span id="rangeLabel" style="font-size:12px; color:#555; margin-left:auto;"></span>' +
        '  </div>' +
        '  <div id="chartWrap" style="position:relative; flex:1; min-height:0;">' +
        '    <canvas id="myChart" style="width:100%; height:100%;"></canvas>' +
        '  </div>' +
        '  <div id="trackWrap" style="position:relative; height:96px; flex-shrink:0;">' +
        '    <canvas id="scheduleTrack" style="width:100%; height:100%; touch-action:none;"></canvas>' +
        '    <div id="dragTooltip" style="position:absolute; display:none; pointer-events:none; background:rgba(0,0,0,0.75); color:white; padding:4px 8px; border-radius:4px; font-size:12px; white-space:nowrap; z-index:10;"></div>' +
        '  </div>' +
        '</div>';

    var canvasElement = document.getElementById('myChart');
    var trackCanvas    = document.getElementById('scheduleTrack');
    var trackWrap      = document.getElementById('trackWrap');
    var rangeLabel     = document.getElementById('rangeLabel');
    var dragTooltip    = document.getElementById('dragTooltip');
    var trackCtx       = trackCanvas.getContext('2d');
    var saveButton = container.querySelector('#saveSchedule');
    var saveStatus = container.querySelector('#saveScheduleStatus');

    var hoverTime = null;

    function drawHoverTimeLine(drawingCtx, activeChart, top, bottom) {
        var axis = activeChart.scales.x;
        var area = activeChart.chartArea;
        if (hoverTime === null || !axis || !area) return;
        var x = axis.getPixelForValue(hoverTime);
        if (!isFinite(x) || x < area.left || x > area.right) return;
        drawingCtx.save();
        drawingCtx.beginPath();
        drawingCtx.rect(area.left, top, area.right - area.left, bottom - top);
        drawingCtx.clip();
        drawingCtx.beginPath();
        drawingCtx.setLineDash([5, 4]);
        drawingCtx.strokeStyle = '#808080';
        drawingCtx.lineWidth = 1.5;
        drawingCtx.moveTo(x, top);
        drawingCtx.lineTo(x, bottom);
        drawingCtx.stroke();
        drawingCtx.restore();
    }

    // Modra oznaka trenutnega časa; vidna samo znotraj prikazanega obdobja.
    function drawCurrentTimeLine(drawingCtx, activeChart, top, bottom) {
        var axis = activeChart.scales.x;
        if (!axis || !activeChart.chartArea) return;
        var now = Date.now();
        if (now < axis.min || now > axis.max) return;
        var x = axis.getPixelForValue(now);
        if (!isFinite(x)) return;
        drawingCtx.save();
        drawingCtx.beginPath();
        drawingCtx.rect(activeChart.chartArea.left, top,
            activeChart.chartArea.right - activeChart.chartArea.left, bottom - top);
        drawingCtx.clip();
        drawingCtx.beginPath();
        drawingCtx.setLineDash([]);
        drawingCtx.strokeStyle = '#2196f3';
        drawingCtx.lineWidth = 2;
        drawingCtx.moveTo(x, top);
        drawingCtx.lineTo(x, bottom);
        drawingCtx.stroke();
        drawingCtx.restore();
    }
    self.ctx.myChart = new Chart(canvasElement, {
        type: 'line',
        data: { datasets: [] },
        plugins: [{
            id: 'futureDataOpacity',
            beforeDatasetsDraw: function (activeChart) {
                // Ista časovna meja za vse podatkovne serije v tem izrisu.
                activeChart._futureOpacityNow = Date.now();
            },
            beforeDatasetDraw: function (activeChart) {
                var area = activeChart.chartArea;
                var axis = activeChart.scales.x;
                var split = axis.getPixelForValue(activeChart._futureOpacityNow);
                if (!isFinite(split)) split = area.right;
                split = Math.max(area.left, Math.min(area.right, split));
                activeChart._futureOpacitySplit = split;

                // Običajni izris omejimo na preteklost.
                var c = activeChart.ctx;
                c.save();
                c.beginPath();
                c.rect(area.left, area.top, split - area.left, area.bottom - area.top);
                c.clip();
            },
            afterDatasetDraw: function (activeChart, args) {
                var c = activeChart.ctx;
                c.restore();
                var area = activeChart.chartArea;
                var split = activeChart._futureOpacitySplit;
                if (split >= area.right) return;

                // Prihodnost izrišemo posebej s prosojnostjo. Rez je točno
                // pri trenutnem času, tudi sredi stopničastega odseka.
                c.save();
                try {
                    c.beginPath();
                    c.rect(split, area.top, area.right - split, area.bottom - area.top);
                    c.clip();
                    c.globalAlpha *= Math.max(0, Math.min(1, FUTURE_DATA_OPACITY));
                    args.meta.controller.draw();
                } finally {
                    c.restore();
                }
            }
        }, {
            id: 'currentTimeLine',
            afterDraw: function (activeChart) {
                var area = activeChart.chartArea;
                if (area) {
                    drawHoverTimeLine(activeChart.ctx, activeChart, area.top, area.bottom);
                    drawCurrentTimeLine(activeChart.ctx, activeChart, area.top, area.bottom);
                }
            }
        }],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'socTimeCursor', intersect: false },
            scales: {
                x: {
                    type: 'time',
                    time: { unit: 'minute', displayFormats: { minute: 'HH:mm' } },
                    ticks: { maxRotation: 0 },
                    afterBuildTicks: function (axis) {
                        var rangeMs = axis.max - axis.min;
                        var pxWidth = axis.width || (axis.chart && axis.chart.width) || 600;
                        var minPxPerTick = 70;
                        var maxTicks = Math.max(2, Math.floor(pxWidth / minPxPerTick));
                        var niceStepsMin = [15, 30, 45, 60, 90, 120, 180, 240, 360, 480, 720, 1440];
                        var stepMin = niceStepsMin[niceStepsMin.length - 1];
                        for (var i = 0; i < niceStepsMin.length; i++) {
                            if (rangeMs / (niceStepsMin[i] * 60000) <= maxTicks) { stepMin = niceStepsMin[i]; break; }
                        }
                        var STEP = stepMin * 60 * 1000;
                        var start = Math.ceil(axis.min / STEP) * STEP;
                        var ticks = [];
                        for (var t = start; t <= axis.max; t += STEP) ticks.push({ value: t });
                        axis.ticks = ticks;
                    }
                },
                y: {
                    position: 'left',
                    beginAtZero: false,
                    title: { display: true, text: 'Cena' }
                },
                yPercent: {
                    type: 'linear',
                    position: 'right',
                    min: 0,
                    max: 100,
                    title: { display: true, text: 'Odstotki' },
                    ticks: {
                        stepSize: 20,
                        callback: function (value) { return value + ' %'; }
                    },
                    grid: { drawOnChartArea: false }
                }
            },
            plugins: {
                tooltip: {
                    mode: 'socTimeCursor',
                    intersect: false,
                    callbacks: {
                        title: function (items) {
                            if (!items.length) return '';
                            return new Date(items[0].chart._tooltipTime).toLocaleString('sl-SI');
                        },
                        label: function (context) {
                            var label = context.dataset.label || '';
                            var sample = telemetryAtTime(context.dataset, context.chart._tooltipTime);
                            var value = sample ? sample.value.toLocaleString('sl-SI', { maximumFractionDigits: 2 }) : '–';
                            return (label ? label + ': ' : '') + value +
                                (context.dataset.yAxisID === 'yPercent' ? ' %' : '');
                        }
                    }
                }
            }
        }
    });

    var chart = self.ctx.myChart;
    var liveSoc = [];
    var liveSubscription = null;
    var liveRequest = null;
    var liveStarting = false;
    var disposed = false;

    function acceptLiveSoc(entries) {
        if (disposed) return;
        var entry = (entries || []).find(function (d) { return telemetryMatches(d.dataKey, ['soc[%]', 'soc']); });
        if (!entry) return;
        var min = ctx.timeWindow && ctx.timeWindow.minTime;
        liveSoc = mergeSocMeasurements(liveSoc, entry.data, min == null ? -Infinity : Number(min), Infinity);
        if (ctx._renderTrack) ctx._renderTrack();
    }
    ctx._acceptLiveSoc = acceptLiveSoc;
    ctx._ensureLiveSoc = function () {
        if (disposed || liveStarting || liveSubscription || !ctx.subscriptionApi) return;
        var source = (ctx.datasources || []).find(function (ds) {
            return (ds.dataKeys || []).some(function (key) { return telemetryMatches(key, ['soc[%]', 'soc']); });
        });
        if (!source) return;
        var copy = Object.assign({}, source);
        copy.dataKeys = source.dataKeys.filter(function (key) { return telemetryMatches(key, ['soc[%]', 'soc']); })
            .map(function (key) { return Object.assign({}, key); });
        copy.latestDataKeys = [];
        liveStarting = true;
        liveRequest = ctx.subscriptionApi.createSubscription({
            type: 'latest', datasources: [copy],
            callbacks: {
                onDataUpdated: function (subscription) { acceptLiveSoc(subscription.data); },
                onDataUpdateError: function (subscription, error) { console.error('SOC live update:', error); }
            }
        }, true).subscribe(function (subscription) {
            liveStarting = false;
            if (disposed) { ctx.subscriptionApi.removeSubscription(subscription.id); return; }
            liveSubscription = subscription;
            acceptLiveSoc(subscription.data);
        }, function (error) {
            liveStarting = false;
            console.error('SOC live subscription:', error);
        });
    };
    ctx._destroyLiveSoc = function () {
        disposed = true;
        if (liveRequest) liveRequest.unsubscribe();
        if (liveSubscription) ctx.subscriptionApi.removeSubscription(liveSubscription.id);
        ctx._acceptLiveSoc = null;
        ctx._ensureLiveSoc = null;
    };

    function updateSocForecast() {
        var data = ctx.data || [];
        var soc = data.find(function (d) { return telemetryMatches(d.dataKey, ['soc[%]', 'soc']); });
        var sun = data.find(function (d) { return telemetryMatches(d.dataKey, ['sun_data', 'sun_percent']); });
        var end = ctx.timeWindow && Number(ctx.timeWindow.maxTime);
        if (end == null || !isFinite(end)) {
            end = -Infinity;
            data.forEach(function (d) {
                validTelemetryPoints(d.data).forEach(function (p) { end = Math.max(end, p[0]); });
            });
        }
        var now = Date.now();
        var min = ctx.timeWindow && ctx.timeWindow.minTime;
        var measurements = mergeSocMeasurements(soc && soc.data, liveSoc,
            min == null ? -Infinity : Number(min), Math.min(now, end));
        var points = calculateSocForecast(measurements, sun && sun.data, intervals, end, now);
        var socSeries = chart.data.datasets.find(function (d) { return d._socSeries; });
        if (socSeries) {
            // Vedno izhajamo iz meritev, da se napoved pri ponovnem izrisu ne podvaja.
            var measured = measurements
                .filter(function (p) { return p[0] <= now; })
                .map(function (p) { return { x: p[0], y: p[1] }; });
            // Prva napovedana točka je zadnja meritev, zato je ne dodamo dvakrat.
            socSeries.data = measured.concat(points.slice(1));
            socSeries.stepped = false;
            socSeries.borderDash = [];
            socSeries.yAxisID = 'yPercent';
        }
        chart.update('none');
    }

    // ========== STATE ==========
    var intervals = [];
    var savedSchedule = '[]';
    var savingSchedule = false;
    var saveRequest = null;
    ctx._scheduleEdited = false;
    ctx._scheduleLocalOverride = false;

    function syncSaveButton() {
        ctx._scheduleEdited = JSON.stringify(canonicalSchedule(intervals)) !== savedSchedule;
        saveButton.style.display = ctx._scheduleEdited || savingSchedule ? 'inline-block' : 'none';
        saveButton.disabled = savingSchedule || isDragging || isMoving || isResizing;
        saveButton.textContent = savingSchedule ? 'Shranjujem …' : 'Shrani';
    }
    function getScheduleDevice() {
        var entry = (ctx.data || []).find(function (d) { return telemetryMatches(d.dataKey, ['schedule_auto']); });
        var source = entry && entry.datasource;
        if (!source || !source.entityId) source = (ctx.datasources || []).find(function (ds) {
            return ds.entityId && (ds.dataKeys || []).some(function (key) { return telemetryMatches(key, ['schedule_auto']); });
        });
        if (!source) return null;
        var entity = source.entityId;
        var type = typeof entity === 'object' ? entity.entityType : source.entityType;
        if (type && type !== 'DEVICE') return null;
        return { entityId: typeof entity === 'object' ? entity.id : entity, entityName: source.entityName || source.name || '' };
    }
    function saveSchedule() {
        syncSaveButton();
        if (!ctx._scheduleEdited || saveButton.disabled) return;
        var device = getScheduleDevice();
        if (!device || !device.entityId) {
            saveStatus.textContent = 'Napaka: naprava za schedule_auto ni določena na widgetu.';
            return;
        }
        var snapshot = canonicalSchedule(intervals);
        var source = (ctx.data || []).find(function (d) { return telemetryMatches(d.dataKey, ['schedule_auto']); });
        var schedule = encodeManualSchedule(snapshot, source && source.data, ctx.timeWindow);
        if (!schedule.length) {
            saveStatus.textContent = 'Napaka: časovno obdobje urnika ni določeno.';
            return;
        }
        var payload = { message: schedule, sender: 'schedule_manual', deviceName: device.entityName };
        savingSchedule = true;
        ctx._savingSchedule = true;
        saveStatus.textContent = '';
        syncSaveButton();
        function finish(error) {
            savingSchedule = false;
            ctx._savingSchedule = false;
            if (disposed) return;
            if (error) saveStatus.textContent = error;
            else {
                savedSchedule = JSON.stringify(snapshot);
                ctx._scheduleLocalOverride = true;
                saveStatus.textContent = 'Rule Engine je potrdil urnik.';
            }
            syncSaveButton();
        }
        try {
            saveRequest = self.ctx.http.post(
                '/api/rule-engine/DEVICE/' + encodeURIComponent(device.entityId) + '/15000', payload
            ).subscribe(function (response) {
                if (!response || response.success !== true) {
                    finish('Urnik ni bil potrjen. Spremembe so ohranjene.');
                    return;
                }
                finish(null);
            }, function (error) {
                finish('Napaka pri pošiljanju (' + error.status + '). Spremembe so ohranjene.');
            });
        } catch (error) {
            finish('Napaka pri pošiljanju. Spremembe so ohranjene.');
        }
    }
    saveButton.addEventListener('click', saveSchedule);
    ctx._destroyScheduleSave = function () {
        saveButton.removeEventListener('click', saveSchedule);
        if (saveRequest) saveRequest.unsubscribe();
    };
    var idCounter = 0;
    var isDragging = false;
    var startX = null;
    var draftType = null;
    var ctrlHeld = false;
    var createPointerId = null;
    var isResizing = false;
    var resizeIntervalId = null;
    var resizeEdge = null;
    var resizePointerId = null;
    var isMoving = false;
    var moveIntervalId = null;
    var movePointerId = null;
    var moveStartX = null;
    var moveOrigMin = null;
    var moveOrigMax = null;
    var draft = null;
    var EDGE_PX = 10;
    var STEP_MS = 15 * 60 * 1000;
    var COLORS = {
        polnjenje:  { bg: 'rgba(76, 175, 80, 0.35)',  border: 'rgba(76, 175, 80, 0.95)' },
        praznjenje: { bg: 'rgba(244, 67, 54, 0.35)',  border: 'rgba(244, 67, 54, 0.95)' }
    };

    // ========== TRACK LAYOUT ==========
    var LANE_H = 34, LANE_GAP = 6, PAD_TOP = 8;
    function laneRect(type) {
        var y0 = (type === 'polnjenje') ? PAD_TOP : PAD_TOP + LANE_H + LANE_GAP;
        return { y0: y0, y1: y0 + LANE_H };
    }
    // Kateremu "lanu" pripada dana y-koordinata (z malo tolerance med lanoma)
    function typeAtY(yPixel) {
        var top = laneRect('polnjenje');
        var bot = laneRect('praznjenje');
        var mid = (top.y1 + bot.y0) / 2;
        if (yPixel < PAD_TOP - 4 || yPixel > bot.y1 + 4) return null;
        return (yPixel < mid) ? 'polnjenje' : 'praznjenje';
    }

    // ========== HELPERS ==========
    function snapToStep(ts) { return Math.round(ts / STEP_MS) * STEP_MS; }
    function formatTime(ts) {
        var d = new Date(ts);
        return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
    }
    function formatDuration(from, to) {
        var mins = Math.round((to - from) / 60000);
        var h = Math.floor(mins / 60);
        var m = mins % 60;
        return (h > 0 ? h + 'h ' : '') + m + 'min';
    }

    // ========== INTERVAL LOGIC (nespremenjeno) ==========
    function subtractRange(interval, cutMin, cutMax) {
        var min = interval.xMin, max = interval.xMax;
        if (cutMax <= min || cutMin >= max) return [interval];
        var pieces = [];
        if (cutMin > min) pieces.push({ id: 'iv_' + (idCounter++), type: interval.type, xMin: min, xMax: cutMin });
        if (cutMax < max) pieces.push({ id: 'iv_' + (idCounter++), type: interval.type, xMin: cutMax, xMax: max });
        return pieces;
    }
    function mergeOverlappingOrTouching(xMin, xMax, candidates) {
        var mergedMin = xMin, mergedMax = xMax;
        var pool = candidates.slice();
        var changed = true;
        while (changed) {
            changed = false;
            for (var i = pool.length - 1; i >= 0; i--) {
                var iv = pool[i];
                if (iv.xMax >= mergedMin && iv.xMin <= mergedMax) {
                    mergedMin = Math.min(mergedMin, iv.xMin);
                    mergedMax = Math.max(mergedMax, iv.xMax);
                    pool.splice(i, 1);
                    changed = true;
                }
            }
        }
        return { xMin: mergedMin, xMax: mergedMax, remaining: pool };
    }
    function addInterval(xMin, xMax, type, addAsNew) {
        if (xMax <= xMin) return;
        ctx._scheduleEdited = true;
        var sameType  = intervals.filter(function (iv) { return iv.type === type; });
        var otherType = intervals.filter(function (iv) { return iv.type !== type; });
        if (!addAsNew) {
            var newIntervals = [];
            for (var i = 0; i < otherType.length; i++) newIntervals = newIntervals.concat(subtractRange(otherType[i], xMin, xMax));
            newIntervals.push({ id: 'iv_' + (idCounter++), type: type, xMin: xMin, xMax: xMax });
            intervals = newIntervals;
        } else {
            var cutOthers = [];
            for (var j = 0; j < otherType.length; j++) cutOthers = cutOthers.concat(subtractRange(otherType[j], xMin, xMax));
            var merged = mergeOverlappingOrTouching(xMin, xMax, sameType);
            intervals = cutOthers.concat(merged.remaining);
            intervals.push({ id: 'iv_' + (idCounter++), type: type, xMin: merged.xMin, xMax: merged.xMax });
        }
        renderTrack();
    }

    // ========== RISANJE TRAKU ==========
    function setupTrackCanvas() {
        var dpr = window.devicePixelRatio || 1;
        var w = trackCanvas.clientWidth, h = trackCanvas.clientHeight;
        trackCanvas.width = Math.max(1, Math.round(w * dpr));
        trackCanvas.height = Math.max(1, Math.round(h * dpr));
        trackCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function roundRect(c, x, y, w, h, r) {
        c.beginPath();
        c.moveTo(x + r, y);
        c.arcTo(x + w, y, x + w, y + h, r);
        c.arcTo(x + w, y + h, x, y + h, r);
        c.arcTo(x, y + h, x, y, r);
        c.arcTo(x, y, x + w, y, r);
        c.closePath();
    }

    function renderTrack(hoverOnly) {
        syncSaveButton();
        if (hoverOnly !== true) updateSocForecast();
        var w = trackCanvas.clientWidth, h = trackCanvas.clientHeight;
        trackCtx.clearRect(0, 0, w, h);

        var area = chart.chartArea;
        if (!area) return;
        var left = area.left, right = area.right;

        // vertikalne mrežne črte (poravnane z osjo grafa zgoraj)
        var ticks = (chart.scales.x && chart.scales.x.ticks) || [];
        trackCtx.strokeStyle = 'rgba(0,0,0,0.08)';
        trackCtx.setLineDash([3, 3]);
        trackCtx.lineWidth = 1;
        for (var i = 0; i < ticks.length; i++) {
            var xp = chart.scales.x.getPixelForValue(ticks[i].value);
            trackCtx.beginPath();
            trackCtx.moveTo(xp, 0);
            trackCtx.lineTo(xp, h);
            trackCtx.stroke();
        }
        trackCtx.setLineDash([]);

        // ozadja vrstic + oznake v levem robu
        ['polnjenje', 'praznjenje'].forEach(function (type) {
            var lane = laneRect(type);
            trackCtx.fillStyle = '#fafafa';
            trackCtx.strokeStyle = '#e0e0e0';
            roundRect(trackCtx, left, lane.y0, right - left, lane.y1 - lane.y0, 4);
            trackCtx.fill();
            trackCtx.stroke();

            trackCtx.fillStyle = type === 'polnjenje' ? '#2e7d32' : '#b71c1c';
            trackCtx.font = '11px sans-serif';
            trackCtx.textAlign = 'right';
            trackCtx.textBaseline = 'middle';
            var label = type === 'polnjenje' ? '🟢 Polnjenje' : '🔴 Praznjenje';
            trackCtx.fillText(label, Math.max(6, left - 6), (lane.y0 + lane.y1) / 2);
        });
        trackCtx.textAlign = 'left';

        function drawBlock(iv, dashed) {
            var lane = laneRect(iv.type);
            var col = COLORS[iv.type];
            var x0 = Math.max(left, chart.scales.x.getPixelForValue(iv.xMin));
            var x1 = Math.min(right, chart.scales.x.getPixelForValue(iv.xMax));
            if (x1 <= x0) return;
            trackCtx.fillStyle = col.bg;
            trackCtx.strokeStyle = col.border;
            trackCtx.lineWidth = 1.5;
            if (dashed) trackCtx.setLineDash([4, 3]);
            roundRect(trackCtx, x0, lane.y0, x1 - x0, lane.y1 - lane.y0, 4);
            trackCtx.fill();
            trackCtx.stroke();
            trackCtx.setLineDash([]);
        }
        for (var k = 0; k < intervals.length; k++) drawBlock(intervals[k], false);
        if (draft) drawBlock({ type: draft.type, xMin: draft.xMin, xMax: draft.xMax }, true);

        drawHoverTimeLine(trackCtx, chart, 0, h);
        drawCurrentTimeLine(trackCtx, chart, 0, h);

        self.ctx.intervals = intervals.map(function (iv) {
            return { type: iv.type, from: iv.xMin, to: iv.xMax };
        });
    }

    // ========== NALAGANJE IZ SCHEDULE ==========
    function loadIntervalsFromSchedule(dataArr) {
        var scheduleData = null;
        for (var i = 0; i < dataArr.length; i++) {
            var d = dataArr[i];
            if (telemetryMatches(d.dataKey, ['schedule_auto'])) {
                scheduleData = d.data;
                break;
            }
        }
        if (!scheduleData || scheduleData.length === 0) { console.log('Ni schedule podatkov'); return; }

        intervals = [];
        idCounter = 0;
        var currentType = null, currentStart = null;

        for (var i = 0; i < scheduleData.length; i++) {
            var ts = scheduleData[i][0];
            var val = Number(scheduleData[i][1]);
            var type = null;
            if (val === 1) type = 'polnjenje';
            else if (val === -1) type = 'praznjenje';

            if (type !== currentType) {
                if (currentType !== null && currentStart !== null) {
                    intervals.push({ id: 'iv_' + (idCounter++), type: currentType, xMin: currentStart, xMax: ts });
                }
                currentType = type;
                currentStart = (type !== null) ? ts : null;
            }
        }
        if (currentType !== null && currentStart !== null) {
            var lastTs = scheduleData[scheduleData.length - 1][0];
            intervals.push({ id: 'iv_' + (idCounter++), type: currentType, xMin: currentStart, xMax: lastTs + STEP_MS });
        }
        savedSchedule = JSON.stringify(canonicalSchedule(intervals));
        renderTrack();
    }
    self.ctx.loadIntervalsFromSchedule = loadIntervalsFromSchedule;

    // ========== HIT TESTING (na traku, upošteva tudi lano/y) ==========
    function findEdgeAtPixel(xPixel, yPixel) {
        var best = null, bestDist = EDGE_PX;
        for (var i = 0; i < intervals.length; i++) {
            var iv = intervals[i];
            var lane = laneRect(iv.type);
            if (yPixel < lane.y0 - 4 || yPixel > lane.y1 + 4) continue;
            var minPx = chart.scales.x.getPixelForValue(iv.xMin);
            var maxPx = chart.scales.x.getPixelForValue(iv.xMax);
            var dMin = Math.abs(xPixel - minPx);
            var dMax = Math.abs(xPixel - maxPx);
            if (dMin <= bestDist) { bestDist = dMin; best = { interval: iv, edge: 'min' }; }
            if (dMax <= bestDist) { bestDist = dMax; best = { interval: iv, edge: 'max' }; }
        }
        return best;
    }
    function findIntervalAtPixel(xPixel, yPixel) {
        for (var i = 0; i < intervals.length; i++) {
            var iv = intervals[i];
            var lane = laneRect(iv.type);
            if (yPixel < lane.y0 - 4 || yPixel > lane.y1 + 4) continue;
            var minPx = chart.scales.x.getPixelForValue(iv.xMin);
            var maxPx = chart.scales.x.getPixelForValue(iv.xMax);
            if (xPixel >= minPx && xPixel <= maxPx) return iv;
        }
        return null;
    }

    // ========== DOGODKI (samo na traku, levi gumb) ==========
    trackCanvas.addEventListener('contextmenu', function (e) { e.preventDefault(); });

    trackCanvas.addEventListener('pointerdown', function (e) {
        if (e.button !== 0) return;
        var rect = trackCanvas.getBoundingClientRect();
        var xPixel = e.clientX - rect.left;
        var yPixel = e.clientY - rect.top;

        var edge = findEdgeAtPixel(xPixel, yPixel);
        if (edge) {
            e.preventDefault();
            isResizing = true;
            resizePointerId = e.pointerId;
            trackCanvas.setPointerCapture(e.pointerId);
            resizeIntervalId = edge.interval.id;
            resizeEdge = edge.edge;
            dragTooltip.style.display = 'block';
            updateResizeDraft(e);
            return;
        }
        var hit = findIntervalAtPixel(xPixel, yPixel);
        if (hit) {
            e.preventDefault();
            isMoving = true;
            movePointerId = e.pointerId;
            trackCanvas.setPointerCapture(e.pointerId);
            moveIntervalId = hit.id;
            moveStartX = snapToStep(chart.scales.x.getValueForPixel(xPixel));
            moveOrigMin = hit.xMin;
            moveOrigMax = hit.xMax;
            dragTooltip.style.display = 'block';
            updateMoveDraft(e);
            return;
        }

        var area = chart.chartArea;
        if (!area || xPixel < area.left || xPixel > area.right) return;

        // Tip se določi kar glede na to, v kateri lan (zgornji/spodnji) si kliknil
        var type = typeAtY(yPixel);
        if (!type) return;

        e.preventDefault();
        isDragging = true;
        draftType = type;
        createPointerId = e.pointerId;
        trackCanvas.setPointerCapture(e.pointerId);
        ctrlHeld = e.ctrlKey || e.metaKey;
        startX = snapToStep(chart.scales.x.getValueForPixel(xPixel));
        dragTooltip.style.display = 'block';
        updateCreateDraft(e);
    });

    trackCanvas.addEventListener('pointermove', function (e) {
        if (isDragging || isResizing || isMoving) return;
        var rect = trackCanvas.getBoundingClientRect();
        var xPixel = e.clientX - rect.left;
        var yPixel = e.clientY - rect.top;
        if (findEdgeAtPixel(xPixel, yPixel)) trackCanvas.style.cursor = 'ew-resize';
        else if (findIntervalAtPixel(xPixel, yPixel)) trackCanvas.style.cursor = 'grab';
        else if (typeAtY(yPixel)) trackCanvas.style.cursor = 'crosshair';
        else trackCanvas.style.cursor = 'default';
    });

    function showTooltip(e, fromTs, toTs, type) {
        var wrapRect = trackWrap.getBoundingClientRect();
        dragTooltip.style.left = (e.clientX - wrapRect.left + 12) + 'px';
        dragTooltip.style.top  = (e.clientY - wrapRect.top - 30) + 'px';
        var text = formatTime(fromTs) + ' – ' + formatTime(toTs) + ' (' + formatDuration(fromTs, toTs) + ')';
        dragTooltip.textContent = text;
        rangeLabel.textContent = (type === 'polnjenje' ? '🟢 ' : '🔴 ') + text;
    }

    function updateCreateDraft(e) {
        var rect = trackCanvas.getBoundingClientRect();
        var xPixel = Math.max(0, Math.min(e.clientX - rect.left, rect.width));
        var currentX = snapToStep(chart.scales.x.getValueForPixel(xPixel));
        var fromTs = Math.min(startX, currentX);
        var toTs   = Math.max(startX, currentX);
        if (toTs === fromTs) toTs = fromTs + STEP_MS;
        draft = { xMin: fromTs, xMax: toTs, type: draftType, ctrl: ctrlHeld };
        renderTrack();
        showTooltip(e, fromTs, toTs, draftType);
    }
    function updateResizeDraft(e) {
        ctx._scheduleEdited = true;
        var target = intervals.find(function (iv) { return iv.id === resizeIntervalId; });
        if (!target) { isResizing = false; return; }
        var rect = trackCanvas.getBoundingClientRect();
        var snapped = snapToStep(chart.scales.x.getValueForPixel(Math.max(0, Math.min(e.clientX - rect.left, rect.width))));
        var newMin = target.xMin, newMax = target.xMax;
        if (resizeEdge === 'min') newMin = Math.min(snapped, target.xMax - STEP_MS);
        else newMax = Math.max(snapped, target.xMin + STEP_MS);
        target.xMin = newMin; target.xMax = newMax;
        target._newMin = newMin; target._newMax = newMax;
        renderTrack();
        showTooltip(e, newMin, newMax, target.type);
    }
    function updateMoveDraft(e) {
        ctx._scheduleEdited = true;
        var target = intervals.find(function (iv) { return iv.id === moveIntervalId; });
        if (!target) { isMoving = false; return; }
        var rect = trackCanvas.getBoundingClientRect();
        var currentX = snapToStep(chart.scales.x.getValueForPixel(Math.max(0, Math.min(e.clientX - rect.left, rect.width))));
        var delta = currentX - moveStartX;
        var newMin = moveOrigMin + delta;
        var newMax = moveOrigMax + delta;
        target.xMin = newMin; target.xMax = newMax;
        target._newMin = newMin; target._newMax = newMax;
        renderTrack();
        showTooltip(e, newMin, newMax, target.type);
    }

    trackCanvas.addEventListener('pointermove', function (e) {
        if (isResizing && e.pointerId === resizePointerId) { updateResizeDraft(e); return; }
        if (isMoving && e.pointerId === movePointerId) { updateMoveDraft(e); return; }
        if (isDragging && e.pointerId === createPointerId) {
            ctrlHeld = e.ctrlKey || e.metaKey;
            updateCreateDraft(e);
        }
    });

    function finishCreate() {
        isDragging = false;
        dragTooltip.style.display = 'none';
        rangeLabel.textContent = '';
        if (draft) { var d = draft; draft = null; addInterval(d.xMin, d.xMax, d.type, d.ctrl); }
        else renderTrack();
        startX = null; createPointerId = null; draftType = null;
    }
    function finalizeEdit(intervalId) {
        var target = intervals.find(function (iv) { return iv.id === intervalId; });
        if (target && target._newMin !== undefined) {
            var newMin = target._newMin, newMax = target._newMax;
            delete target._newMin; delete target._newMax;
            var others = intervals.filter(function (iv) { return iv.id !== target.id; });
            var sameType  = others.filter(function (iv) { return iv.type === target.type; });
            var otherType = others.filter(function (iv) { return iv.type !== target.type; });
            var cutOthers = [];
            for (var i = 0; i < otherType.length; i++) cutOthers = cutOthers.concat(subtractRange(otherType[i], newMin, newMax));
            var merged = mergeOverlappingOrTouching(newMin, newMax, sameType);
            intervals = cutOthers.concat(merged.remaining);
            intervals.push({ id: target.id, type: target.type, xMin: merged.xMin, xMax: merged.xMax });
            renderTrack();
        }
    }
    function finishResize() {
        isResizing = false;
        dragTooltip.style.display = 'none';
        rangeLabel.textContent = '';
        finalizeEdit(resizeIntervalId);
        resizeIntervalId = null; resizeEdge = null; resizePointerId = null;
    }
    function finishMove() {
        isMoving = false;
        dragTooltip.style.display = 'none';
        rangeLabel.textContent = '';
        finalizeEdit(moveIntervalId);
        moveIntervalId = null; movePointerId = null; moveStartX = null; moveOrigMin = null; moveOrigMax = null;
    }

    trackCanvas.addEventListener('pointerup', function (e) {
        if (isResizing && e.pointerId === resizePointerId) finishResize();
        else if (isMoving && e.pointerId === movePointerId) finishMove();
        else if (isDragging && e.pointerId === createPointerId) finishCreate();
    });
    trackCanvas.addEventListener('pointercancel', function (e) {
        if (isResizing && e.pointerId === resizePointerId) finishResize();
        else if (isMoving && e.pointerId === movePointerId) finishMove();
        else if (isDragging && e.pointerId === createPointerId) finishCreate();
    });

    // Skupna oznaka časa pod miško na grafu in urniku.
    function moveHoverLine(e) {
        var rect = e.currentTarget.getBoundingClientRect();
        var area = chart.chartArea;
        if (!area || !chart.scales.x || !rect.width) return;
        var x = (e.clientX - rect.left) * chart.width / rect.width;
        hoverTime = x >= area.left && x <= area.right
            ? chart.scales.x.getValueForPixel(x) : null;
        chart.draw();
        renderTrack(true);
    }
    function clearHoverLine() {
        hoverTime = null;
        chart.draw();
        renderTrack(true);
    }
    [canvasElement, trackCanvas].forEach(function (canvas) {
        canvas.addEventListener('pointermove', moveHoverLine);
        canvas.addEventListener('pointerleave', clearHoverLine);
        canvas.addEventListener('pointercancel', clearHoverLine);
    });
    self.ctx._removeHoverListeners = function () {
        [canvasElement, trackCanvas].forEach(function (canvas) {
            canvas.removeEventListener('pointermove', moveHoverLine);
            canvas.removeEventListener('pointerleave', clearHoverLine);
            canvas.removeEventListener('pointercancel', clearHoverLine);
        });
    };

    // ========== RESIZE OPAZOVANJE TRAKU ==========
    setupTrackCanvas();
    self.ctx._trackResizeObserver = new ResizeObserver(function () {
        setupTrackCanvas();
        renderTrack();
    });
    self.ctx._trackResizeObserver.observe(trackWrap);

    self.ctx._renderTrack = renderTrack;
    self.onDataUpdated();
    ctx._ensureLiveSoc();
    self.ctx._currentTimeTimer = setInterval(function () {
        chart.draw();
        renderTrack();
    }, 1000);
};

self.onDataUpdated = function () {
    var ctx = self.ctx;
    var chart = self.ctx.myChart;
    if (!chart) return;
    if (ctx._ensureLiveSoc) ctx._ensureLiveSoc();

    var datasets = [];
    for (var i = 0; i < (ctx.data || []).length; i++) {
        var dataKey = ctx.data[i].dataKey;
        // Urnik prikazujemo samo v spodnjem traku.
        if (telemetryMatches(dataKey, ['schedule_auto'])) continue;
        var values = ctx.data[i].data || [];
        datasets.push({
            _socSeries: telemetryMatches(dataKey, ['soc[%]', 'soc']),
            label: dataKey ? dataKey.label : ('ds' + i),
            yAxisID: isPercentDataKey(dataKey) ? 'yPercent' : 'y',
            data: values.map(function (point) { return { x: Number(point[0]), y: point[1] }; })
                .sort(function (a, b) { return a.x - b.x; }),
            borderColor: dataKey ? dataKey.color : '#999',
            backgroundColor: dataKey ? dataKey.color : '#999',
            fill: false,
            tension: 0,
            stepped: 'before',
            pointRadius: 0,
            pointHoverRadius: 4,
            pointHoverBackgroundColor: dataKey ? dataKey.color : '#999',
            pointHoverBorderColor: '#fff',
            pointHoverBorderWidth: 2
        });
    }
    chart.data.datasets = datasets;
    chart.update();

    if (self.ctx._renderTrack) self.ctx._renderTrack();

    if (!self.ctx._scheduleEdited && !self.ctx._scheduleLocalOverride && !self.ctx._savingSchedule) {
        var hasSchedule = false;
        for (var i = 0; i < (ctx.data || []).length; i++) {
            if (telemetryMatches(ctx.data[i].dataKey, ['schedule_auto']) &&
                ctx.data[i].data && ctx.data[i].data.length > 0) { hasSchedule = true; break; }
        }
        if (hasSchedule && self.ctx.loadIntervalsFromSchedule) {
            self.ctx.loadIntervalsFromSchedule(ctx.data);
            self.ctx._intervalsLoaded = true;
        }
    }
};

self.onLatestDataUpdated = function () {
    if (self.ctx._acceptLiveSoc) self.ctx._acceptLiveSoc(self.ctx.latestData);
};

self.onResize = function () {
    if (self.ctx.myChart) self.ctx.myChart.resize();
    if (self.ctx._renderTrack) requestAnimationFrame(self.ctx._renderTrack);
};

self.onDestroy = function () {
    if (self.ctx._destroyScheduleSave) self.ctx._destroyScheduleSave();
    if (self.ctx._destroyLiveSoc) self.ctx._destroyLiveSoc();
    if (self.ctx._removeHoverListeners) self.ctx._removeHoverListeners();
    if (self.ctx._currentTimeTimer) clearInterval(self.ctx._currentTimeTimer);
    if (self.ctx.myChart) self.ctx.myChart.destroy();
    if (self.ctx._trackResizeObserver) self.ctx._trackResizeObserver.disconnect();
};



