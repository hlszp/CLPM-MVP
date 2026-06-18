import { createWriteStream, writeFileSync } from "node:fs";
import { join } from "node:path";

const OUT_DIR = new URL(".", import.meta.url).pathname;
const LOOP_COUNT = 24;
const DURATION_SECONDS = 3600;
const START_UTC_MS = Date.UTC(2026, 5, 16, 0, 0, 0);
const TZ_OFFSET_HOURS = 8;

let seed = 20260616;
function random() {
  seed = (seed * 1664525 + 1013904223) >>> 0;
  return seed / 0x100000000;
}

function rand(min, max) {
  return min + (max - min) * random();
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function round(value, digits = 3) {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function timestampAt(second) {
  const local = new Date(START_UTC_MS + second * 1000 + TZ_OFFSET_HOURS * 3600 * 1000);
  const yyyy = local.getUTCFullYear();
  const mm = String(local.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(local.getUTCDate()).padStart(2, "0");
  const hh = String(local.getUTCHours()).padStart(2, "0");
  const mi = String(local.getUTCMinutes()).padStart(2, "0");
  const ss = String(local.getUTCSeconds()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}T${hh}:${mi}:${ss}+08:00`;
}

const scenarioCycle = [
  "normal",
  "normal",
  "oscillation",
  "valve_stiction",
  "manual_mode",
  "data_quality_issue",
  "disturbance",
  "tuning_candidate",
];

const loopTypes = [
  { prefix: "FIC", type: "flow", unit: "%", pvMin: 0, pvMax: 120, spBase: [42, 85], gain: 0.55, tau: [18, 45] },
  { prefix: "LIC", type: "level", unit: "%", pvMin: 0, pvMax: 100, spBase: [38, 72], gain: 0.32, tau: [60, 160] },
  { prefix: "TIC", type: "temperature", unit: "degC", pvMin: 80, pvMax: 360, spBase: [145, 290], gain: 0.22, tau: [90, 220] },
  { prefix: "PIC", type: "pressure", unit: "MPa", pvMin: 0, pvMax: 8, spBase: [2.2, 6.6], gain: 0.045, tau: [30, 90] },
  { prefix: "AIC", type: "composition", unit: "%", pvMin: 0, pvMax: 100, spBase: [14, 68], gain: 0.28, tau: [120, 260] },
];

const loops = Array.from({ length: LOOP_COUNT }, (_, idx) => {
  const family = loopTypes[idx % loopTypes.length];
  const scenario = scenarioCycle[idx % scenarioCycle.length];
  const loopNo = 1101 + idx * 7;
  const sp = rand(family.spBase[0], family.spBase[1]);
  const p = round(rand(0.8, 2.8), 2);
  const i = Math.round(rand(55, 260));
  const d = idx % 5 === 0 ? round(rand(0, 18), 1) : 0;
  const tau = rand(family.tau[0], family.tau[1]);
  return {
    loop_id: `loop_${String(idx + 1).padStart(2, "0")}`,
    loop_tag: `${family.prefix}-${loopNo}`,
    loop_name: `${family.type.toUpperCase()} control loop ${idx + 1}`,
    unit_name: idx < 12 ? "气化装置 A" : "气化装置 B",
    loop_group: idx % 2 === 0 ? "前段" : "后段",
    control_type: family.type,
    engineering_unit: family.unit,
    scenario,
    pv_min: family.pvMin,
    pv_max: family.pvMax,
    nominal_sp: round(sp, 3),
    process_gain: family.gain,
    time_constant_s: round(tau, 1),
    p,
    i,
    d,
  };
});

function modeFor(loop, second) {
  if (loop.scenario === "manual_mode") {
    if (second >= 780 && second <= 2260) return "MAN";
    return "AUTO";
  }
  if (loop.scenario === "data_quality_issue" && second >= 2550 && second <= 2660) return "UNKNOWN";
  if (loop.loop_tag.startsWith("PIC") && second > 2100 && second < 2380) return "CAS";
  return "AUTO";
}

function qualityFor(loop, second) {
  if (loop.scenario === "data_quality_issue") {
    if ((second >= 620 && second <= 700) || (second >= 2440 && second <= 2590)) return "BAD";
    if (second >= 1600 && second <= 1660) return "FROZEN";
  }
  if (loop.scenario === "disturbance" && second >= 1800 && second <= 1810) return "SUSPECT";
  return "GOOD";
}

function setpointFor(loop, second) {
  let sp = loop.nominal_sp;
  if (loop.scenario === "normal" && second > 1800) sp += loop.control_type === "pressure" ? 0.35 : 3.5;
  if (loop.scenario === "tuning_candidate" && second > 1200) sp += loop.control_type === "pressure" ? 0.42 : 4.2;
  if (loop.scenario === "disturbance" && second > 2700) sp -= loop.control_type === "temperature" ? 8 : 2.5;
  return clamp(sp, loop.pv_min + 0.05 * (loop.pv_max - loop.pv_min), loop.pv_max - 0.05 * (loop.pv_max - loop.pv_min));
}

function eventMarker(loop, second) {
  if (second === 0) return "window_start";
  if (second === DURATION_SECONDS) return "window_end";
  if (loop.scenario === "manual_mode" && second === 780) return "mode_auto_to_manual";
  if (loop.scenario === "manual_mode" && second === 2261) return "mode_manual_to_auto";
  if (loop.scenario === "normal" && second === 1801) return "sp_change";
  if (loop.scenario === "tuning_candidate" && second === 1201) return "small_sp_excitation";
  if (loop.scenario === "disturbance" && second === 1800) return "process_disturbance";
  if (loop.scenario === "data_quality_issue" && second === 620) return "quality_bad_start";
  if (loop.scenario === "data_quality_issue" && second === 701) return "quality_recovered";
  return "";
}

const states = loops.map((loop) => ({
  pv: loop.nominal_sp + rand(-0.8, 0.8),
  op: clamp(50 + rand(-8, 8), 0, 100),
  integral: 0,
  frozenPv: null,
}));

const csvPath = join(OUT_DIR, "control_loop_second_level_24loops_1h.csv");
const stream = createWriteStream(csvPath, { encoding: "utf8" });
stream.write([
  "timestamp",
  "second",
  "loop_id",
  "loop_tag",
  "unit_name",
  "loop_group",
  "control_type",
  "scenario",
  "pv",
  "sp",
  "op",
  "mode",
  "p",
  "i",
  "d",
  "engineering_unit",
  "quality",
  "event_marker",
].join(",") + "\n");

const events = [];

for (let second = 0; second <= DURATION_SECONDS; second += 1) {
  const timestamp = timestampAt(second);
  loops.forEach((loop, idx) => {
    const state = states[idx];
    const mode = modeFor(loop, second);
    const quality = qualityFor(loop, second);
    const sp = setpointFor(loop, second);
    const error = sp - state.pv;
    const noiseScale = (loop.pv_max - loop.pv_min) * 0.0018;
    const scenarioNoise = loop.scenario === "data_quality_issue" ? noiseScale * 2.8 : noiseScale;

    if (mode === "AUTO" || mode === "CAS") {
      state.integral = clamp(state.integral + error / Math.max(loop.i, 1), -25, 25);
      const pAction = loop.p * error * 0.65;
      const iAction = state.integral * 1.8;
      let suggestedOp = 50 + pAction + iAction;
      if (loop.scenario === "oscillation") {
        suggestedOp += 9 * Math.sin((2 * Math.PI * second) / 260);
      }
      if (loop.scenario === "valve_stiction") {
        const step = Math.round(suggestedOp / 6) * 6;
        suggestedOp = second % 45 < 34 ? state.op : step;
      }
      state.op = clamp(0.88 * state.op + 0.12 * suggestedOp + rand(-0.35, 0.35), 0, 100);
    } else if (mode === "MAN") {
      state.op = clamp(state.op + 0.04 * Math.sin(second / 55) + rand(-0.08, 0.08), 0, 100);
    }

    let disturbance = 0;
    if (loop.scenario === "disturbance" && second >= 1800 && second <= 2280) {
      disturbance = (loop.pv_max - loop.pv_min) * 0.055 * Math.exp(-(second - 1800) / 380);
    }
    if (loop.scenario === "oscillation") {
      disturbance += (loop.pv_max - loop.pv_min) * 0.018 * Math.sin((2 * Math.PI * second) / 260 + 0.7);
    }

    const targetPv = sp + (state.op - 50) * loop.process_gain * 0.06 + disturbance;
    state.pv += (targetPv - state.pv) / loop.time_constant_s + rand(-scenarioNoise, scenarioNoise);

    if (quality === "BAD") {
      state.pv += rand(-1, 1) * (loop.pv_max - loop.pv_min) * 0.035;
    }
    if (quality === "FROZEN") {
      if (state.frozenPv === null) state.frozenPv = state.pv;
      state.pv = state.frozenPv;
    } else {
      state.frozenPv = null;
    }

    state.pv = clamp(state.pv, loop.pv_min, loop.pv_max);
    const marker = eventMarker(loop, second);
    if (marker) {
      events.push({
        timestamp,
        second,
        loop_id: loop.loop_id,
        loop_tag: loop.loop_tag,
        event_marker: marker,
        mode,
        quality,
      });
    }
    stream.write([
      timestamp,
      second,
      loop.loop_id,
      loop.loop_tag,
      loop.unit_name,
      loop.loop_group,
      loop.control_type,
      loop.scenario,
      round(state.pv),
      round(sp),
      round(state.op),
      mode,
      loop.p,
      loop.i,
      loop.d,
      loop.engineering_unit,
      quality,
      marker,
    ].join(",") + "\n");
  });
}

stream.end();

const metadata = {
  dataset_id: "CLPM-DEMO-SECOND-LEVEL-20260616",
  generated_at: "2026-06-16T00:00:00+08:00",
  sample_start_time: "2026-06-16T08:00:00+08:00",
  sample_end_time: "2026-06-16T09:00:00+08:00",
  duration_seconds: DURATION_SECONDS,
  sample_interval_seconds: 1,
  loop_count: loops.length,
  row_count: loops.length * (DURATION_SECONDS + 1),
  fields: [
    "timestamp",
    "second",
    "loop_id",
    "loop_tag",
    "unit_name",
    "loop_group",
    "control_type",
    "scenario",
    "pv",
    "sp",
    "op",
    "mode",
    "p",
    "i",
    "d",
    "engineering_unit",
    "quality",
    "event_marker",
  ],
  safety_boundary: "Demo data only. No DCS read/write. PID values are simulated parameters.",
  loops,
};

writeFileSync(join(OUT_DIR, "loops_metadata.json"), `${JSON.stringify(metadata, null, 2)}\n`, "utf8");
writeFileSync(join(OUT_DIR, "events.csv"), [
  "timestamp,second,loop_id,loop_tag,event_marker,mode,quality",
  ...events.map((event) => [
    event.timestamp,
    event.second,
    event.loop_id,
    event.loop_tag,
    event.event_marker,
    event.mode,
    event.quality,
  ].join(",")),
  "",
].join("\n"), "utf8");

const scenarioSummary = loops.reduce((acc, loop) => {
  acc[loop.scenario] = (acc[loop.scenario] || 0) + 1;
  return acc;
}, {});

writeFileSync(join(OUT_DIR, "dataset_summary.json"), `${JSON.stringify({
  dataset_id: metadata.dataset_id,
  csv_file: "control_loop_second_level_24loops_1h.csv",
  metadata_file: "loops_metadata.json",
  events_file: "events.csv",
  sample_start_time: metadata.sample_start_time,
  sample_end_time: metadata.sample_end_time,
  duration_seconds: metadata.duration_seconds,
  sample_interval_seconds: metadata.sample_interval_seconds,
  loop_count: metadata.loop_count,
  row_count: metadata.row_count,
  fields: metadata.fields,
  scenario_summary: scenarioSummary,
}, null, 2)}\n`, "utf8");

console.log(`Generated ${metadata.row_count} rows for ${metadata.loop_count} loops`);
console.log(`CSV: ${csvPath}`);
