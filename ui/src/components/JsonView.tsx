export function JsonView({ value }: { value: unknown }) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  const lines = text.split("\n");
  return (
    <pre style={{
      background: "var(--bg)", border: "none",
      padding: "10px 16px", fontSize: "var(--sz-sm)", lineHeight: 1.7,
      overflow: "auto", whiteSpace: "pre-wrap", wordBreak: "break-all",
      margin: 0,
    }}>
      {lines.map((line, i) => {
        const keyMatch = line.match(/^(\s*)"([^"]+)"(\s*:)(.*)$/);
        if (keyMatch) {
          const [, indent, key, colon, rest] = keyMatch;
          const valColor = rest.trim().startsWith('"') ? "var(--text)"
            : /^[\d.-]+/.test(rest.trim()) ? "#6ab0f5"
            : rest.trim() === "true" || rest.trim() === "false" ? "#c49a2a"
            : rest.trim() === "null," || rest.trim() === "null" ? "var(--text-dim)"
            : "var(--text)";
          return (
            <span key={i}>
              {indent}
              <span style={{ color: "var(--cyan-dim)" }}>"{key}"</span>
              <span style={{ color: "var(--text-dim)" }}>{colon}</span>
              <span style={{ color: valColor }}>{rest}</span>
              {"\n"}
            </span>
          );
        }
        const isStructural = /^\s*[{}[\],]*\s*$/.test(line);
        return (
          <span key={i} style={{ color: isStructural ? "var(--border-strong)" : "var(--text-muted)" }}>
            {line}{"\n"}
          </span>
        );
      })}
    </pre>
  );
}
