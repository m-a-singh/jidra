#!/usr/bin/env node
/**
 * JIDRA TypeScript sidecar.
 * Runs inside an ephemeral node:20-slim container with the target repo mounted.
 * Emits one JSONL record per line to stdout, schema matching JIDRA's Graph model.
 *
 * Usage: node index.js <repo_root>
 */

const { Project, SyntaxKind, ts } = require("ts-morph");
const path = require("path");
const crypto = require("crypto");
const fs = require("fs");

const repoRoot = process.argv[2];
if (!repoRoot) {
  process.stderr.write("Usage: node index.js <repo_root> [file1,file2,...]\n");
  process.exit(1);
}

// Optional: comma-separated repo-relative paths to restrict extraction to.
// The full Project is still loaded for cross-file type resolution.
const fileFilter = process.argv[3]
  ? new Set(process.argv[3].split(",").map(f => path.resolve(repoRoot, f)))
  : null;

// ── ID helpers (mirror models.py stable_id) ──────────────────────────────────

function stableId(value) {
  return crypto.createHash("sha1").update(value).digest("hex").slice(0, 16);
}

function classId(fullName, filePath) {
  return stableId(`class::${fullName}::${filePath}`);
}

function methodId(signature, filePath, startLine) {
  return stableId(`method::${signature}::${filePath}::${startLine}`);
}

function fieldId(classFullName, fieldName, filePath, line) {
  return stableId(`field::${classFullName}#${fieldName}::${filePath}::${line}`);
}

function callsiteId(callerMethodId, line, column, calleeName) {
  return stableId(`call::${callerMethodId}::${line}:${column}::${calleeName}`);
}

function inheritanceEdgeId(sourceClass, targetClass, relation) {
  return stableId(`inheritance::${sourceClass}::${relation}::${targetClass}`);
}

function resolvedCallEdgeId(csId, calleeMethodId) {
  return stableId(`resolved_call::${csId}::${calleeMethodId}`);
}

function methodSignature(classFullName, methodName, paramTypes) {
  return `${classFullName}#${methodName}(${paramTypes.join(", ")})`;
}

// ── Stereotype detection ──────────────────────────────────────────────────────

const NESTJS_STEREOTYPES = {
  Controller: "controller",
  Get: "endpoint",
  Post: "endpoint",
  Put: "endpoint",
  Delete: "endpoint",
  Patch: "endpoint",
  Injectable: "service",
  Module: "module",
  Guard: "guard",
  Interceptor: "interceptor",
  Pipe: "pipe",
  // Angular class decorators (Phase 4)
  Component: "angular_component",
  NgModule: "angular_module",
  Directive: "angular_directive",
};

// React hook naming convention: a function whose name is use<Capital>...
const REACT_HOOK_RE = /^use[A-Z]/;

