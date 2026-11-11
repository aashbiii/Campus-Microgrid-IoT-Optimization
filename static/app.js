/**
 * Campus Micro-Grid & Energy Optimization System (CM-EOS)
 * Frontend Reactive Engine (Pure Vanilla JS & Chart.js)
 * Real-time 1-second countdown tickers, live telemetry charts, and closed-loop actuation.
 */

// Global State Cache
let currentLabsData = {};
let telemetryChart = null;
let forecastChart = null;

// Lab Colors
const LAB_COLORS = {
    "cc_lab": "#00d4ff",
    "carmel_lab": "#6366f1",
    "assumption_lab": "#10b981",
    "chavera_lab": "#f59e0b"
};

// -------------------------------------------------------------
// Initialization
// -------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
    initClock();
    initCharts();
    fetchStatus();
    fetchTelemetry();
    fetchForecast();
    fetchAuditLogs();

    // High-frequency status polling (every 1.0 second)
    setInterval(fetchStatus, 1000);

    // Smooth client-side 1-second countdown ticker
    setInterval(tickCountdown, 1000);

    // Periodic telemetry chart refresh (every 5 seconds)
    setInterval(fetchTelemetry, 5000);

    // Periodic forecast & audit refresh (every 30 seconds)
    setInterval(fetchForecast, 30000);
    setInterval(fetchAuditLogs, 15000);
});

// -------------------------------------------------------------
// Live Clock
// -------------------------------------------------------------
function initClock() {
    function updateClock() {
        const now = new Date();
        document.getElementById("liveClock").innerText = now.toLocaleTimeString();
    }
    updateClock();
    setInterval(updateClock, 1000);
}

// -------------------------------------------------------------
// API Status Fetch & DOM Rendering
// -------------------------------------------------------------
async function fetchStatus() {
    try {
        const res = await fetch("/api/status");
        if (!res.ok) return;
        const data = await res.json();

        // 1. Update Global KPIs
        document.getElementById("metricTotalPower").innerText = data.total_power_w.toLocaleString();
        document.getElementById("metricTotalKw").innerText = `${data.total_power_kw.toFixed(2)} kW Active Load`;
        document.getElementById("metricActiveLabs").innerText = data.active_labs;
        document.getElementById("metricStandbyLabs").innerText = `${data.standby_labs} Standby/Tripped`;
        document.getElementById("metricKwhSaved").innerText = data.kwh_saved.toFixed(4);
        document.getElementById("metricCo2Saved").innerText = data.co2_saved_kg.toFixed(3);

        if (data.broker) {
            document.getElementById("brokerHost").innerText = data.broker;
        }

        // 2. Update Laboratories Cache & Render Cards
        currentLabsData = data.labs;
        renderLabsGrid(data.labs);

    } catch (e) {
        console.warn("[app] Status fetch error:", e);
    }
}

function renderLabsGrid(labs) {
    const container = document.getElementById("labsContainer");
    if (!container) return;

    // Check if card elements already exist to prevent DOM recreation flicker
    for (const labId in labs) {
        const lab = labs[labId];
        let card = document.getElementById(`card-${labId}`);

        if (!card) {
            card = createLabCardSkeleton(lab);
            container.appendChild(card);
        }

        updateLabCardContent(card, lab);
    }
}

function createLabCardSkeleton(lab) {
    const card = document.createElement("div");
    card.className = "lab-card";
    card.id = `card-${lab.id}`;

    card.innerHTML = `
        <div class="lab-header">
            <div class="lab-title-group">
                <span class="lab-title">${lab.name}</span>
                <span class="node-type-pill">${lab.type}</span>
            </div>
            <span class="badge-status" id="badge-${lab.id}">ACTIVE ON</span>
        </div>
        <div class="lab-description">${lab.description}</div>

        <div class="lab-metrics-row">
            <div class="metric-col">
                <div class="metric-col-label">Active Power</div>
                <div class="metric-col-value" id="val-power-${lab.id}">${lab.power_w.toFixed(1)} W</div>
            </div>
            <div class="metric-col">
                <div class="metric-col-label">Current</div>
                <div class="metric-col-value" id="val-curr-${lab.id}">${lab.current.toFixed(2)} A</div>
            </div>
            <div class="metric-col">
                <div class="metric-col-label">Voltage</div>
                <div class="metric-col-value" id="val-volt-${lab.id}">${lab.voltage.toFixed(1)} V</div>
            </div>
        </div>

        <div id="banner-slot-${lab.id}"></div>

        <div class="lab-actions-row">
            <button class="btn-act btn-restore" onclick="sendCommand('${lab.id}', 'RESTORE')">
                ⚡ Restore Circuit
            </button>
            <button class="btn-act btn-trip" onclick="sendCommand('${lab.id}', 'SHUTDOWN')">
                🛑 Trip Cutoff
            </button>
        </div>

        <div class="lab-last-seen" id="last-seen-${lab.id}">Telemetry: ${lab.timestamp}</div>
    `;
    return card;
}

