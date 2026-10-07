// Prosojnost podatkov desno od modre črte: 0 = nevidno, 1 = polno vidno.
var FUTURE_DATA_OPACITY = 0.55;
var FULL_BATTERY_INTERVALS = 8;
var SOC_INTERVAL_MS = 15 * 60 * 1000;
var MIN_ZOOM_MS = 15 * 60 * 1000;
var MAX_ZOOM_MS = 7 * 24 * 60 * 60 * 1000;

var CONSUMPTION_FORECAST_COLOR = '#15956b';
var CONSUMPTION_RANGE_COLOR = 'rgba(21, 149, 107, 0.20)';

function consumptionForecastRole(key) {
    if (telemetryMatches(key, ['forecast_consumption_lower'])) return 'lower';
    if (telemetryMatches(key, ['forecast_consumption_upper'])) return 'upper';
    if (telemetryMatches(key, ['forecast_consumption'])) return 'mean';
    return null;
}

function consumptionSource(entry) {
    var source = entry.datasource || {};
    var id = source.entityId || entry.entityId;
    if (id && typeof id === 'object') id = id.id;
    return String(id || source.name || source.aliasName || 'default');
}

// Pas rišemo neposredno med časovno ujemajočima se mejama, nikoli do ničle.
// Vsak pravokotnik pokrije samo svoj 15-minutni interval; vrzeli ostanejo prazne.
function drawConsumptionRange(chart) {
    var area = chart.chartArea;
    var x = chart.scales.x, y = chart.scales.yPower;
    if (!area || !x || !y) return;
    var c = chart.ctx;
    c.save();
    c.beginPath();
    c.rect(area.left, area.top, area.right - area.left, area.bottom - area.top);
    c.clip();
    c.fillStyle = CONSUMPTION_RANGE_COLOR;
    chart.data.datasets.forEach(function (lower, lowerIndex) {
        if (lower._consumptionRole !== 'lower' || !chart.isDatasetVisible(lowerIndex)) return;
        var upperIndex = chart.data.datasets.findIndex(function (d) {
            return d._consumptionRole === 'upper' && d._consumptionSource === lower._consumptionSource;
        });
        if (upperIndex < 0 || !chart.isDatasetVisible(upperIndex)) return;
        var upper = new Map(chart.data.datasets[upperIndex].data.map(function (p) { return [p.x, p.y]; }));
        lower.data.forEach(function (point, i) {
            if (point._intervalEnd) return;
            var high = upper.get(point.x);
            if (point.y == null || high == null || !isFinite(point.y) || !isFinite(high) || high < point.y) return;
            var end = Math.min(point.x + SOC_INTERVAL_MS,
                i + 1 < lower.data.length ? lower.data[i + 1].x : Infinity);
            var left = Math.max(area.left, x.getPixelForValue(point.x));
            var right = Math.min(area.right, x.getPixelForValue(end));
            if (right <= left) return;
            var top = y.getPixelForValue(high), bottom = y.getPixelForValue(point.y);
            c.fillRect(left, top, right - left, bottom - top);
        });
    });
    c.restore();
}

// Koledarski dan v lokalnem časovnem pasu, kot pri ostalih časovnih oznakah.
function selectedDayBounds(ctx) {
    var reference = ctx.timeWindow && ctx.timeWindow.minTime;
    if (reference == null || !isFinite(Number(reference))) {
        reference = Infinity;
        (ctx.data || []).forEach(function (entry) {
            validTelemetryPoints(entry.data).forEach(function (p) { reference = Math.min(reference, p[0]); });
        });
        if (!isFinite(reference)) reference = Date.now();
    }
    var start = new Date(Number(reference));
    start.setHours(0, 0, 0, 0);
    var end = new Date(start.getTime());
    end.setDate(end.getDate() + 1);
    return { min: start.getTime(), max: end.getTime() };
}

function fullDayTicks(min, max, width) {
    var maxLabels = Math.max(2, Math.floor(width / 65) + 1);
    var steps = [1, 2, 3, 4, 6, 8, 12, 24];
    var hours = steps.find(function (step) { return 24 / step + 1 <= maxLabels; }) || 24;
    var ticks = [{ value: min }];
    for (var hour = hours; hour < 24; hour += hours) {
        var date = new Date(min);
        date.setHours(hour, 0, 0, 0);
        var time = date.getTime();
        if (time > ticks[ticks.length - 1].value && time < max) ticks.push({ value: time });
    }
    ticks.push({ value: max });
    return ticks;
}

