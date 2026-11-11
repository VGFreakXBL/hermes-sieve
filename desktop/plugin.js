// SIEVE desktop dashboard: policy timeline, context matrix grid, dynamics,
// simulator, activity feed, setup. Design language = the host app's own SDK
// (theme vars, native components). Dense but breathing; semantic badges only.
import { jsx } from "react/jsx-runtime";
import { useEffect, useRef, useState } from "react";
import {
  useQuery, useQueryClient, Button, Badge, Input, EmptyState, ScrollArea, Separator, Skeleton, host,
} from "@hermes/plugin-sdk";

const PANE_ID = "sieve:sieve-pane";

function openPane() {
  if (host && typeof host.revealPane === "function") host.revealPane(PANE_ID);
}

const POLL = 10000;
const TIER_LABEL = ["digest", "chip", "notify", "interrupt", "alarm", "top"];

function useData(ctx, key, path, interval) {
  return useQuery({
    queryKey: [ctx.source, key],
    queryFn: () => ctx.rest(path),
    refetchInterval: interval,
  });
}

const GRID_MIN = 560;

function useWidth() {
  const ref = useRef(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const measure = () => setWidth(el.clientWidth || 0);
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

function tierStyle(tier) {
  // one accent family; intensity carries the tier, never a rainbow
  const base = {
    cursor: "pointer", minWidth: "34px", textAlign: "center", borderRadius: "6px",
    padding: "2px 6px", fontSize: "11px", border: "1px solid var(--ui-stroke-secondary)",
    color: "var(--ui-text-secondary)", background: "transparent",
  };
  if (tier >= 4) return { ...base, color: "var(--ui-accent)", borderColor: "var(--ui-accent)" };
  if (tier >= 2) return { ...base, color: "var(--ui-text-primary)" };
  return base;
}

function Timeline({ windows }) {
  const nowHour = new Date().getHours();
  const covered = (h) => (windows || []).some((w) => {
    const s = parseInt(String(w.start).split(":")[0], 10);
    const e = parseInt(String(w.end).split(":")[0], 10);
    if (Number.isNaN(s) || Number.isNaN(e)) return false;
    return s < e ? h >= s && h < e : h >= s || h < e;
  });
  const ticks = [0, 6, 12, 18];
  return jsx("div", {
    style: { width: "100%", minWidth: 0 },
    children: [
      jsx("div", {
        key: "bars",
        style: { display: "flex", gap: "2px", alignItems: "flex-end", height: "34px", width: "100%", minWidth: 0 },
        children: Array.from({ length: 24 }, (_, h) =>
          jsx("div", {
            key: h,
            title: `${String(h).padStart(2, "0")}:00${covered(h) ? " silent" : ""}${h === nowHour ? " (now)" : ""}`,
            style: {
              flex: "1 1 0", minWidth: 0, height: h === nowHour ? "34px" : (covered(h) ? "22px" : "10px"),
              borderRadius: "3px",
              background: h === nowHour ? "var(--ui-accent)"
                : covered(h) ? "var(--ui-text-secondary)" : "var(--ui-stroke-secondary)",
              opacity: h === nowHour ? 1 : covered(h) ? 0.85 : 0.35,
            },
          })),
      }),
      jsx("div", {
        key: "ticks",
        style: { display: "flex", marginTop: "4px", width: "100%" },
        children: ticks.map((h) => jsx("div", {
          key: h,
          style: {
            width: "25%", fontSize: "10px", color: "var(--ui-text-quaternary)",
            textAlign: h === 0 ? "left" : h === 18 ? "right" : "center",
          },
          children: h === nowHour ? `${h} now` : String(h),
        })),
      }),
    ],
  });
}

function cellButton(cls, name, tier, onCell, compact) {
  return jsx("button", {
    key: name,
    style: compact
      ? { ...tierStyle(tier), flex: "1 1 88px", minWidth: 0, maxWidth: "100%", display: "flex", flexDirection: "column", gap: "1px" }
      : { ...tierStyle(tier), flex: "1 1 0", minWidth: 0 },
    onClick: () => onCell(cls, name, tier),
    title: `${cls} in ${name}: T${tier} (${TIER_LABEL[tier]})`,
    children: compact
      ? [
          jsx("span", { key: "n", style: { fontSize: "9px", lineHeight: "1.2", overflowWrap: "anywhere" }, children: name }),
          jsx("span", { key: "t", children: `T${tier}` }),
        ]
      : `T${tier}`,
  });
}

function MatrixTab({ status, mx, onCell, onSimulate, sim, simBusy, simError, onQuiet, onSnooze, onRemoveQuiet, onTest, alarm }) {
  const contexts = (mx && mx.contexts) || [];
  const classes = (mx && mx.classes) || [];
  const [widthRef, width] = useWidth();
  const compact = width === 0 || width < GRID_MIN;
  return jsx("div", {
    ref: widthRef,
    style: { display: "flex", flexDirection: "column", gap: "12px", width: "100%", minWidth: 0 },
    children: [
      jsx("div", {
        key: "tl",
        style: { minWidth: 0 },
        children: [
          jsx("div", {
            key: "h",
            style: { fontSize: "11px", color: "var(--ui-text-quaternary)", marginBottom: "6px" },
            children: "Silent hours clock",
          }),
          jsx(Timeline, { key: "t", windows: status.windows || [] }),
          jsx("div", {
            key: "cap",
            style: { fontSize: "11px", color: "var(--ui-text-secondary)", marginTop: "6px", overflowWrap: "anywhere" },
            children: "Tall bar is now. Filled bars are silent hours: anything below the floor waits for the digest. Alarms still break through unless you set the floor to none.",
          }),
          jsx("div", {
            key: "acts",
            style: { display: "flex", gap: "6px", flexWrap: "wrap", marginTop: "8px" },
            children: [
              jsx(Button, { key: "sleep", onClick: () => onQuiet({ start: "23:00", end: "08:00", days: "0123456", floor: 4, label: "sleep" }), children: "Sleep 11pm-8am" }),
              jsx(Button, { key: "snooze", onClick: () => onSnooze("1h"), children: "Snooze 1h" }),
              jsx(Button, { key: "morn", onClick: () => onSnooze("8am"), children: "Snooze until 8am" }),
            ],
          }),
          (status.windows || []).length
            ? jsx("div", {
                key: "wins",
                style: { marginTop: "8px", display: "flex", flexDirection: "column", gap: "4px" },
                children: (status.windows || []).map((w) => jsx("div", {
                  key: `w${w.id}`,
                  style: { display: "flex", justifyContent: "space-between", gap: "8px", fontSize: "12px", color: "var(--ui-text-secondary)" },
                  children: [
                    jsx("span", { key: "l", children: `${w.label || "silent"} ${w.start}-${w.end} floor T${w.floor === 6 ? "none" : w.floor}` }),
                    jsx(Button, { key: "x", onClick: () => onRemoveQuiet(w.id), children: "Remove" }),
                  ],
                })),
              })
            : jsx("div", {
                key: "empty",
                style: { fontSize: "11px", color: "var(--ui-text-quaternary)", marginTop: "6px" },
                children: "No silent hours yet. The clock stays empty until you set one.",
              }),
        ],
      }),
      compact
        ? jsx("div", {
            key: "stack",
            style: { display: "flex", flexDirection: "column", gap: "10px", minWidth: 0 },
            children: classes.map((cls) => jsx("div", {
              key: cls,
              style: { minWidth: 0 },
              children: [
                jsx("div", {
                  key: "l",
                  style: { fontSize: "11px", color: "var(--ui-text-secondary)", marginBottom: "4px" },
                  children: cls,
                }),
                jsx("div", {
                  key: "cells",
                  style: { display: "flex", flexWrap: "wrap", gap: "4px", minWidth: 0 },
                  children: contexts.map(([name]) => cellButton(cls, name, (mx.grid[cls] || {})[name] ?? 2, onCell, true)),
                }),
              ],
            })),
          })
        : jsx("div", {
            key: "grid",
            style: { display: "flex", flexDirection: "column", gap: "4px", minWidth: 0, width: "100%" },
            children: [
              jsx("div", {
                key: "hdr",
                style: { display: "flex", gap: "4px", minWidth: 0 },
                children: [
                  jsx("div", { key: "c0", style: { width: "108px", flex: "0 0 108px" } }),
                  ...contexts.map(([name]) => jsx("div", {
                    key: name,
                    style: {
                      flex: "1 1 0", minWidth: 0, fontSize: "10px", lineHeight: "1.2",
                      color: "var(--ui-text-quaternary)", textAlign: "center", overflowWrap: "anywhere",
                    },
                    children: name,
                  })),
                ],
              }),
              ...classes.map((cls) =>
                jsx("div", { key: cls, style: { display: "flex", gap: "4px", alignItems: "center", minWidth: 0 },
                  children: [
                    jsx("div", {
                      key: "l",
                      style: { width: "108px", flex: "0 0 108px", fontSize: "11px", color: "var(--ui-text-secondary)", overflowWrap: "anywhere" },
                      children: cls,
                    }),
                    ...contexts.map(([name]) => cellButton(cls, name, (mx.grid[cls] || {})[name] ?? 2, onCell, false)),
                  ],
                })),
            ],
          }),
      jsx("div", {
        key: "hint",
        style: { fontSize: "10px", color: "var(--ui-text-quaternary)", marginTop: "4px", overflowWrap: "anywhere" },
        children: "Click a cell to cycle its tier. A context column overrides the base row when that context is active.",
      }),
      jsx(Separator, { key: "s" }),
      jsx("div", {
        key: "sim",
        style: { minWidth: 0 },
        children: [
          jsx("div", {
            key: "h",
            style: { fontSize: "11px", color: "var(--ui-text-quaternary)", marginBottom: "6px" },
            children: "Preview (does not ring the phone)",
          }),
          jsx("div", {
            key: "card",
            style: {
              border: "1px solid var(--ui-stroke-secondary)",
              borderRadius: "8px",
              padding: "8px 10px",
              marginBottom: "8px",
              fontSize: "12px",
              color: "var(--ui-text-primary)",
              overflowWrap: "anywhere",
            },
            children: simBusy
              ? "Checking..."
              : simError
                ? `Preview failed: ${simError}`
                : sim
                  ? `${sim.event_class} in ${sim.context}: T${sim.tier} would ${sim.action}. ${sim.rule || ""}${sim.reason ? " " + sim.reason : ""}. ${sim.note || ""}`
                  : "Pick an event below. This only predicts the tier. It does not queue an alarm.",
          }),
          jsx("div", { key: "row", style: { display: "flex", gap: "6px", flexWrap: "wrap", minWidth: 0, width: "100%" },
            children: classes.map((cls) => jsx(Button, {
              key: cls, onClick: () => onSimulate(cls), children: cls,
            })),
          }),
          jsx("div", {
            key: "test",
            style: { display: "flex", gap: "6px", flexWrap: "wrap", marginTop: "10px", alignItems: "center" },
            children: [
              jsx("span", { key: "l", style: { fontSize: "11px", color: "var(--ui-text-quaternary)" }, children: "Real phone test" }),
              jsx(Button, { key: "t4", onClick: () => onTest(4), children: "Test pleasant alarm" }),
              jsx(Button, { key: "t5", onClick: () => onTest(5), children: "Test T5" }),
            ],
          }),
          alarm ? jsx("div", {
            key: "alarm",
            style: { marginTop: "8px", fontSize: "12px", color: "var(--ui-text-primary)", overflowWrap: "anywhere" },
            children: alarm,
          }) : null,
        ],
      }),
    ],
  });
}

function DynamicsTab({ status, cfg, onSave }) {
  const [budget, setBudget] = useState(cfg.budget_per_hour || "");
  const [promo, setPromo] = useState(cfg.promo_minutes || "");
  const context = cfg.active_context || "base";
  return jsx("div", {
    style: { display: "flex", flexDirection: "column", gap: "14px" },
    children: [
      jsx("div", { key: "budget", children: [
        jsx("div", { key: "l", style: { fontSize: "11px", color: "var(--ui-text-quaternary)" },
          children: "Attention budget: max interrupts per hour (0 = off)" }),
        jsx("div", { key: "r", style: { display: "flex", gap: "6px", marginTop: "4px" },
          children: [
            jsx(Input, { key: "i", value: budget, onChange: (e) => setBudget(e.target.value), placeholder: "e.g. 6" }),
            jsx(Button, { key: "b", onClick: () => onSave({ budget_per_hour: budget }), children: "Apply" }),
          ] }),
      ] }),
      jsx("div", { key: "promo", children: [
        jsx("div", { key: "l", style: { fontSize: "11px", color: "var(--ui-text-quaternary)" },
          children: "Promotion ladder: held items escalate one tier after N minutes (0 = off)" }),
        jsx("div", { key: "r", style: { display: "flex", gap: "6px", marginTop: "4px" },
          children: [
            jsx(Input, { key: "i", value: promo, onChange: (e) => setPromo(e.target.value), placeholder: "e.g. 30" }),
            jsx(Button, { key: "b", onClick: () => onSave({ promo_minutes: promo }), children: "Apply" }),
          ] }),
      ] }),
      jsx("div", { key: "ctx", children: [
        jsx("div", { key: "l", style: { fontSize: "11px", color: "var(--ui-text-quaternary)" },
          children: "Active context (auto-detection sources vary; this forces one)" }),
        jsx("div", { key: "r", style: { display: "flex", gap: "6px", marginTop: "4px", flexWrap: "wrap" },
          children: ["base", "meeting", "commuting", "vacation", "streaming", "sleeping", "deep_work"].map((c) =>
            jsx(Button, {
              key: c,
              onClick: () => onSave({ active_context: c }),
              children: c === context ? `[${c}]` : c,
            })),
        }),
      ] }),
      jsx(Separator, { key: "s1" }),
      jsx("div", { key: "wins", children: [
        jsx("div", { key: "h", style: { fontSize: "11px", color: "var(--ui-text-quaternary)" },
          children: "Quiet windows" }),
        (status.windows || []).length === 0
          ? jsx(EmptyState, { key: "e", description: "None. In chat: sieve quiet 23:00-08:00 floor=T4 weekdays" })
          : jsx("div", { key: "l" }, ...(status.windows || []).map((w, i) => jsx("div", {
              key: `w${i}`,
              style: { fontSize: "12px", color: "var(--ui-text-secondary)", padding: "2px 0" },
              children: `${w.start}-${w.end} [${w.days}] floor T${w.floor === 6 ? "none" : w.floor}${w.label ? " " + w.label : ""}`,
            }))),
      ] }),
      (status.mutes || []).length ? jsx("div", { key: "mutes", children: [
        jsx("div", { key: "h", style: { fontSize: "11px", color: "var(--ui-text-quaternary)" },
          children: "Active mutes" }),
        jsx("div", { key: "l" }, ...(status.mutes || []).map((m, i) => jsx("div", {
          key: `m${i}`,
          style: { fontSize: "12px", color: "var(--ui-text-secondary)", padding: "2px 0" },
          children: `T${m.tier} muted until ${m.until}`,
        }))),
      ] }) : null,
    ],
  });
}

function ActivityTab({ why, stats }) {
  return jsx("div", {
    style: { display: "flex", flexDirection: "column", gap: "12px" },
    children: [
      jsx("div", { key: "feed", children: [
        jsx("div", { key: "h", style: { fontSize: "11px", color: "var(--ui-text-quaternary)", marginBottom: "6px" },
          children: "Decisions" }),
        jsx(ScrollArea, {
          key: "l",
          style: { maxHeight: "240px" },
          children: ((why && why.events) || []).length === 0
            ? jsx(EmptyState, { description: "No events routed yet." })
            : jsx("div", {}, ...((why && why.events) || []).map((e) => jsx("div", {
                key: `e${e.id}`,
                style: { display: "flex", justifyContent: "space-between", gap: "8px", padding: "3px 0", fontSize: "11px" },
                children: [
                  jsx("span", { key: "a", style: { color: "var(--ui-text-primary)" },
                    children: `${e.event_class} T${e.tier} -> ${e.action}` }),
                  jsx("span", { key: "b", style: { color: "var(--ui-text-quaternary)" }, children: e.detail || "" }),
                ],
              }))),
        }),
      ] }),
      jsx(Separator, { key: "s" }),
      jsx("div", { key: "stats", children: [
        jsx("div", { key: "h", style: { fontSize: "11px", color: "var(--ui-text-quaternary)", marginBottom: "6px" },
          children: "Rule performance (total / delivered / held)" }),
        ((stats && stats.rules) || []).length === 0
          ? jsx(EmptyState, { key: "e", description: "No rule activity yet." })
          : jsx("div", { key: "l" }, ...((stats && stats.rules) || []).map((r) => jsx("div", {
              key: `r${r.rule_id}`,
              style: { fontSize: "11px", color: "var(--ui-text-secondary)", padding: "2px 0" },
              children: `${r.rule_id}: ${r.total} / ${r.delivered} / ${r.held}`,
            }))),
      ] }),
    ],
  });
}

function SetupTab({ doctor }) {
  return jsx("div", {
    style: { display: "flex", flexDirection: "column", gap: "8px" },
    children: [
      ...(((doctor && doctor.checks) || []).map((c) => jsx("div", {
        key: c.name,
        style: { fontSize: "12px", color: c.ok ? "var(--ui-text-secondary)" : "var(--ui-accent)" },
        children: `${c.ok ? "ok" : "needs setup"}: ${c.name}${c.detail ? " (" + c.detail + ")" : ""}`,
      }))),
      doctor && doctor.note
        ? jsx("div", { style: { fontSize: "11px", color: "var(--ui-text-quaternary)" }, children: doctor.note })
        : null,
    ],
  });
}

function Pane({ ctx }) {
  const qc = useQueryClient();
  const [tab, setTab] = useState("matrix");
  const [sim, setSim] = useState(null);
  const [simBusy, setSimBusy] = useState(false);
  const [simError, setSimError] = useState("");
  const [alarm, setAlarm] = useState("");
  const status = useData(ctx, "status", "/status", POLL);
  const why = useData(ctx, "why", "/why", POLL);
  const mx = useData(ctx, "matrix", "/matrix", 30000);
  const stats = useData(ctx, "stats", "/stats", 30000);
  const cfg = useData(ctx, "config", "/config", 60000);
  const doctor = useData(ctx, "doctor", "/doctor", 60000);

  const invalidate = () => qc.invalidateQueries({ queryKey: [ctx.source] });
  const onCell = (cls, context, tier) =>
    ctx.rest("/matrix/cell", {
      method: "POST",
      body: { event_class: cls, context, tier: (tier + 1) % 6 },
    }).then(invalidate);
  const onSimulate = (cls) => {
    const context = (cfg.data || {}).active_context || "base";
    setSimBusy(true);
    setSimError("");
    const q = `/simulate?event_class=${encodeURIComponent(cls)}&context=${encodeURIComponent(context)}`;
    ctx.rest(q).then((r) => {
      setSim(r && typeof r === "object" ? { ...r, event_class: r.event_class || cls, context: r.context || context } : null);
      if (!r || typeof r.tier !== "number") setSimError("No prediction came back. Reload desktop plugins, then try again.");
    }).catch((err) => setSimError(String(err && err.message ? err.message : err)))
      .finally(() => setSimBusy(false));
  };
  const onQuiet = (body) => ctx.rest("/quiet", { method: "POST", body }).then(invalidate);
  const onRemoveQuiet = (id) => ctx.rest("/quiet/remove", { method: "POST", body: { id } }).then(invalidate);
  const onSnooze = (until) => ctx.rest("/cmd", { method: "POST", body: { args: `pause ${until}` } }).then(invalidate);
  const onTest = (tier) => {
    setAlarm("Sending test to the phone...");
    ctx.rest("/cmd", { method: "POST", body: { args: `test T${tier}` } })
      .then((r) => setAlarm((r && (r.result || r.text)) || JSON.stringify(r)))
      .catch((err) => setAlarm(String(err && err.message ? err.message : err)));
  };
  const onSave = (patch) => ctx.rest("/config", { method: "POST", body: patch }).then(invalidate);

  const s = status.data || {};
  const tabs = [["matrix", "Matrix"], ["dynamics", "Dynamics"], ["activity", "Activity"], ["setup", "Setup"]];

  return jsx("div", {
    style: { display: "flex", flexDirection: "column", height: "100%", width: "100%", minWidth: 0, boxSizing: "border-box", padding: "12px", gap: "10px", overflow: "hidden" },
    children: [
      jsx("div", {
        key: "head",
        style: { display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap", minWidth: 0 },
        children: [
          jsx("strong", { key: "t", children: "SIEVE" }),
          jsx(Badge, { key: "h", children: `${s.held ?? 0} held` }),
          s.paused_until ? jsx(Badge, { key: "p", children: "paused" }) : null,
          s.degraded ? jsx(Badge, { key: "d", children: "degraded" }) : null,
          jsx("span", { key: "sp", style: { flex: 1 } }),
          jsx(Button, {
            key: "snooze",
            onClick: () => ctx.rest("/cmd", { method: "POST", body: { args: "pause 1h" } }).then(invalidate),
            children: "Snooze 1h",
          }),
          jsx(Button, {
            key: "flush",
            onClick: () => ctx.rest("/cmd", { method: "POST", body: { args: "flush" } }).then(invalidate),
            children: "Flush",
          }),
        ],
      }),
      jsx("div", {
        key: "tabs",
        style: { display: "flex", gap: "4px", flexWrap: "wrap", minWidth: 0 },
        children: tabs.map(([id, label]) => jsx(Button, {
          key: id,
          onClick: () => setTab(id),
          children: tab === id ? `[${label}]` : label,
        })),
      }),
      jsx(ScrollArea, {
        key: "body",
        style: { flex: "1", minWidth: 0, width: "100%" },
        children: status.isLoading
          ? jsx(Skeleton, { style: { height: "200px" } })
          : tab === "matrix"
            ? jsx(MatrixTab, {
                status: s, mx: mx.data, sim, simBusy, simError, alarm,
                onCell, onSimulate, onQuiet, onSnooze, onRemoveQuiet, onTest,
              })
            : tab === "dynamics"
              ? jsx(DynamicsTab, { status: s, cfg: cfg.data || {}, onSave })
              : tab === "activity"
                ? jsx(ActivityTab, { why: why.data, stats: stats.data })
                : jsx(SetupTab, { doctor: doctor.data }),
      }),
    ],
  });
}

function Chip({ ctx }) {
  const status = useData(ctx, "status", "/status", POLL);
  const held = status.data && typeof status.data.held === "number" ? status.data.held : 0;
  return jsx("button", {
    type: "button",
    onClick: openPane,
    title: "Open SIEVE",
    style: {
      display: "inline-flex", alignItems: "center", gap: "6px", cursor: "pointer",
      background: "transparent", border: 0, padding: "0 4px", color: "inherit",
      font: "inherit",
    },
    children: [
      jsx("span", { key: "t", children: "SIEVE" }),
      jsx(Badge, { key: "b", children: String(held) }),
    ],
  });
}

export default {
  id: "sieve",
  name: "SIEVE",
  register(ctx) {
    ctx.register({ id: "sieve-chip", area: "statusBar.right", render: () => jsx(Chip, { ctx }) });
    ctx.register({
      id: "sieve-pane",
      area: "panes",
      data: { title: "SIEVE", placement: "right", height: "560px" },
      render: () => jsx(Pane, { ctx }),
    });
  },
};
