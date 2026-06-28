interface LogLine {
  text: string;
  kind: "ok" | "err" | "warn" | "run" | "plain";
}

export function OutputLog({ logs }: { logs: LogLine[] }) {
  if (logs.length === 0) {
    return (
      <div className="log-pane" style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: 160 }}>
        <span style={{ color: "var(--text-dim)", fontSize: 13 }}>configure above and run the pipeline</span>
      </div>
    );
  }

  return (
    <div className="log-pane" style={{ padding: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
      {logs.map((line, i) => (
        <div
          key={i}
          style={{
            padding: "10px 14px",
            borderBottom: i < logs.length - 1 ? "0.5px solid var(--border)" : "none",
            display: "flex",
            gap: 10,
            alignItems: "flex-start",
            fontSize: 13,
            lineHeight: 1.5,
            animation: `slideInLeft 0.3s ease-out ${i * 0.05}s backwards`,
          }}
        >
          <div style={{ minWidth: 20, paddingTop: 2 }}>
            {line.kind === "ok" && <span style={{ color: "var(--success)" }}>✓</span>}
            {line.kind === "err" && <span style={{ color: "var(--error)" }}>✗</span>}
            {line.kind === "warn" && <span style={{ color: "var(--warn)" }}>!</span>}
            {line.kind === "run" && <span style={{ color: "var(--cyan)" }}>●</span>}
            {line.kind === "plain" && <span style={{ color: "var(--text-dim)" }}>·</span>}
          </div>
          <div
            style={{
              flex: 1,
              color:
                line.kind === "ok"
                  ? "var(--success)"
                  : line.kind === "err"
                    ? "var(--error)"
                    : line.kind === "warn"
                      ? "var(--warn)"
                      : line.kind === "run"
                        ? "var(--cyan)"
                        : "var(--text-muted)",
            }}
          >
            {line.text}
          </div>
        </div>
      ))}
    </div>
  );
}
