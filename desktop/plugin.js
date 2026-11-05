// SIEVE desktop plugin: queued-N status chip + boundary pane (status/why/rules/doctor).
// Desktop SDK rules: @hermes/plugin-sdk + react only; jsx() calls (no JSX syntax);
// theme vars only; polling via the app's React Query (ctx.socket is a no-op on
// OAuth remotes, so polling is the reliable path).
import { jsx } from "react/jsx-runtime";
import {
  useQuery, useQueryClient, Button, Badge, EmptyState, ScrollArea, Separator,
} from "@hermes/plugin-sdk";

const POLL = 10000;

function Chip({ ctx }) {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: [ctx.source, "status"],
    queryFn: () => ctx.rest("/status"),
    refetchInterval: POLL,
  });
  const held = data && typeof data.held === "number" ? data.held : 0;
  return jsx("span", {
    style: { display: "inline-flex", alignItems: "center", gap: "6px", cursor: "pointer" },
    onClick: () => qc.invalidateQueries({ queryKey: [ctx.source] }),
    title: "SIEVE: held items / click to refresh",
    children: [
      jsx("span", { key: "t", children: "SIEVE" }),
      jsx(Badge, { key: "b", children: String(held) }),
      data && data.degraded ? jsx("span", { key: "w", children: "!" }) : null,
    ],
  });
}

function Row({ left, right }) {
  return jsx("div", {
    style: {
      display: "flex", justifyContent: "space-between", gap: "8px",
      padding: "4px 0", color: "var(--ui-text-secondary)", fontSize: "12px",
    },
    children: [
      jsx("span", { key: "l", style: { color: "var(--ui-text-primary)" }, children: left }),
      jsx("span", { key: "r", children: right }),
    ],
  });
}

function Pane({ ctx }) {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: [ctx.source, "status"], queryFn: () => ctx.rest("/status"), refetchInterval: POLL });
  const why = useQuery({ queryKey: [ctx.source, "why"], queryFn: () => ctx.rest("/why"), refetchInterval: POLL });
  const rules = useQuery({ queryKey: [ctx.source, "rules"], queryFn: () => ctx.rest("/rules") });
  const doctor = useQuery({ queryKey: [ctx.source, "doctor"], queryFn: () => ctx.rest("/doctor") });

  const s = status.data || {};
  const refresh = () => qc.invalidateQueries({ queryKey: [ctx.source] });
  const send = (args) =>
    ctx.rest("/cmd", { method: "POST", body: { args } }).then(refresh);

  return jsx("div", {
    style: { padding: "12px", display: "flex", flexDirection: "column", gap: "10px", height: "100%" },
    children: [
      jsx("div", {
        key: "head",
        style: { display: "flex", alignItems: "center", gap: "8px" },
        children: [
          jsx("strong", { key: "t", children: "SIEVE" }),
          jsx(Badge, { key: "h", children: `${s.held ?? 0} held` }),
          s.degraded ? jsx(Badge, { key: "d", children: "escalation degraded" }) : null,
          jsx("span", {
            key: "sp", style: { flex: 1 },
          }),
          jsx(Button, { key: "flush", onClick: () => send("flush"), children: "Flush digest" }),
          jsx(Button, { key: "pause", onClick: () => send("pause 2h"), children: "Pause 2h" }),
        ],
      }),
      jsx(Row, {
        key: "meta",
        left: `paused: ${s.paused_until || "no"}`,
        right: `target: ${s.target || "?"}`,
      }),
      jsx(Separator, { key: "s1" }),

      jsx("div", { key: "wh", children: "Quiet windows" }),
      (s.windows || []).length === 0
        ? jsx(EmptyState, {
            key: "we",
            description: "No quiet windows. Try: sieve quiet 23:00-08:00 floor=T4 weekdays",
          })
        : jsx("div", { key: "wl" }, ...(s.windows || []).map((w, i) =>
            jsx(Row, {
              key: `w${i}`,
              left: `${w.start}-${w.end} [${w.days}]`,
              right: `floor T${w.floor === 6 ? "none" : w.floor}${w.label ? " " + w.label : ""}`,
            }))),
      (s.mutes || []).length
        ? jsx("div", { key: "ml" }, ...(s.mutes || []).map((m, i) =>
            jsx(Row, { key: `m${i}`, left: `T${m.tier} muted`, right: `until ${m.until}` })))
        : null,
      jsx(Separator, { key: "s2" }),

      jsx("div", { key: "whh", children: "Decisions (why)" }),
      jsx(ScrollArea, {
        key: "why",
        style: { maxHeight: "180px" },
        children: ((why.data && why.data.events) || []).length === 0
          ? jsx(EmptyState, { key: "whe", description: "No events routed yet." })
          : jsx("div", {}, ...((why.data && why.data.events) || []).map((e) =>
              jsx(Row, {
                key: `e${e.id}`,
                left: `${e.event_class} T${e.tier} -> ${e.action}`,
                right: e.detail || "",
              }))),
      }),
      jsx(Separator, { key: "s3" }),

      jsx("div", { key: "ru", children: "Rules (user rules beat defaults)" }),
      jsx(ScrollArea, {
        key: "rules",
        style: { maxHeight: "160px" },
        children: jsx("div", {}, ...[
          ...((rules.data && rules.data.user) || []).map((r) =>
            jsx(Row, {
              key: `r${r.id}`,
              left: `#${r.id} ${r.event_class} T${r.tier}${r.enabled ? "" : " [off]"}`,
              right: r.label || r.match,
            })),
          ...((rules.data && rules.data.defaults) || []).map((r, i) =>
            jsx(Row, { key: `rd${i}`, left: `${r.event_class} T${r.tier} default`, right: r.label })),
        ]),
      }),
      jsx(Separator, { key: "s4" }),

      jsx("div", { key: "dc", children: "Setup" }),
      ...(((doctor.data && doctor.data.checks) || []).map((c, i) =>
        jsx(Row, { key: `c${i}`, left: `${c.ok ? "ok" : "missing"}: ${c.name}`, right: c.detail || "" }))),
      doctor.data && doctor.data.note
        ? jsx("div", {
            key: "dn",
            style: { color: "var(--ui-text-quaternary)", fontSize: "11px" },
            children: doctor.data.note,
          })
        : null,
    ],
  });
}

export default {
  id: "sieve",
  name: "SIEVE",
  register(ctx) {
    ctx.register({
      id: "sieve-chip",
      area: "statusBar.right",
      render: () => jsx(Chip, { ctx }),
    });
    ctx.register({
      id: "sieve-pane",
      area: "panes",
      data: { title: "SIEVE", placement: "right", height: "480px" },
      render: () => jsx(Pane, { ctx }),
    });
  },
};
