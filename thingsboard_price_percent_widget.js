// Add future percentage telemetry keys to this list (values are already 0–100).
var PERCENT_DATA_KEYS = ['sun_percent', 'SOC[%]'];

function isPercentDataKey(dataKey) {
    if (!dataKey) return false;
    var name = String(dataKey.name == null ? '' : dataKey.name).trim();
    var key = (name || String(dataKey.label || '').trim()).toLowerCase();
    return PERCENT_DATA_KEYS.some(function (entry) {
        return String(entry).trim().toLowerCase() === key;
    });
}

self.onInit = function () {
    var ctx = self.ctx;
    var container = ctx.$container[0];
    container.innerHTML =
        '<div style="display:flex; flex-direction:column; height:100%; gap:6px;">' +
        '  <div style="display:flex; align-items:center;">' +
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
            id: 'currentTimeLine',
            afterDraw: function (activeChart) {
                var area = activeChart.chartArea;
                if (area) drawCurrentTimeLine(activeChart.ctx, activeChart, area.top, area.bottom);
            }
        }],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
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
                    mode: 'index',
                    intersect: false,
                    callbacks: {
                        label: function (context) {
                            var label = context.dataset.label || '';
                            var value = context.formattedValue;
                            return (label ? label + ': ' : '') + value +
                                (context.dataset.yAxisID === 'yPercent' ? ' %' : '');
                        }
                    }
                }
            }
        }
    });

    var chart = self.ctx.myChart;

    // ========== STATE ==========
    var intervals = [];
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

    function renderTrack() {
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
            if (d.dataKey && d.dataKey.label && d.dataKey.label.toLowerCase() === 'schedule_auto') {
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

    // ========== RESIZE OPAZOVANJE TRAKU ==========
    setupTrackCanvas();
    self.ctx._trackResizeObserver = new ResizeObserver(function () {
        setupTrackCanvas();
        renderTrack();
    });
    self.ctx._trackResizeObserver.observe(trackWrap);

    self.ctx._renderTrack = renderTrack;
    self.ctx._currentTimeTimer = setInterval(function () {
        chart.draw();
        renderTrack();
    }, 1000);
};

self.onDataUpdated = function () {
    var ctx = self.ctx;
    var chart = self.ctx.myChart;
    if (!chart) return;

    var datasets = [];
    for (var i = 0; i < ctx.data.length; i++) {
        var dataKey = ctx.data[i].dataKey;
        // Urnik prikazujemo samo v spodnjem traku.
        if (dataKey && String(dataKey.label || '').toLowerCase() === 'schedule_auto') continue;
        var values = ctx.data[i].data || [];
        datasets.push({
            label: dataKey ? dataKey.label : ('ds' + i),
            yAxisID: isPercentDataKey(dataKey) ? 'yPercent' : 'y',
            data: values.map(function (point) { return { x: point[0], y: point[1] }; }),
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

    if (!self.ctx._intervalsLoaded) {
        var hasSchedule = false;
        for (var i = 0; i < ctx.data.length; i++) {
            if (ctx.data[i].dataKey && ctx.data[i].dataKey.label === 'schedule_auto' &&
                ctx.data[i].data && ctx.data[i].data.length > 0) { hasSchedule = true; break; }
        }
        if (hasSchedule && self.ctx.loadIntervalsFromSchedule) {
            self.ctx.loadIntervalsFromSchedule(ctx.data);
            self.ctx._intervalsLoaded = true;
        }
    }
};

self.onResize = function () {
    if (self.ctx.myChart) self.ctx.myChart.resize();
    if (self.ctx._renderTrack) requestAnimationFrame(self.ctx._renderTrack);
};

self.onDestroy = function () {
    if (self.ctx._currentTimeTimer) clearInterval(self.ctx._currentTimeTimer);
    if (self.ctx.myChart) self.ctx.myChart.destroy();
    if (self.ctx._trackResizeObserver) self.ctx._trackResizeObserver.disconnect();
};