function zoomTicks(min, max, width) {
    var labelWidth = max - min > 24 * 60 * 60 * 1000 ? 100 : 65;
    var maxLabels = Math.max(2, Math.floor(width / labelWidth) + 1);
    var steps = [15, 30, 60, 120, 180, 240, 360, 480, 720, 1440, 2880, 4320];
    var step = steps[steps.length - 1] * 60000;
    for (var i = 0; i < steps.length; i++) {
        if ((max - min) / (steps[i] * 60000) <= maxLabels - 1) {
            step = steps[i] * 60000;
            break;
        }
    }
    var ticks = [{ value: min }];
    for (var t = Math.ceil(min / step) * step; t < max; t += step) {
        if (t > min) ticks.push({ value: t });
    }
    ticks.push({ value: max });
    return ticks;
}

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
function calculateSocForecast(soc, sun, schedule, end, now, scheduleValues, maxDischargePower, totalCapacity) {
    var measured = validTelemetryPoints(soc).filter(function (p) { return p[0] <= now; });
    if (!measured.length || !(FULL_BATTERY_INTERVALS > 0)) return [];
    var last = measured[measured.length - 1];
    var time = last[0], value = Math.max(0, Math.min(100, last[1]));
    if (!(end > time)) return [];
    // Manjkajoče ali neveljavno sonce ne sme prekiniti celotne SOC črte.
    // Uporabimo zadnji veljavni odstotek; pred prvim podatkom predpostavimo 0 %.
    var solar = validTelemetryPoints(sun).filter(function (p) { return p[1] >= 0 && p[1] <= 100; });
    var power = validTelemetryPoints(scheduleValues);
    var dischargePower = validTelemetryPoints(maxDischargePower).filter(function (p) { return p[1] >= 0; });
    var capacity = validTelemetryPoints(totalCapacity).filter(function (p) { return p[1] > 0; });
    var boundaries = [time, end];
    for (var t = (Math.floor(time / SOC_INTERVAL_MS) + 1) * SOC_INTERVAL_MS; t < end; t += SOC_INTERVAL_MS) boundaries.push(t);
    solar.forEach(function (p) { if (p[0] > time && p[0] < end) boundaries.push(p[0]); });
    power.forEach(function (p) { if (p[0] > time && p[0] < end) boundaries.push(p[0]); });
    dischargePower.forEach(function (p) { if (p[0] > time && p[0] < end) boundaries.push(p[0]); });
    capacity.forEach(function (p) { if (p[0] > time && p[0] < end) boundaries.push(p[0]); });
    schedule.forEach(function (iv) {
        [iv.xMin, iv.xMax].forEach(function (ts) { if (ts > time && ts < end) boundaries.push(ts); });
    });
    boundaries.sort(function (a, b) { return a - b; });
    var points = [{ x: time, y: value }], sunIndex = -1, powerIndex = -1;
    var dischargePowerIndex = -1, capacityIndex = -1;
    for (var i = 1; i < boundaries.length; i++) {
        var next = boundaries[i];
        if (next <= time) continue;
        while (sunIndex + 1 < solar.length && solar[sunIndex + 1][0] <= time) sunIndex++;
        while (powerIndex + 1 < power.length && power[powerIndex + 1][0] <= time) powerIndex++;
        while (dischargePowerIndex + 1 < dischargePower.length && dischargePower[dischargePowerIndex + 1][0] <= time) dischargePowerIndex++;
        while (capacityIndex + 1 < capacity.length && capacity[capacityIndex + 1][0] <= time) capacityIndex++;
        var active = schedule.find(function (iv) { return iv.xMin <= time && time < iv.xMax; });
        var rate = 0;
        if (active && active.type === 'polnjenje') {
            var sunPercent = sunIndex >= 0 ? solar[sunIndex][1] : 0;
            var chargePower = powerIndex >= 0 ? power[powerIndex][1] : sunPercent / 100;
            rate = (100 / FULL_BATTERY_INTERVALS) * Math.max(0, Math.min(1, chargePower));
        } else if (active && active.type === 'praznjenje') {
            var maxPower = dischargePowerIndex >= 0 ? dischargePower[dischargePowerIndex][1] : 0;
            var totalEnergy = capacityIndex >= 0 ? capacity[capacityIndex][1] : 0;
            var dischargeFraction = powerIndex >= 0 && power[powerIndex][1] < 0 ?
                Math.max(0, Math.min(1, -power[powerIndex][1])) : 1;
            // Negativna vrednost urnika določa delež največje moči: -0,6 pomeni 60 %.
            rate = totalEnergy > 0 ? -(maxPower * dischargeFraction / totalEnergy) * 100 *
                (SOC_INTERVAL_MS / 3600000) : 0;
        }
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

// Te serije so prikazane stopničasto; ostale vidne serije so navadne črte.
var STEPPED_DATA_KEYS = ['price', 'bsp cena', 'sun_data'];

// Ti podatki so na voljo izračunom widgeta, na grafu pa se ne prikažejo.
var HIDDEN_DATA_KEYS = [
    'schedule_auto',
    'max_discharge_power[kW]',
    'total_capacity[kWh]'
];

function isHiddenDataKey(dataKey) {
    if (telemetryMatches(dataKey, HIDDEN_DATA_KEYS)) return true;
    if (!dataKey) return false;
    return [dataKey.name, dataKey.label].some(function (value) {
        var key = String(value || '').trim().toLowerCase();
        return key.indexOf('max_discharge_power') !== -1 ||
            key.indexOf('total_capacity') !== -1;
    });
}

function isPercentDataKey(dataKey) {
    if (!dataKey) return false;
    var name = String(dataKey.name == null ? '' : dataKey.name).trim();
    var key = (name || String(dataKey.label || '').trim()).toLowerCase();
    return PERCENT_DATA_KEYS.some(function (entry) {
        return String(entry).trim().toLowerCase() === key;
    });
}

function isActivePowerDataKey(dataKey) {
    if (!dataKey) return false;
    return [dataKey.name, dataKey.label].some(function (value) {
        return String(value || '').trim().toLowerCase().indexOf('active_power_total') !== -1;
    });
}

// Vse, kar ima v imenu ali labelu "kw" (kW, KW, [kW], consumption[kW]), gre na os moči.
// "kwh" je izvzet.
function isKwDataKey(dataKey) {
    if (!dataKey) return false;
    return [dataKey.name, dataKey.label].some(function (value) {
        return /kw(?!h)/.test(String(value || '').trim().toLowerCase());
    });
}

function valueAtOrBefore(values, time) {
    var points = validTelemetryPoints(values);
    var low = 0, high = points.length - 1, found = -1;
    while (low <= high) {
        var middle = (low + high) >> 1;
        if (points[middle][0] <= time) {
            found = middle;
            low = middle + 1;
        } else high = middle - 1;
    }
    return found >= 0 ? points[found][1] : null;
}

function projectedActivePower(ctx, chart) {
    if (!chart || !chart.scales.x) return [];
    var now = Date.now();
    var end = Number(chart.scales.x.max);
    if (!(end > now)) return [];

    var scheduleEntry = (ctx.data || []).find(function (entry) {
        return telemetryMatches(entry.dataKey, ['schedule_auto']);
    });
    var powerEntry = (ctx.data || []).find(function (entry) {
        return telemetryMatches(entry.dataKey, ['max_discharge_power[kw]']);
    });
    var schedulePoints = scheduleEntry && scheduleEntry.data;
    var powerPoints = powerEntry && powerEntry.data;

    function actionAt(time) {
        if (ctx._scheduleEdited || ctx._scheduleLocalOverride) {
            var active = (ctx.intervals || []).find(function (interval) {
                return interval.from <= time && time < interval.to;
            });
            return active ? (active.type === 'polnjenje' ? 1 : -1) : 0;
        }
        var stored = valueAtOrBefore(schedulePoints, time);
        return stored === null ? 0 : Math.max(-1, Math.min(1, stored));
    }

    function powerAt(time) {
        var maximumPower = valueAtOrBefore(powerPoints, time);
        if (maximumPower === null || !isFinite(maximumPower)) return null;
        return Math.max(0, maximumPower) * actionAt(time);
    }

    var result = [];
    function appendPower(time, value) {
        if (value === null) return;
        var previous = result[result.length - 1];
        if (previous && previous.y !== value && time > previous.x) {
            result.push({ x: time - 1, y: previous.y });
        }
        result.push({ x: time, y: value });
    }
    appendPower(now, powerAt(now));
    var firstBoundary = (Math.floor(now / SOC_INTERVAL_MS) + 1) * SOC_INTERVAL_MS;
    for (var time = firstBoundary; time < end; time += SOC_INTERVAL_MS) {
        appendPower(time, powerAt(time));
    }
    appendPower(end, powerAt(end));
    return result;
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
        '<div style="display:flex; flex-direction:column; width:100%; min-width:0; max-width:100%; height:100%; gap:6px;">' +
        '  <div style="display:flex; flex-wrap:wrap; min-width:0; align-items:center; gap:8px; padding:8px 10px; border:1px solid #e8edf3; border-radius:10px; background:#fff; box-shadow:0 1px 3px rgba(20,45,75,0.06);">' +
        '    <button id="saveSchedule" type="button" style="display:none; background:#1976d2; color:white; border:0; border-radius:4px; padding:6px 14px; cursor:pointer;">Shrani</button>' +
        '    <span id="saveScheduleStatus" role="status" style="font-size:12px; margin-left:8px;"></span>' +
        '    <span style="display:flex; gap:4px; margin-left:8px;">' +
        '      <button id="zoomOut" type="button" title="Oddalji" aria-label="Oddalji" style="display:none;">−</button>' +
        '      <button id="zoomIn" type="button" title="Približaj" aria-label="Približaj" style="display:none;">+</button>' +
        '      <button id="zoomReset" type="button" title="Pokaži cel dan" style="border:1px solid #d8e2ee; border-radius:6px; padding:5px 10px; background:#f8fafc; color:#31506e; font-size:12px; cursor:pointer;">Cel dan</button>' +
        '    </span>' +
        '    <span id="profitCalculator" style="font-size:13px; color:#26384a; margin-left:auto; white-space:nowrap; padding:6px 10px; border-radius:7px; background:#f2f7fc; border:1px solid #dce9f5;">' +
        '      Profit: <b id="actualProfit">–</b> dosežen + <b id="forecastProfit">–</b> predvideni = <b id="totalProfit">–</b>' +
        '    </span>' +
        '    <span id="rangeLabel" style="font-size:12px; color:#555; margin-left:12px;"></span>' +
        '  </div>' +
        '  <div id="chartWrap" style="position:relative; flex:1; min-height:0; min-width:0; width:100%; padding:4px 8px 0; border:1px solid #edf1f5; border-radius:10px; background:#fff; box-sizing:border-box;">' +
        '    <canvas id="myChart" style="display:block; max-width:100%; width:100%; height:100%; cursor:grab;"></canvas>' +
        '  </div>' +
        '  <div id="trackWrap" style="position:relative; height:96px; flex-shrink:0; min-width:0; width:100%; padding:0 8px 6px; border:1px solid #edf1f5; border-radius:10px; background:#fff; box-sizing:border-box;">' +
        '    <canvas id="scheduleTrack" style="display:block; max-width:100%; width:100%; height:100%; touch-action:none;"></canvas>' +
        '    <div id="dragTooltip" style="position:absolute; display:none; pointer-events:none; background:rgba(0,0,0,0.75); color:white; padding:4px 8px; border-radius:4px; font-size:12px; white-space:nowrap; z-index:10;"></div>' +
        '  </div>' +
        '</div>';

    var canvasElement = document.getElementById('myChart');
    var trackCanvas    = document.getElementById('scheduleTrack');
    var trackWrap      = document.getElementById('trackWrap');
    var rangeLabel     = document.getElementById('rangeLabel');
    var actualProfit   = document.getElementById('actualProfit');
    var forecastProfit = document.getElementById('forecastProfit');
    var totalProfit    = document.getElementById('totalProfit');
    var dragTooltip    = document.getElementById('dragTooltip');
    var trackCtx       = trackCanvas.getContext('2d');
    var saveButton = container.querySelector('#saveSchedule');
    var saveStatus = container.querySelector('#saveScheduleStatus');
    var zoomOutButton = container.querySelector('#zoomOut');
    var zoomInButton = container.querySelector('#zoomIn');
    var zoomResetButton = container.querySelector('#zoomReset');
    var zoomRange = null;
    var panState = null;
    var baseDay = selectedDayBounds(ctx);
    var loadedWindow = {
        min: Number(ctx.timeWindow && ctx.timeWindow.minTime),
        max: Number(ctx.timeWindow && ctx.timeWindow.maxTime)
    };
    var requestedWindows = [];
    var zoomRequestTimer = null;

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
            id: 'consumptionForecastRange',
            beforeDatasetsDraw: drawConsumptionRange
        }, {
            id: 'fullDayRange',
            beforeUpdate: function (activeChart) {
                var currentMin = Number(ctx.timeWindow && ctx.timeWindow.minTime);
                var currentMax = Number(ctx.timeWindow && ctx.timeWindow.maxTime);
                if (isFinite(currentMin) && isFinite(currentMax) &&
                    (currentMin !== loadedWindow.min || currentMax !== loadedWindow.max)) {
                    var requestIndex = requestedWindows.findIndex(function (request) {
                        return Math.abs(currentMin - request.min) < 60000 &&
                            Math.abs(currentMax - request.max) < 60000;
                    });
                    if (requestIndex < 0) {
                        var sameDay = selectedDayBounds(ctx).min === baseDay.min;
                        var sameDuration = Math.abs((currentMax - currentMin) -
                            (loadedWindow.max - loadedWindow.min)) < 60000;
                        if (!sameDay || !sameDuration) {
                            baseDay = selectedDayBounds(ctx);
                            zoomRange = null;
                        }
                        requestedWindows = [];
                    } else {
                        requestedWindows.splice(0, requestIndex + 1);
                    }
                    loadedWindow = { min: currentMin, max: currentMax };
                }
                activeChart.options.scales.x.min = zoomRange ? zoomRange.min : baseDay.min;
                activeChart.options.scales.x.max = zoomRange ? zoomRange.max : baseDay.max;
            }
        }, {
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
                    if (!activeChart.data.datasets[args.index]._consumptionRole) {
                        c.globalAlpha *= Math.max(0, Math.min(1, FUTURE_DATA_OPACITY));
                    }
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
                    ticks: {
                        maxRotation: 0, autoSkip: false,
                        callback: function (value) {
                            if (Number(value) === this.max && this.min === baseDay.min &&
                                this.max === baseDay.max) return '24:00';
                            var date = new Date(Number(value));
                            var hour = String(date.getHours()).padStart(2, '0') + ':' +
                                String(date.getMinutes()).padStart(2, '0');
                            return this.max - this.min > 24 * 60 * 60 * 1000
                                ? String(date.getDate()).padStart(2, '0') + '.' +
                                  String(date.getMonth() + 1).padStart(2, '0') + ' ' + hour
                                : hour;
                        }
                    },
                    afterBuildTicks: function (axis) {
                        var width = axis.width || (axis.chart && axis.chart.width) || 600;
                        axis.ticks = axis.min === baseDay.min && axis.max === baseDay.max
                            ? fullDayTicks(axis.min, axis.max, width)
                            : zoomTicks(axis.min, axis.max, width);
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
                },
                yPower: {
                    type: 'linear',
                    position: 'right',
                    title: { display: true, text: 'Moč [kW]' },
                    ticks: {
                        callback: function (value) { return value + ' kW'; }
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
                            var unit = context.dataset.yAxisID === 'yPercent' ? ' %' :
                                (context.dataset.yAxisID === 'yPower' ? ' kW' : '');
                            return (label ? label + ': ' : '') + value + unit;
                        }
                    }
                }
            }
        }
    });

    var chart = self.ctx.myChart;

    function seriesByName(names) {
        return (ctx.data || []).find(function (entry) {
            return telemetryMatches(entry.dataKey, names);
        });
    }

    function lastValueAt(points, time) {
        points = points || [];
        var low = 0, high = points.length - 1, found = -1;
        while (low <= high) {
            var middle = (low + high) >> 1;
            if (points[middle][0] <= time) {
                found = middle;
                low = middle + 1;
            } else high = middle - 1;
        }
        return found >= 0 ? points[found][1] : null;
    }

    function scheduleValueAt(time, schedulePoints) {
        // Shranjen urnik je edini vir za dosežen in predvideni profit.
        // Dejanska moč, SOC in poraba zato ne morejo spremeniti zneska.
        if (!ctx._scheduleEdited && !ctx._scheduleLocalOverride) {
            var stored = lastValueAt(schedulePoints, time);
            return stored === null ? 0 : stored;
        }

        // Med urejanjem takoj pokažemo predogled lokalno narisanih intervalov.
        var active = (intervals || []).find(function (iv) {
            return iv.xMin <= time && time < iv.xMax;
        });
        return active ? (active.type === 'polnjenje' ? 1 : -1) : 0;
    }

    function formatProfit(value) {
        return value === null || !isFinite(value) ? '–' :
            value.toLocaleString('sl-SI', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' €';
    }

    function updateProfitCalculator() {
        if (!chart.scales.x || !actualProfit || !forecastProfit || !totalProfit) return;
        var rangeMin = Number(chart.scales.x.min);
        var rangeMax = Number(chart.scales.x.max);
        if (!isFinite(rangeMin) || !isFinite(rangeMax) || rangeMax <= rangeMin) return;

        var price = seriesByName(['price', 'bsp cena']);
        var schedule = seriesByName(['schedule_auto']);
        var maxDischarge = seriesByName(['max_discharge_power[kw]']);
        var capacity = seriesByName(['total_capacity[kwh]']);
        var pricePoints = validTelemetryPoints(price && price.data);
        var schedulePoints = validTelemetryPoints(schedule && schedule.data);
        var dischargePoints = validTelemetryPoints(maxDischarge && maxDischarge.data);
        var capacityPoints = validTelemetryPoints(capacity && capacity.data);
        var now = Date.now();

        // Doseženi in predvideni profit uporabljata izbrane nakupe/prodaje.
        // Razlikuje ju samo časovna meja "zdaj".
        function integrateSchedule(from, to) {
            if (!(to > from)) return 0;
            var profit = 0, samples = 0;
            for (var time = from; time < to; time += SOC_INTERVAL_MS) {
                var next = Math.min(to, time + SOC_INTERVAL_MS);
                var priceValue = lastValueAt(pricePoints, time);
                if (priceValue === null) continue;

                var action = scheduleValueAt(time, schedulePoints);
                if (action === 0) {
                    samples++;
                    continue;
                }

                var powerValue = null;
                if (action < 0) {
                    var maximumDischargePower = lastValueAt(dischargePoints, time);
                    var dischargeFraction = Math.max(0, Math.min(1, -action));
                    powerValue = maximumDischargePower === null ? null :
                        maximumDischargePower * dischargeFraction;
                } else {
                    var totalEnergy = lastValueAt(capacityPoints, time);
                    if (totalEnergy > 0) {
                        // Enaka hitrost polnjenja kot v SOC napovedi: polna baterija v 8 intervalih.
                        powerValue = -action * totalEnergy * 3600000 /
                            (FULL_BATTERY_INTERVALS * SOC_INTERVAL_MS);
                    }
                }
                if (powerValue === null || !isFinite(powerValue)) continue;
                profit += powerValue * ((next - time) / 3600000) * priceValue / 1000;
                samples++;
            }
            return samples ? profit : null;
        }

        var achieved = integrateSchedule(rangeMin, Math.min(rangeMax, now));
        var forecast = integrateSchedule(Math.max(rangeMin, now), rangeMax);
        var total = achieved === null || forecast === null ? null : achieved + forecast;
        actualProfit.textContent = formatProfit(achieved);
        forecastProfit.textContent = formatProfit(forecast);
        totalProfit.textContent = formatProfit(total);
    }

    function updateZoomButtons() {
        var span = chart.scales.x.max - chart.scales.x.min;
        zoomInButton.disabled = span <= MIN_ZOOM_MS + 1;
        zoomOutButton.disabled = span >= MAX_ZOOM_MS - 1;
        var today = new Date();
        today.setHours(0, 0, 0, 0);
        var tomorrow = new Date(today.getTime());
        tomorrow.setDate(tomorrow.getDate() + 1);
        zoomResetButton.disabled = Math.abs(chart.scales.x.min - today.getTime()) < 1 &&
            Math.abs(chart.scales.x.max - tomorrow.getTime()) < 1;
    }
    function requestVisibleData() {
        if (zoomRequestTimer) clearTimeout(zoomRequestTimer);
        zoomRequestTimer = setTimeout(function () {
            zoomRequestTimer = null;
            if (!zoomRange || !ctx.timewindowFunctions ||
                !ctx.timewindowFunctions.onUpdateTimewindow) return;
            var min = Number(ctx.timeWindow && ctx.timeWindow.minTime);
            var max = Number(ctx.timeWindow && ctx.timeWindow.maxTime);
            if (!isFinite(min) || !isFinite(max) || zoomRange.min < min || zoomRange.max > max) {
                requestedWindows.push({ min: zoomRange.min, max: zoomRange.max });
                ctx.timewindowFunctions.onUpdateTimewindow(zoomRange.min, zoomRange.max);
            }
        }, 200);
    }
    function applyZoom(anchor, factor) {
        var axis = chart.scales.x;
        var span = axis.max - axis.min;
        var nextSpan = Math.max(MIN_ZOOM_MS, Math.min(MAX_ZOOM_MS, span * factor));
        if (Math.abs(nextSpan - span) < 1) return;
        var fraction = Math.max(0, Math.min(1, (anchor - axis.min) / span));
        var nextMin = anchor - fraction * nextSpan;
        zoomRange = { min: nextMin, max: nextMin + nextSpan };
        chart.update('none');
        updateZoomButtons();
        requestVisibleData();
        if (ctx._renderTrack) ctx._renderTrack();
    }
    function zoomAtPointer(event) {
        var area = chart.chartArea;
        var rect = event.currentTarget.getBoundingClientRect();
        if (!area || !rect.width || !rect.height || area.right <= area.left) return;
        var x = (event.clientX - rect.left) * chart.width / rect.width;
        if (x < area.left || x > area.right) return;
        event.preventDefault();
        applyZoom(chart.scales.x.getValueForPixel(x), event.deltaY < 0 ? 0.8 : 1.25);
    }
    function startPan(event) {
        if (event.button !== 0 || panState) return;
        var area = chart.chartArea;
        var rect = canvasElement.getBoundingClientRect();
        if (!area || !rect.width || !rect.height || area.right <= area.left) return;
        var x = (event.clientX - rect.left) * chart.width / rect.width;
        var y = (event.clientY - rect.top) * chart.height / rect.height;
        if (x < area.left || x > area.right || y < area.top || y > area.bottom) return;
        panState = {
            pointerId: event.pointerId,
            startX: event.clientX,
            min: chart.scales.x.min,
            max: chart.scales.x.max,
            plotWidth: (area.right - area.left) * rect.width / chart.width,
            moved: false
        };
        hoverTime = null;
        canvasElement.style.cursor = 'grabbing';
        canvasElement.setPointerCapture(event.pointerId);
    }
    function movePan(event) {
        if (!panState || event.pointerId !== panState.pointerId) return;
        if (Math.abs(event.clientX - panState.startX) < 3 && !panState.moved) return;
        panState.moved = true;
        var offset = (event.clientX - panState.startX) /
            panState.plotWidth * (panState.max - panState.min);
        zoomRange = { min: panState.min - offset, max: panState.max - offset };
        chart.update('none');
        updateZoomButtons();
        if (ctx._renderTrack) ctx._renderTrack(true);
        event.preventDefault();
    }
    function endPan(event) {
        if (!panState || event.pointerId !== panState.pointerId) return;
        var moved = panState.moved;
        panState = null;
        canvasElement.style.cursor = 'grab';
        if (canvasElement.hasPointerCapture(event.pointerId)) {
            canvasElement.releasePointerCapture(event.pointerId);
        }
        if (moved) {
            requestVisibleData();
            if (ctx._renderTrack) ctx._renderTrack();
        }
    }
    canvasElement.addEventListener('wheel', zoomAtPointer, { passive: false });
    trackCanvas.addEventListener('wheel', zoomAtPointer, { passive: false });
    canvasElement.addEventListener('pointerdown', startPan);
    canvasElement.addEventListener('pointermove', movePan);
    canvasElement.addEventListener('pointerup', endPan);
    canvasElement.addEventListener('pointercancel', endPan);
    zoomInButton.addEventListener('click', function () {
        applyZoom((chart.scales.x.min + chart.scales.x.max) / 2, 0.5);
    });
    zoomOutButton.addEventListener('click', function () {
        applyZoom((chart.scales.x.min + chart.scales.x.max) / 2, 2);
    });
    zoomResetButton.addEventListener('click', function () {
        if (zoomRequestTimer) clearTimeout(zoomRequestTimer);
        zoomRequestTimer = null;
        var today = new Date();
        today.setHours(0, 0, 0, 0);
        var tomorrow = new Date(today.getTime());
        tomorrow.setDate(tomorrow.getDate() + 1);
        baseDay = { min: today.getTime(), max: tomorrow.getTime() };
        zoomRange = { min: baseDay.min, max: baseDay.max };
        chart.update('none');
        updateZoomButtons();
        requestVisibleData();
        if (ctx._renderTrack) ctx._renderTrack();
    });
    ctx._removeZoomListeners = function () {
        if (zoomRequestTimer) clearTimeout(zoomRequestTimer);
        canvasElement.removeEventListener('wheel', zoomAtPointer);
        trackCanvas.removeEventListener('wheel', zoomAtPointer);
        canvasElement.removeEventListener('pointerdown', startPan);
        canvasElement.removeEventListener('pointermove', movePan);
        canvasElement.removeEventListener('pointerup', endPan);
        canvasElement.removeEventListener('pointercancel', endPan);
    };
    ctx._updateZoomButtons = updateZoomButtons;
    updateZoomButtons();
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
        var autoSchedule = data.find(function (d) { return telemetryMatches(d.dataKey, ['schedule_auto']); });
        var maxDischargePower = data.find(function (d) {
            return telemetryMatches(d.dataKey, ['max_discharge_power[kw]']);
        });
        var totalCapacity = data.find(function (d) {
            return telemetryMatches(d.dataKey, ['total_capacity[kwh]']);
        });
        var end = chart.scales.x.max;
        var now = Date.now();
        var min = chart.scales.x.min;
        var measurements = mergeSocMeasurements(soc && soc.data, liveSoc,
            min == null ? -Infinity : Number(min), Math.min(now, end));
        var points = calculateSocForecast(measurements, sun && sun.data, intervals, end, now,
            ctx._scheduleEdited || ctx._scheduleLocalOverride ? [] : autoSchedule && autoSchedule.data,
            maxDischargePower && maxDischargePower.data, totalCapacity && totalCapacity.data);
        var socSeries = chart.data.datasets.find(function (d) { return d._socSeries; });
        if (socSeries) {
            // Vedno izhajamo iz meritev, da se napoved pri ponovnem izrisu ne podvaja.
            var measured = measurements
                .filter(function (p) { return p[0] <= now; })
                .map(function (p) { return { x: p[0], y: p[1] }; });
            // Prva napovedana točka je zadnja meritev, zato je ne dodamo dvakrat.
            socSeries.data = measured.concat(points.slice(1));
            socSeries.stepped = false;
            socSeries.tension = 0;
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
        updateProfitCalculator();
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
            if (val > 0) type = 'polnjenje';
            else if (val < 0) type = 'praznjenje';

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
        if (panState && e.currentTarget === canvasElement) return;
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
        var isSoc = telemetryMatches(dataKey, ['soc[%]', 'soc']);
        var isActivePower = isActivePowerDataKey(dataKey);
        var forecastRole = consumptionForecastRole(dataKey);
        var isStepped = !!forecastRole || telemetryMatches(dataKey, STEPPED_DATA_KEYS);
        if (isHiddenDataKey(dataKey)) continue;
        var values = ctx.data[i].data || [];
        var seriesData = values.map(function (point) {
            return { x: Number(point[0]), y: point[1] == null || point[1] === '' ? null : Number(point[1]) };
        }).filter(function (point) {
            return isFinite(point.x) && (forecastRole || point.y != null) && (point.y == null || isFinite(point.y)) &&
                (!isActivePower || point.x <= Date.now());
        }).sort(function (a, b) { return a.x - b.x; });
        if (isActivePower) seriesData = seriesData.concat(projectedActivePower(ctx, chart));
        if (forecastRole && seriesData.length) {
            var complete = [];
            seriesData.forEach(function (point, index) {
                complete.push(point);
                var next = seriesData[index + 1];
                if (next && next.x - point.x > SOC_INTERVAL_MS) {
                    complete.push({ x: point.x + SOC_INTERVAL_MS, y: null });
                }
            });
            var last = complete[complete.length - 1];
            complete.push({ x: last.x + SOC_INTERVAL_MS, y: last.y, _intervalEnd: true });
            seriesData = complete;
        }
        var forecastLabels = { mean: 'Napoved porabe', lower: 'Spodnja meja porabe', upper: 'Zgornja meja porabe' };
        var color = forecastRole ? CONSUMPTION_FORECAST_COLOR : (dataKey ? dataKey.color : '#999');
        datasets.push({
            _socSeries: isSoc,
            _consumptionRole: forecastRole,
            _consumptionSource: consumptionSource(ctx.data[i]),
            label: forecastRole ? forecastLabels[forecastRole] : (dataKey ? dataKey.label : ('ds' + i)),
            yAxisID: (isActivePower || isKwDataKey(dataKey) || forecastRole) ? 'yPower' : (isPercentDataKey(dataKey) ? 'yPercent' : 'y'),
            data: seriesData,
            borderColor: forecastRole && forecastRole !== 'mean' ? 'rgba(21, 149, 107, 0.65)' : color,
            backgroundColor: forecastRole ? CONSUMPTION_RANGE_COLOR : color,
            borderWidth: forecastRole === 'mean' ? 2.8 : (forecastRole ? 1.2 : 2),
            borderDash: forecastRole && forecastRole !== 'mean' ? [4, 3] : [],
            spanGaps: false,
            fill: false,
            tension: 0,
            stepped: isStepped ? 'before' : false,
            pointRadius: 0,
            pointHoverRadius: 4,
            pointHoverBackgroundColor: color,
            pointHoverBorderColor: '#fff',
            pointHoverBorderWidth: 2
        });
    }
    chart.data.datasets = datasets;
    chart.update();
    if (ctx._updateZoomButtons) ctx._updateZoomButtons();

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
    if (self.ctx._removeZoomListeners) self.ctx._removeZoomListeners();
    if (self.ctx._destroyScheduleSave) self.ctx._destroyScheduleSave();
    if (self.ctx._destroyLiveSoc) self.ctx._destroyLiveSoc();
    if (self.ctx._removeHoverListeners) self.ctx._removeHoverListeners();
    if (self.ctx._currentTimeTimer) clearInterval(self.ctx._currentTimeTimer);
    if (self.ctx.myChart) self.ctx.myChart.destroy();
    if (self.ctx._trackResizeObserver) self.ctx._trackResizeObserver.disconnect();
};