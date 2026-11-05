// SIEVE desktop dashboard: policy timeline, context matrix grid, dynamics,
// simulator, activity feed, setup. Design language = the host app's own SDK
// (theme vars, native components). Dense but breathing; semantic badges only.
import { jsx } from "react/jsx-runtime";
import { useState } from "react";
import {
  useQuery, useQueryClient, Button, Badge, Input, EmptyState, ScrollArea, Separator, Skeleton,
} from "@hermes/plugin-sdk";

const POLL = 10000;
const TIER_LABEL = ["digest", "chip", "notify", "interrupt", "alarm", "siren"];

function useData(ctx, key, path, interval) {
  return useQuery({
    queryKey: [ctx.source, key],
    queryFn: () => ctx.rest(path),
    refetchInterval: interval,
  });
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
    const s = parseInt(w.start.split(":")[0], 10);
    const e = parseInt(w.end.split(":")[0], 10);
    return s < e ? h >= s && h < e : h >= s || h < e;
  });
  return jsx("div", {
    style: { display: "flex", gap: "2px", alignItems: "flex-end", height: "34px" },
    children: Array.from({ length: 24 }, (_, h) =>
      jsx("div", {
        key: h,
        title: `${String(h).padStart(2, "0")}:00${covered(h) ? " quiet" : ""}`,
        style: {
          flex: 1, height: h === nowHour ? "34px" : (covered(h) ? "22px" : "12px"),
          borderRadius: "3px",
          background: h === nowHour ? "var(--ui-accent)"
            : covered(h) ? "var(--ui-stroke-secondary)" : "var(--ui-stroke-tertiary, var(--ui-stroke-secondary))",
          opacity: h === nowHour ? 1 : 0.7,
        },
      })),
  });
}

function MatrixTab({ status, mx, onCell, onSimulate, sim }) {
  const contexts = (mx && mx.contexts) || [];
  const classes = (mx && mx.classes) || [];
  return jsx("div", {
    style: { display: "flex", flexDirection: "column", gap: "12px" },
    children: [
      jsx("div", {
        key: "tl",
        children: [
          jsx("div", {
            key: "h",
            style: { fontSize: "11px", color: "var(--ui-text-quaternary)", marginBottom: "6px" },
            children: `24h policy clock${(status.windows || []).length ? "" : " (no quiet windows)"}`,
          }),
          jsx(Timeline, { key: "t", windows: status.windows || [] }),
        ],
      }),
      jsx("div", {
        key: "grid",
        style: { display: "flex", flexDirection: "column", gap: "4px" },
        children: [
          jsx("div", {
            key: "hdr",
            style: { display: "flex", gap: "4px" },
            children: [
              jsx("div", { key: "c0", style: { width: "120px" } }),
              ...contexts.map(([name]) => jsx("div", {
                key: name,
                style: { flex: 1, fontSize: "10px", color: "var(--ui-text-quaternary)", textAlign: "center" },
                children: name,
              })),
            ],
          }),
          ...classes.map((cls) =>
            jsx("div", { key: cls, style: { display: "flex", gap: "4px", alignItems: "center" },
              children: [
                jsx("div", {
                  key: "l",
                  style: { width: "120px", fontSize: "11px", color: "var(--ui-text-secondary)" },
                  children: cls,
                }),
                ...contexts.map(([name]) => {
                  const tier = (mx.grid[cls] || {})[name] ?? 2;
                  return jsx("button", {
                    key: name,
                    style: tierStyle(tier),
                    onClick: () => onCell(cls, name, tier),
                    title: `${cls} in ${name}: T${tier} (${TIER_LABEL[tier]})`,
                    children: `T${tier}`,
                  });
                }),
              ],
            })),
          jsx("div", {
            key: "hint",
            style: { fontSize: "10px", color: "var(--ui-text-quaternary)", marginTop: "4px" },
            children: "Click a cell to cycle its tier. A context column overrides the base row when that context is active.",
          }),
        ],
      }),
      jsx(Separator, { key: "s" }),
      jsx("div", {
        key: "sim",
        children: [
          jsx("div", {
            key: "h",
            style: { fontSize: "11px", color: "var(--ui-text-quaternary)", marginBottom: "6px" },
            children: "Simulator (dry run, no side effects)",
          }),
          jsx("div", { key: "row", style: { display: "flex", gap: "6px", flexWrap: "wrap" },
            children: classes.map((cls) => jsx(Button, {
              key: cls, onClick: () => onSimulate(cls), children: cls,
            })),
          }),
          sim ? jsx("div", {
            key: "out",
            style: { marginTop: "8px", fontSize: "12px", color: "var(--ui-text-primary)" },
            children: `${sim.event_class} [${sim.context}]: T${sim.tier} -> ${sim.action} | ${sim.rule}` +
              (sim.reason ? ` | ${sim.reason}` : ""),
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
  const onSimulate = (cls) =>
    ctx.rest("/simulate", {
      method: "POST",
      body: { event_class: cls, context: (cfg.data || {}).active_context || "base" },
    }).then((r) => setSim({ ...r, event_class: cls, context: (cfg.data || {}).active_context || "base" }));
  const onSave = (patch) => ctx.rest("/config", { method: "POST", body: patch }).then(invalidate);

  const s = status.data || {};
  const tabs = [["matrix", "Matrix"], ["dynamics", "Dynamics"], ["activity", "Activity"], ["setup", "Setup"]];

  return jsx("div", {
    style: { display: "flex", flexDirection: "column", height: "100%", padding: "12px", gap: "10px" },
    children: [
      jsx("div", {
        key: "head",
        style: { display: "flex", alignItems: "center", gap: "8px" },
        children: [
          jsx("strong", { key: "t", children: "SIEVE" }),
          jsx(Badge, { key: "h", children: `${s.held ?? 0} held` }),
          s.paused_until ? jsx(Badge, { key: "p", children: "paused" }) : null,
          s.degraded ? jsx(Badge, { key: "d", children: "degraded" }) : null,
          jsx("span", { key: "sp", style: { flex: 1 } }),
          jsx(Button, {
            key: "flush",
            onClick: () => ctx.rest("/cmd", { method: "POST", body: { args: "flush" } }).then(invalidate),
            children: "Flush",
          }),
        ],
      }),
      jsx("div", {
        key: "tabs",
        style: { display: "flex", gap: "4px" },
        children: tabs.map(([id, label]) => jsx(Button, {
          key: id,
          onClick: () => setTab(id),
          children: tab === id ? `[${label}]` : label,
        })),
      }),
      jsx(ScrollArea, {
        key: "body",
        style: { flex: "1" },
        children: status.isLoading
          ? jsx(Skeleton, { style: { height: "200px" } })
          : tab === "matrix"
            ? jsx(MatrixTab, { status: s, mx: mx.data, sim, onCell, onSimulate })
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
  return jsx("span", {
    style: { display: "inline-flex", alignItems: "center", gap: "6px", cursor: "pointer" },
    title: "SIEVE held items",
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
