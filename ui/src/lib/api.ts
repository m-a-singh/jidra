const BASE = "/api";

async function get<T>(path: string, params?: Record<string, string | number | boolean | undefined>): Promise<T> {
  const url = new URL(path, window.location.origin);
  url.pathname = BASE + url.pathname;
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined) url.searchParams.set(k, String(v));
    }
  }
  const res = await fetch(url.toString());
  if (!res.ok) throw new Error((await res.json()).detail ?? res.statusText);
  return res.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error((await res.json()).detail ?? res.statusText);
  return res.json();
}

function sse(path: string, body: unknown, onEvent: (event: string, data: unknown) => void): () => void {
  const ctrl = new AbortController();
  (async () => {
    const res = await fetch(BASE + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    if (!res.body) return;
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop() ?? "";
      for (const chunk of parts) {
        let event = "message";
        let data = "";
        for (const line of chunk.split("\n")) {
          if (line.startsWith("event: ")) event = line.slice(7);
          if (line.startsWith("data: ")) data = line.slice(6);
        }
        try { onEvent(event, JSON.parse(data)); } catch { onEvent(event, data); }
      }
    }
  })().catch(() => {});
  return () => ctrl.abort();
}

export const api = {
  health: () => get<{ status: string }>("/health"),

  index: {
    status: (repo_path: string, output_path?: string) =>
      get<{ indexed: boolean; variant?: string; node_count?: number; class_count?: number; validated?: boolean }>("/index/status", { repo_path, output_path }),
    run: (body: {
      repo_path: string; output_path?: string; actuator_url?: string;
      port?: number; timeout?: number; skip_build?: boolean;
      build_dir?: string; use_docker?: boolean; write_mcp_config?: boolean;
    }, cb: (e: string, d: unknown) => void) => sse("/index/run", body, cb),
  },

  graph: {
    nodes: (params: { repo_path: string; output_path?: string; method?: string; depth?: number; package?: string; language?: string; limit?: number }) =>
      get<{ nodes: unknown[]; edges: unknown[]; truncated: boolean }>("/graph/nodes", params),
    node: (node_id: string, repo_path: string) =>
      get<Record<string, unknown>>(`/graph/node/${encodeURIComponent(node_id)}`, { repo_path }),
    search: (params: { repo_path: string; q: string; language?: string; limit?: number }) =>
      get<Record<string, unknown>>("/graph/search", params),
  },

  sql: {
    schema: (repo_path: string, db = "graph") => get<{ table: string; columns: { name: string; type: string }[] }[]>("/sql/schema", { repo_path, db }),
    query: (body: { repo_path: string; sql: string; db?: string }) =>
      post<{ columns: string[]; rows: unknown[][]; truncated: boolean }>("/sql/query", body),
  },

  mcp: {
    tools: (repo_path?: string) => get<{ name: string; description: string; input_schema: { properties?: Record<string, { title?: string; type?: string; anyOf?: { type: string }[]; default?: unknown; description?: string }>; required?: string[] } }[]>("/mcp/tools", repo_path ? { repo_path } : undefined),
    call: (body: { tool: string; params: Record<string, unknown>; repo_path?: string; output_path?: string }) =>
      post<{ result: unknown }>("/mcp/call", body),
    sessionLog: (repo_path: string, limit = 100) => get<{ tool_name: string; method_id?: string; timestamp: string }[]>("/mcp/session-log", { repo_path, limit }),
  },
};