function updateLabCardContent(card, lab) {
    // 1. Update Metrics
    document.getElementById(`val-power-${lab.id}`).innerText = `${lab.power_w.toFixed(1)} W`;
    document.getElementById(`val-curr-${lab.id}`).innerText = `${lab.current.toFixed(2)} A`;
    document.getElementById(`val-volt-${lab.id}`).innerText = `${lab.voltage.toFixed(1)} V`;
    document.getElementById(`last-seen-${lab.id}`).innerText = `Telemetry: ${lab.timestamp}`;

    const badge = document.getElementById(`badge-${lab.id}`);
    const bannerSlot = document.getElementById(`banner-slot-${lab.id}`);

    // 2. Evaluate State
    if (lab.relay_state === "OFF") {
        badge.className = "badge-status badge-off";
        badge.innerText = "SHUT DOWN";
        bannerSlot.innerHTML = `
            <div class="shutdown-banner">
                <strong>🛑 FACILITY SHUT DOWN (STANDBY MODE)</strong>
                <p>Circuit contactor opened via autonomous optimization cutoff. Workstation baseline clamped.</p>
            </div>
        `;
    } else if (lab.is_idle && lab.remaining_s > 0) {
        badge.className = "badge-status badge-idle";
        badge.innerText = `⏳ SHUTTING DOWN IN ${lab.remaining_s}s`;

        const totalDur = lab.idle_duration_s || 30;
        const pct = Math.min(100, Math.max(0, Math.round(((totalDur - lab.remaining_s) / totalDur) * 100)));

        bannerSlot.innerHTML = `
            <div class="idle-banner">
                <div class="idle-banner-header">
                    <span class="idle-banner-title">⚠️ Equipment Unattended (${lab.power_w.toFixed(1)} W)</span>
                    <span class="idle-timer-badge">Going to shut down in ${lab.remaining_s}s</span>
                </div>
                <div class="idle-banner-sub">
                    Power draw below ${lab.idle_threshold_w} W cutoff. Autonomous shutdown will actuate when timer hits 0s.
                </div>
                <div class="progress-track-idle">
                    <div class="progress-bar-idle" style="width: ${pct}%;"></div>
                </div>
            </div>
        `;
    } else if (lab.is_idle && lab.remaining_s <= 0) {
        badge.className = "badge-status badge-off";
        badge.innerText = "🛑 SHUTTING DOWN NOW";
        bannerSlot.innerHTML = `
            <div class="shutdown-banner">
                <strong>🛑 AUTO-SHUTDOWN INITIATING</strong>
                <p>Idle persistence reached. Dispatching MQTT SHUTDOWN command to contactor coil.</p>
            </div>
        `;
    } else {
        badge.className = "badge-status badge-on";
        badge.innerText = "ACTIVE ON";
        bannerSlot.innerHTML = "";
    }
}

// -------------------------------------------------------------
// Client-Side 1-Second Countdown Ticker
// -------------------------------------------------------------
function tickCountdown() {
    for (const labId in currentLabsData) {
        const lab = currentLabsData[labId];
        if (lab.is_idle && lab.remaining_s > 0) {
            lab.remaining_s -= 1;
            const badge = document.getElementById(`badge-${lab.id}`);
            if (badge) {
                badge.innerText = `⏳ SHUTTING DOWN IN ${lab.remaining_s}s`;
            }
            const timerBadge = document.querySelector(`#banner-slot-${lab.id} .idle-timer-badge`);
            if (timerBadge) {
                timerBadge.innerText = `Going to shut down in ${lab.remaining_s}s`;
            }
            const progressBar = document.querySelector(`#banner-slot-${lab.id} .progress-bar-idle`);
            if (progressBar) {
                const totalDur = lab.idle_duration_s || 30;
                const pct = Math.min(100, Math.max(0, Math.round(((totalDur - lab.remaining_s) / totalDur) * 100)));
                progressBar.style.width = `${pct}%`;
            }

            if (lab.remaining_s <= 0) {
                // Immediate client-side trigger trigger
                fetchStatus();
            }
        }
    }
}

// -------------------------------------------------------------
// Interactive Closed-Loop Command Dispatch
// -------------------------------------------------------------
async function sendCommand(labId, command) {
    const labName = currentLabsData[labId]?.name || labId;
    showToast(`Sending ${command} to ${labName}...`, "info");

    try {
        const res = await fetch(`/api/cmd/${labId}/${command}`, { method: "POST" });
        const data = await res.json();

        if (data.success) {
            if (command === "RESTORE") {
                showToast(`⚡ ${labName} energized back ON!`, "success");
            } else {
                showToast(`🛑 ${labName} circuit breaker tripped to OFF!`, "danger");
            }
            // Rapid state refresh
            setTimeout(fetchStatus, 300);
            setTimeout(fetchAuditLogs, 500);
        } else {
            showToast(`Command error: ${data.error}`, "danger");
        }
    } catch (e) {
        showToast(`Failed to dispatch command: ${e}`, "danger");
    }
}