const PATH_STEREOTYPES = [
  // NestJS / TypeScript conventions
  [/\.controller\.(ts|tsx)$/, "controller"],
  [/\.service\.(ts|tsx)$/, "service"],
  [/\.repository\.(ts|tsx)$/, "repository"],
  [/\.resolver\.(ts|tsx)$/, "resolver"],
  [/\.guard\.(ts|tsx)$/, "guard"],
  [/\.middleware\.(ts|tsx)$/, "middleware"],
  [/\.module\.(ts|tsx)$/, "module"],
  [/\.hook\.(ts|tsx)$/, "hook"],
  [/hooks\/use[A-Z]/, "hook"],
  [/\.component\.(ts|tsx)$/, "component"],
  [/components\//, "component"],
  [/pages\//, "page"],
  [/context\//, "context"],
  // JavaScript / frontend conventions
  [/\.(jsx)$/, "component"],
  [/routes?\//i, "route"],
  [/controllers?\//i, "controller"],
  [/middleware\//i, "middleware"],
  [/models?\//i, "model"],
  [/services?\//i, "service"],
  [/store\//i, "store"],
  [/reducers?\//i, "reducer"],
  [/actions?\//i, "action"],
  [/api\//i, "endpoint"],
];

function getStereotypes(decoratorNames, filePath) {
  const result = new Set();
  for (const d of decoratorNames) {
    if (NESTJS_STEREOTYPES[d]) result.add(NESTJS_STEREOTYPES[d]);
  }
  for (const [pattern, label] of PATH_STEREOTYPES) {
    if (pattern.test(filePath)) result.add(label);
  }
  return [...result];
}

// ── Type helpers ─────────────────────────────────────────────────────────────

function isExternalType(typeText) {
  // Heuristic: if ts-morph resolves the declaration to node_modules, it's external
  return typeText && typeText.includes("node_modules");
}

function safeTypeName(node) {
  try {
    const t = node.getType();
    const text = t.getText();
    // Avoid huge union/intersection noise
    if (text.length > 80) return "unknown";
    return text;
  } catch {
    return "unknown";
  }
}

function getDecoratorNames(node) {
  try {
    return node.getDecorators().map((d) => d.getName());
  } catch {
    return [];
  }
}

function hasJsxReturn(node) {
  try {
    return (
      node.getDescendantsOfKind(SyntaxKind.JsxElement).length > 0 ||
      node.getDescendantsOfKind(SyntaxKind.JsxSelfClosingElement).length > 0 ||
      node.getDescendantsOfKind(SyntaxKind.JsxFragment).length > 0
    );
  } catch {
    return false;
  }
}

// Map a function to a semantic framework role (Phase 4). React/Angular focused;
// the equivalent rules live in the in-process tree-sitter backend (Phase 7).
function detectFrameworkRole(methodName, node, decoratorNames, isEndpoint) {
  if (isEndpoint) return "http_handler";
  if (decoratorNames.includes("Component")) return "component";
  if (REACT_HOOK_RE.test(methodName)) return "hook";
  if (hasJsxReturn(node)) return "component";
  return null;
}

// ── Namespace from file path (replaces Java package) ─────────────────────────

function filePathToNamespace(filePath, root) {
  const rel = path.relative(root, filePath);
  // src/components/UserCard.tsx → src.components
  const parts = rel.replace(/\.(ts|tsx)$/, "").split(path.sep);
  parts.pop(); // drop filename
  return parts.join(".") || "<root>";
}

function filePathToClassName(filePath) {
  return path.basename(filePath).replace(/\.(ts|tsx)$/, "");
}

// ── Collect declaration source safely ────────────────────────────────────────

function nodeSource(node) {
  try {
    return node.getText().slice(0, 4000);
  } catch {
    return "";
  }
}

function startLine(node) {
  try {
    return node.getStartLineNumber();
  } catch {
    return 0;
  }
}

function endLine(node) {
  try {
    return node.getEndLineNumber();
  } catch {
    return 0;
  }
}

// ── Main extraction ───────────────────────────────────────────────────────────

function emit(record) {
  process.stdout.write(JSON.stringify(record) + "\n");
}

// Maps callee method full signatures → method id for call resolution
const methodRegistry = new Map(); // signature → method_id

// Collect everything first, emit after so method registry is populated
const allRecords = [];

function extractFile(sourceFile, root) {
  const filePath = sourceFile.getFilePath();
  const relPath = path.relative(root, filePath);
  const ns = filePathToNamespace(filePath, root);
  const imports = sourceFile.getImportDeclarations().map((i) => i.getModuleSpecifierValue());

  // ── Classes ────────────────────────────────────────────────────────────────
  for (const cls of sourceFile.getClasses()) {
    const clsName = cls.getName() || filePathToClassName(filePath);
    const fullName = `${ns}.${clsName}`;
    const decoratorNames = getDecoratorNames(cls);
    const stereotypes = getStereotypes(decoratorNames, relPath);
    const extendsExpr = cls.getExtends();
    const implExprs = cls.getImplements();

    const cId = classId(fullName, relPath);

    allRecords.push({
      _type: "class",
      id: cId,
      package_name: ns,
      name: clsName,
      full_name: fullName,
      file_path: relPath,
      start_line: startLine(cls),
      end_line: endLine(cls),
      modifiers: cls.getModifiers().map((m) => m.getText()),
      annotations: decoratorNames,
      extends: extendsExpr ? extendsExpr.getExpression().getText() : null,
      implements: implExprs.map((i) => i.getExpression().getText()),
      imports,
      stereotypes,
      language: "typescript",
    });

    // Inheritance edges
    if (extendsExpr) {
      const target = extendsExpr.getExpression().getText();
      const edgeId = inheritanceEdgeId(fullName, target, "extends");
      allRecords.push({
        _type: "inheritance_edge",
        id: edgeId,
        source_class_id: cId,
        source_class: fullName,
        target_class: target,
        relation: "extends",
      });
    }
    for (const impl of implExprs) {
      const target = impl.getExpression().getText();
      const edgeId = inheritanceEdgeId(fullName, target, "implements");
      allRecords.push({
        _type: "inheritance_edge",
        id: edgeId,
        source_class_id: cId,
        source_class: fullName,
        target_class: target,
        relation: "implements",
      });
    }

    // Fields
    for (const prop of cls.getProperties()) {
      const fName = prop.getName();
      const fType = safeTypeName(prop);
      const fId = fieldId(fullName, fName, relPath, startLine(prop));
      allRecords.push({
        _type: "field",
        id: fId,
        class_id: cId,
        name: fName,
        type_name: fType,
        modifiers: prop.getModifiers().map((m) => m.getText()),
        file_path: relPath,
        line: startLine(prop),
      });
    }

    // Methods
    for (const method of cls.getMethods()) {
      extractMethod(method, fullName, cId, relPath, imports, decoratorNames, allRecords);
    }

    // Constructor
    for (const ctor of cls.getConstructors()) {
      extractMethod(ctor, fullName, cId, relPath, imports, decoratorNames, allRecords, "__init__");
    }
  }

  // ── Top-level functions (React components, hooks, utilities) ──────────────
  const fileClass = filePathToClassName(filePath);
  const fileFullName = `${ns}.${fileClass}`;
  const fileCId = classId(fileFullName, relPath);
  let hasTopLevel = false;

  for (const fn of sourceFile.getFunctions()) {
    if (!hasTopLevel) {
      // Emit a synthetic "file module" class to hang these methods on
      allRecords.push({
        _type: "class",
        id: fileCId,
        package_name: ns,
        name: fileClass,
        full_name: fileFullName,
        file_path: relPath,
        start_line: 1,
        end_line: sourceFile.getEndLineNumber(),
        modifiers: [],
        annotations: [],
        extends: null,
        implements: [],
        imports,
        stereotypes: getStereotypes([], relPath),
      });
      hasTopLevel = true;
    }
    extractMethod(fn, fileFullName, fileCId, relPath, imports, [], allRecords);
  }

  // Arrow functions assigned to const (common React pattern)
  for (const varDecl of sourceFile.getVariableDeclarations()) {
    const initializer = varDecl.getInitializer();
    if (!initializer) continue;
    const kind = initializer.getKind();
    if (
      kind !== SyntaxKind.ArrowFunction &&
      kind !== SyntaxKind.FunctionExpression
    )
      continue;

    if (!hasTopLevel) {
      allRecords.push({
        _type: "class",
        id: fileCId,
        package_name: ns,
        name: fileClass,
        full_name: fileFullName,
        file_path: relPath,
        start_line: 1,
        end_line: sourceFile.getEndLineNumber(),
        modifiers: [],
        annotations: [],
        extends: null,
        implements: [],
        imports,
        stereotypes: getStereotypes([], relPath),
      });
      hasTopLevel = true;
    }

    extractMethod(
      initializer,
      fileFullName,
      fileCId,
      relPath,
      imports,
      [],
      allRecords,
      varDecl.getName()
    );
  }
}

function extractMethod(node, classFullName, cId, relPath, imports, classDecorators, records, nameOverride) {
  let methodName;
  try {
    methodName = nameOverride || node.getName() || "<anonymous>";
  } catch {
    methodName = nameOverride || "<anonymous>";
  }

  const paramTypes = [];
  const paramNames = [];
  try {
    for (const p of node.getParameters()) {
      paramNames.push(p.getName());
      paramTypes.push(safeTypeName(p));
    }
  } catch {}

  let returnType = "unknown";
  try {
    returnType = node.getReturnType().getText();
    if (returnType.length > 80) returnType = "unknown";
  } catch {}

  const sig = methodSignature(classFullName, methodName, paramTypes);
  const sl = startLine(node);
  const mId = methodId(sig, relPath, sl);

  methodRegistry.set(sig, mId);

  const decoratorNames = getDecoratorNames(node);
  const allDecorators = [...classDecorators, ...decoratorNames];

  // Endpoint detection (NestJS)
  const HTTP_DECORATORS = new Set(["Get", "Post", "Put", "Delete", "Patch", "Options", "Head", "All"]);
  const httpDec = decoratorNames.find((d) => HTTP_DECORATORS.has(d));
  let isEndpoint = !!httpDec;
  let httpMethod = httpDec ? httpDec.toUpperCase() : null;
  let route = null;
  try {
    if (httpDec) {
      const dec = node.getDecorators().find((d) => d.getName() === httpDec);
      const args = dec.getArguments();
      route = args.length ? args[0].getText().replace(/['"]/g, "") : "/";
    }
  } catch {}

  const classContext = {
    stereotypes: getStereotypes(allDecorators, relPath),
    annotations: allDecorators,
  };

  const frameworkRole = detectFrameworkRole(
    methodName,
    node,
    decoratorNames,
    isEndpoint
  );

  records.push({
    _type: "method",
    id: mId,
    class_id: cId,
    class_full_name: classFullName,
    method_name: methodName,
    return_type: returnType,
    parameter_types: paramTypes,
    parameter_names: paramNames,
    signature: sig,
    file_path: relPath,
    start_line: sl,
    end_line: endLine(node),
    source: nodeSource(node),
    class_context: classContext,
    annotations: decoratorNames,
    local_variable_types: {},
    field_reads: [],
    field_writes: [],
    is_endpoint: isEndpoint,
    http_method: httpMethod,
    route,
    controller_route: null,
    full_route: null,
    language: "typescript",
    framework_role: frameworkRole,
  });

  // Call sites
  extractCallSites(node, mId, relPath, records);
}

function extractCallSites(node, callerMethodId, relPath, records) {
  let calls;
  try {
    calls = node.getDescendantsOfKind(SyntaxKind.CallExpression);
  } catch {
    return;
  }

  for (const call of calls) {
    try {
      const expr = call.getExpression();
      let calleeName = "unknown";
      let receiver = null;

      if (expr.getKind() === SyntaxKind.PropertyAccessExpression) {
        calleeName = expr.getName();
        receiver = expr.getExpression().getText();
        if (receiver.length > 60) receiver = null;
      } else {
        calleeName = expr.getText();
        if (calleeName.length > 60) calleeName = "unknown";
      }

      const pos = call.getStart();
      const sf = call.getSourceFile();
      const lc = sf.getLineAndColumnAtPos(pos);
      const line = lc.line;
      const column = lc.column;

      const csId = callsiteId(callerMethodId, line, column, calleeName);

      // Attempt compiler-resolved type of receiver
      let receiverType = null;
      let resolutionStatus = "unresolved";
      let resolvedCandidates = [];

      if (receiver) {
        try {
          const receiverNode = expr.getExpression();
          const t = receiverNode.getType();
          const typeText = t.getText();
          if (!isExternalType(typeText) && typeText.length < 80) {
            receiverType = typeText;
          }
        } catch {}
      }

      // Try to resolve callee via symbol
      let calleeMethodId = null;
      try {
        const sym = expr.getSymbol();
        if (sym) {
          const decls = sym.getDeclarations();
          for (const decl of decls) {
            const declFile = decl.getSourceFile().getFilePath();
            if (declFile.includes("node_modules")) continue; // Option A: skip external
            // Build a rough signature to look up in registry
            const declName = sym.getName();
            const parent = decl.getParent();
            let parentName = "";
            try {
              parentName = parent.getName ? parent.getName() : "";
            } catch {}
            const candidateSig = parentName
              ? `${parentName}#${declName}`
              : declName;
            resolvedCandidates.push(candidateSig);
          }
          if (resolvedCandidates.length > 0) {
            resolutionStatus = "resolved";
          }
        }
      } catch {}

      records.push({
        _type: "callsite",
        id: csId,
        caller_method_id: callerMethodId,
        callee_name: calleeName,
        receiver,
        argument_count: call.getArguments().length,
        file_path: relPath,
        line,
        column,
        text: call.getText().slice(0, 120),
        receiver_type_raw: receiverType,
        receiver_type_normalized: receiverType,
        receiver_resolution_source: receiverType ? "compiler" : null,
        receiver_type: receiverType,
        resolved_candidates: resolvedCandidates,
        resolution_status: resolutionStatus,
        resolution_reason: resolutionStatus === "resolved" ? "compiler_symbol" : "no_symbol",
        candidate_count: resolvedCandidates.length,
      });

      if (calleeMethodId) {
        const reId = resolvedCallEdgeId(csId, calleeMethodId);
        records.push({
          _type: "resolved_call_edge",
          id: reId,
          callsite_id: csId,
          caller_method_id: callerMethodId,
          callee_method_id: calleeMethodId,
        });
      }
    } catch {
      // skip malformed call expression
    }
  }

  // JSX component usage — <MyComponent /> is not a CallExpression but is a real call edge
  let jsxElements;
  try {
    jsxElements = [
      ...node.getDescendantsOfKind(SyntaxKind.JsxOpeningElement),
      ...node.getDescendantsOfKind(SyntaxKind.JsxSelfClosingElement),
    ];
  } catch {
    jsxElements = [];
  }

  for (const el of jsxElements) {
    try {
      const tagNode = el.getTagNameNode();
      const calleeName = tagNode.getText();
      // Only track user-defined components (PascalCase) — skip HTML elements (div, span, etc.)
      if (!calleeName || !/^[A-Z]/.test(calleeName)) continue;

      const pos = el.getStart();
      const sf = el.getSourceFile();
      const lc = sf.getLineAndColumnAtPos(pos);
      const csId = callsiteId(callerMethodId, lc.line, lc.column, calleeName);

      let resolvedCandidates = [];
      let resolutionStatus = "unresolved";
      try {
        const sym = tagNode.getSymbol();
        if (sym) {
          const decls = sym.getDeclarations();
          for (const decl of decls) {
            if (decl.getSourceFile().getFilePath().includes("node_modules")) continue;
            const declName = sym.getName();
            const parent = decl.getParent();
            let parentName = "";
            try { parentName = parent.getName ? parent.getName() : ""; } catch {}
            resolvedCandidates.push(parentName ? `${parentName}#${declName}` : declName);
          }
          if (resolvedCandidates.length > 0) resolutionStatus = "resolved";
        }
      } catch {}

      records.push({
        _type: "callsite",
        id: csId,
        caller_method_id: callerMethodId,
        callee_name: calleeName,
        receiver: null,
        argument_count: 0,
        file_path: relPath,
        line: lc.line,
        column: lc.column,
        text: el.getText().slice(0, 120),
        receiver_type_raw: null,
        receiver_type_normalized: null,
        receiver_resolution_source: null,
        receiver_type: null,
        resolved_candidates: resolvedCandidates,
        resolution_status: resolutionStatus,
        resolution_reason: resolutionStatus === "resolved" ? "compiler_symbol" : "no_symbol",
        candidate_count: resolvedCandidates.length,
      });
    } catch {
      // skip malformed JSX element
    }
  }
}

// ── Resolve call edges after all files processed ──────────────────────────────

function resolveCallEdges(records) {
  const callsites = records.filter((r) => r._type === "callsite");
  const extra = [];

  for (const cs of callsites) {
    if (cs.resolution_status !== "resolved") continue;
    for (const candidate of cs.resolved_candidates) {
      // Look up by partial signature match
      for (const [sig, mId] of methodRegistry.entries()) {
        if (sig.includes(candidate) || sig.endsWith(`#${cs.callee_name}(`)) {
          const reId = resolvedCallEdgeId(cs.id, mId);
          extra.push({
            _type: "resolved_call_edge",
            id: reId,
            callsite_id: cs.id,
            caller_method_id: cs.caller_method_id,
            callee_method_id: mId,
          });
          break;
        }
      }
    }
  }

  return extra;
}

// ── JS/Frontend source root detection ────────────────────────────────────────

function readPackageJson(root) {
  try {
    return JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
  } catch { return {}; }
}

function detectFramework(pkg) {
  const deps = { ...pkg.dependencies, ...pkg.devDependencies };
  if (deps["next"]) return "nextjs";
  if (deps["@angular/core"]) return "angular";
  if (deps["vue"]) return "vue";
  if (deps["react"]) return "react";
  if (deps["express"] || deps["fastify"] || deps["koa"] || deps["hapi"]) return "node";
  return "unknown";
}

const FRAMEWORK_SOURCE_ROOTS = {
  nextjs:  ["src", "app", "pages", "components", "lib", "hooks", "utils"],
  react:   ["src", "components", "lib", "hooks", "utils", "pages"],
  angular: ["src"],
  vue:     ["src"],
  node:    ["src", "lib", "routes", "controllers", "middleware", "api", "server"],
  unknown: ["src", "lib", "app"],
};

function detectSourceRoots(root, framework) {
  const candidates = FRAMEWORK_SOURCE_ROOTS[framework] || FRAMEWORK_SOURCE_ROOTS.unknown;
  const roots = candidates
    .map(c => path.join(root, c))
    .filter(p => { try { return fs.statSync(p).isDirectory(); } catch { return false; } });

  // Fallback: if no known source roots exist, check if root itself has source files
  // Only accept root-level indexing for small repos (< 200 JS/TS files at root)
  if (roots.length === 0) {
    const rootFiles = fs.readdirSync(root)
      .filter(f => /\.(js|ts|jsx|tsx|mjs)$/.test(f)).length;
    if (rootFiles < 200) roots.push(root);
  }

  return roots;
}

const SKIP_FILE_PATTERNS = [
  /\.min\.(js|ts)$/,
  /\.bundle\.(js|ts)$/,
  /\.(generated|gen)\.(js|ts|jsx|tsx)$/,
  /\.d\.ts$/,
  /__generated__/,
  /\.stories\.(js|ts|jsx|tsx)$/,
];

function isSourceFile(filePath) {
  if (SKIP_FILE_PATTERNS.some(p => p.test(filePath))) return false;
  try {
    const content = fs.readFileSync(filePath, "utf8");
    const lines = content.split("\n");
    // Minified bundles have very long lines; source files rarely exceed 500 chars/line on average
    if (lines.length > 0) {
      const avgLineLen = content.length / lines.length;
      if (avgLineLen > 500) return false;
    }
    // Hard cap at 10 000 lines — true source files virtually never reach this
    if (lines.length > 10000) return false;
  } catch { return false; }
  return true;
}

// ── Entry point ───────────────────────────────────────────────────────────────

function findTsConfig(root) {
  const candidates = [
    path.join(root, "tsconfig.json"),
    path.join(root, "tsconfig.app.json"),
    path.join(root, "tsconfig.base.json"),
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return undefined;
}

function main() {
  const tsConfigPath = findTsConfig(repoRoot);

  const project = new Project({
    tsConfigFilePath: tsConfigPath,
    addFilesFromTsConfig: !!tsConfigPath,
    skipAddingFilesFromTsConfig: !tsConfigPath,
    compilerOptions: {
      allowJs: true,
      jsx: ts.JsxEmit.ReactJSX,
      skipLibCheck: true,
      noEmit: true,
    },
    skipFileDependencyResolution: false,
  });

  if (!tsConfigPath) {
    // No tsconfig — detect framework and index only known source roots
    const pkg = readPackageJson(repoRoot);
    const framework = detectFramework(pkg);
    const sourceRoots = detectSourceRoots(repoRoot, framework);

    process.stderr.write(`[jidra-ts] Framework: ${framework}, source roots: ${sourceRoots.map(r => path.relative(repoRoot, r) || ".").join(", ")}\n`);

    const EXCLUDE = new Set([
      "node_modules", "dist", ".next", "out", "build", "coverage",
      ".git", ".turbo", "vendor", "public", "__generated__", ".cache",
      "storybook-static", ".vercel", ".output", ".nuxt", ".svelte-kit",
    ]);

    function addDir(dir) {
      let entries;
      try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
      for (const e of entries) {
        if (EXCLUDE.has(e.name)) continue;
        const full = path.join(dir, e.name);
        if (e.isDirectory()) addDir(full);
        else if (/\.(ts|tsx|js|jsx|mjs)$/.test(e.name) && isSourceFile(full)) {
          project.addSourceFileAtPath(full);
        }
      }
    }

    for (const root of sourceRoots) addDir(root);
  }

  const sourceFiles = project.getSourceFiles().filter((sf) => {
    const fp = sf.getFilePath();
    if (fp.includes("node_modules") || fp.endsWith(".d.ts")) return false;
    if (fileFilter && !fileFilter.has(fp)) return false;
    return true;
  });

  process.stderr.write(
    `[jidra-ts] Indexing ${sourceFiles.length} files from ${repoRoot}${fileFilter ? " (incremental)" : ""}\n`
  );

  for (const sf of sourceFiles) {
    try {
      extractFile(sf, repoRoot);
    } catch (err) {
      process.stderr.write(`[jidra-ts] Warning: skipped ${sf.getFilePath()}: ${err.message}\n`);
    }
  }

  const extra = resolveCallEdges(allRecords);
  for (const r of [...allRecords, ...extra]) {
    emit(r);
  }

  process.stderr.write(`[jidra-ts] Done. Emitted ${allRecords.length + extra.length} records.\n`);
}

main();
