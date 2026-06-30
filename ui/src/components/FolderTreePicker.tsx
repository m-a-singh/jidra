import { useEffect, useState } from "react";
import { api } from "../lib/api";

interface FolderNode {
  name: string;
  path: string;
  default_excluded: boolean;
  expanded: boolean;
  loaded: boolean;
  children: FolderNode[];
}

async function loadChildren(repoPath: string, subpath: string): Promise<FolderNode[]> {
  const res = await api.index.listFolders(repoPath, subpath || undefined);
  return res.folders.map((f) => ({
    name: f.name,
    path: f.path,
    default_excluded: f.default_excluded,
    expanded: false,
    loaded: false,
    children: [],
  }));
}

function updateNode(nodes: FolderNode[], path: string, fn: (n: FolderNode) => FolderNode): FolderNode[] {
  return nodes.map((n) => {
    if (n.path === path) return fn(n);
    if (n.children.length && path.startsWith(n.path + "/")) {
      return { ...n, children: updateNode(n.children, path, fn) };
    }
    return n;
  });
}

function FolderRow({
  node, depth, repoPath, skipped, onToggleExpand, onToggleSkip,
}: {
  node: FolderNode; depth: number; repoPath: string; skipped: Set<string>;
  onToggleExpand: (path: string) => void; onToggleSkip: (path: string, skip: boolean) => void;
}) {
  const isSkipped = node.default_excluded || skipped.has(node.path);
  return (
    <div>
      <div
        className="flex items-center gap-2 py-1 text-sm text-text-muted hover:bg-surface-3 rounded"
        style={{ paddingLeft: `${depth * 18 + 8}px` }}
      >
        <button
          type="button"
          onClick={() => onToggleExpand(node.path)}
          className="w-4 text-xs text-text-faint cursor-pointer select-none"
        >
          {node.expanded ? "▾" : "▸"}
        </button>
        <label className="flex items-center gap-2 cursor-pointer select-none flex-1 min-w-0">
          <input
            type="checkbox"
            checked={isSkipped}
            disabled={node.default_excluded}
            onChange={(e) => onToggleSkip(node.path, e.target.checked)}
            className="accent-accent w-3.5 h-3.5 shrink-0"
          />
          <span className="truncate">{node.name}</span>
          {node.default_excluded && (
            <span className="text-xs text-text-faint shrink-0">excluded by default</span>
          )}
        </label>
      </div>
      {node.expanded && node.children.map((c) => (
        <FolderRow
          key={c.path} node={c} depth={depth + 1} repoPath={repoPath} skipped={skipped}
          onToggleExpand={onToggleExpand} onToggleSkip={onToggleSkip}
        />
      ))}
    </div>
  );
}

export function FolderTreePicker({
  repoPath, skipFolders, onChange,
}: {
  repoPath: string; skipFolders: string[]; onChange: (paths: string[]) => void;
}) {
  const [tree, setTree] = useState<FolderNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const skipped = new Set(skipFolders);

  useEffect(() => {
    if (!repoPath) { setTree([]); return; }
    setLoading(true);
    setError(null);
    loadChildren(repoPath, "")
      .then(setTree)
      .catch((e) => setError(String(e).replace("Error: ", "")))
      .finally(() => setLoading(false));
  }, [repoPath]);

  function toggleExpand(path: string) {
    setTree((t) => {
      const expand = async () => {
        const children = await loadChildren(repoPath, path);
        setTree((cur) => updateNode(cur, path, (n) => ({ ...n, expanded: true, loaded: true, children })));
      };
      const node = (function find(nodes: FolderNode[]): FolderNode | undefined {
        for (const n of nodes) {
          if (n.path === path) return n;
          const found = find(n.children);
          if (found) return found;
        }
        return undefined;
      })(t);
      if (node && !node.loaded) { void expand(); return t; }
      return updateNode(t, path, (n) => ({ ...n, expanded: !n.expanded }));
    });
  }

  function toggleSkip(path: string, skip: boolean) {
    const next = new Set(skipFolders);
    if (skip) next.add(path); else next.delete(path);
    onChange([...next]);
  }

  if (!repoPath) return null;

  return (
    <div className="rounded-lg border border-border bg-surface-2 overflow-hidden mb-10 max-w-[760px]">
      <div className="px-6 py-4 border-b border-border text-sm text-text-muted">
        folders to index
        <span className="text-xs text-text-faint ml-2">
          checked = excluded · default-excluded folders can't be force-included
        </span>
      </div>
      <div className="px-4 py-3 max-h-[280px] overflow-auto">
        {loading && <div className="text-xs text-text-faint px-4 py-2">loading…</div>}
        {error && <div className="text-xs text-red-400 px-4 py-2">{error}</div>}
        {tree.map((n) => (
          <FolderRow
            key={n.path} node={n} depth={0} repoPath={repoPath} skipped={skipped}
            onToggleExpand={toggleExpand} onToggleSkip={toggleSkip}
          />
        ))}
      </div>
    </div>
  );
}