// -------------------------------------------------------------
// Telemetry & Forecast Charts (Chart.js)
// -------------------------------------------------------------
function initCharts() {
    // 1. Real-Time Telemetry Line Chart
    const ctxTelem = document.getElementById("telemetryChart").getContext("2d");
    telemetryChart = new Chart(ctxTelem, {
        type: "line",
        data: {
            labels: [],
            datasets: []
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: { mode: "index", intersect: false },
            scales: {
                x: {
                    grid: { color: "rgba(255, 255, 255, 0.05)" },
                    ticks: { color: "#94a3b8", maxTicksLimit: 10 }
                },
                y: {
                    grid: { color: "rgba(255, 255, 255, 0.05)" },
                    ticks: { color: "#94a3b8" },
                    title: { display: true, text: "Active Power (Watts)", color: "#94a3b8" }
                }
            },
            plugins: {
                legend: {
                    position: "top",
                    labels: { color: "#cbd5e1", font: { family: "Outfit", size: 12 } }
                }
            }
        }
    });

    // 2. ML 6-Hour Forecast Bar Chart
    const ctxFc = document.getElementById("forecastChart").getContext("2d");
    forecastChart = new Chart(ctxFc, {
        type: "bar",
        data: {
            labels: [],
            datasets: [{
                label: "Forecasted Load (W)",
                data: [],
                backgroundColor: "rgba(0, 212, 255, 0.75)",
                borderColor: "#00d4ff",
                borderWidth: 1,
                borderRadius: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    grid: { color: "rgba(255, 255, 255, 0.05)" },
                    ticks: { color: "#94a3b8" }
                },
                y: {
                    grid: { color: "rgba(255, 255, 255, 0.05)" },
                    ticks: { color: "#94a3b8" },
                    title: { display: true, text: "Predicted Watts", color: "#94a3b8" }
                }
            },
            plugins: {
                legend: { display: false }
            }
        }
    });
}

async function fetchTelemetry() {
    try {
        const res = await fetch("/api/telemetry");
        if (!res.ok) return;
        const data = await res.json();

        // Build Datasets
        const datasets = [];
        let maxLabels = [];

        for (const labId in data) {
            const series = data[labId];
            if (series.labels.length > maxLabels.length) {
                maxLabels = series.labels;
            }
            const labName = currentLabsData[labId]?.name || labId;
            const color = LAB_COLORS[labId] || "#ffffff";

            datasets.push({
                label: labName,
                data: series.data,
                borderColor: color,
                backgroundColor: color,
                tension: 0.3,
                borderWidth: 2,
                pointRadius: 1
            });
        }

        if (telemetryChart) {
            telemetryChart.data.labels = maxLabels;
            telemetryChart.data.datasets = datasets;
            telemetryChart.update();
        }
    } catch (e) {
        console.warn("[app] Telemetry fetch error:", e);
    }
}

async function fetchForecast() {
    try {
        const res = await fetch("/api/forecast");
        if (!res.ok) return;
        const data = await res.json();

        if (forecastChart && data.labels) {
            forecastChart.data.labels = data.labels;
            forecastChart.data.datasets[0].data = data.data;
            forecastChart.update();

            const peakBadge = document.getElementById("peakForecastAlert");
            if (peakBadge && data.peak_power_w > 0) {
                peakBadge.innerText = `Peak Demand: ${data.peak_power_w.toLocaleString()} W at ${data.peak_time}`;
            }
        }
    } catch (e) {
        console.warn("[app] Forecast fetch error:", e);
    }
}

// -------------------------------------------------------------
// Audit Trail Table Fetch
// -------------------------------------------------------------
async function fetchAuditLogs() {
    try {
        const res = await fetch("/api/audit");
        if (!res.ok) return;
        const logs = await res.json();

        const tbody = document.getElementById("auditTableBody");
        if (!tbody) return;

        if (logs.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="8" style="text-align: center; color: var(--text-muted); padding: 20px;">
                        No autonomous shutdowns recorded yet. Turn down a Wokwi potentiometer dial to test.
                    </td>
                </tr>
            `;
            return;
        }

        tbody.innerHTML = logs.map(log => `
            <tr>
                <td><code>#${log.id}</code></td>
                <td><strong>${log.lab_name}</strong></td>
                <td><span style="color: var(--amber-warn);">${log.reason}</span></td>
                <td>${log.ts_flagged}</td>
                <td>${log.ts_restored}</td>
                <td><code>${log.avg_power_w} W</code></td>
                <td><strong style="color: var(--emerald-green);">${log.kwh_saved_est.toFixed(4)} kWh</strong></td>
                <td>
                    ${log.confirmed 
                        ? '<span class="pill-loop-ok">✓ CLOSED LOOP</span>' 
                        : '<span class="pill-loop-wait">⏳ PENDING RESTORE</span>'}
                </td>
            </tr>
        `).join("");

    } catch (e) {
        console.warn("[app] Audit fetch error:", e);
    }
}

// -------------------------------------------------------------
// Toast Alerts
// -------------------------------------------------------------
function showToast(message, type = "info") {
    const container = document.getElementById("toastContainer");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `
        <span>${type === "success" ? "⚡" : type === "danger" ? "🛑" : "ℹ️"}</span>
        <span>${message}</span>
    `;

    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = "0";
        setTimeout(() => toast.remove(), 300);
    }, 3500);
}
