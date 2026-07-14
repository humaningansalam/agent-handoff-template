"use strict";

const fs = require("fs");
const path = require("path");

function fail(message) {
  process.stdout.write(JSON.stringify({ ok: false, error: String(message) }));
  process.exitCode = 1;
}

function readInput() {
  return JSON.parse(fs.readFileSync(0, "utf8"));
}

function normalizedAbsolute(value) {
  let resolved = path.resolve(value);
  try {
    resolved = fs.realpathSync.native(resolved);
  } catch (_) {
    // Missing paths are reported by the provider result rather than here.
  }
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

function repoRelative(repoRoot, fileName) {
  const relative = path.relative(repoRoot, fileName).replaceAll(path.sep, "/");
  if (!relative || relative === ".." || relative.startsWith("../") || path.isAbsolute(relative)) {
    return "";
  }
  return relative;
}

function nearestConfig(fileName, repoRoot) {
  let current = path.dirname(fileName);
  while (current === repoRoot || current.startsWith(`${repoRoot}${path.sep}`)) {
    for (const name of ["tsconfig.json", "jsconfig.json"]) {
      const candidate = path.join(current, name);
      if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
        return candidate;
      }
    }
    if (current === repoRoot) {
      break;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      break;
    }
    current = parent;
  }
  return "";
}

function declarationName(ts, node) {
  const name = node && node.name;
  if (name && (ts.isIdentifier(name) || ts.isPrivateIdentifier(name) || ts.isStringLiteral(name) || ts.isNumericLiteral(name))) {
    return name.text;
  }
  if (ts.isConstructorDeclaration(node)) {
    return "constructor";
  }
  if (ts.isFunctionExpression(node) || ts.isArrowFunction(node) || ts.isClassExpression(node)) {
    const parent = node.parent;
    if (parent && ts.isVariableDeclaration(parent) && ts.isIdentifier(parent.name)) {
      return parent.name.text;
    }
    if (parent && (ts.isPropertyDeclaration(parent) || ts.isPropertyAssignment(parent)) && parent.name) {
      return parent.name.getText(parent.getSourceFile()).replace(/^['"]|['"]$/g, "");
    }
  }
  return "";
}

function declarationKind(ts, node) {
  if (ts.isClassDeclaration(node) || ts.isClassExpression(node)) return "class";
  if (ts.isInterfaceDeclaration(node)) return "interface";
  if (ts.isEnumDeclaration(node)) return "enum";
  if (ts.isTypeAliasDeclaration(node)) return "type_alias";
  if (ts.isConstructorDeclaration(node)) return "constructor";
  if (ts.isGetAccessorDeclaration(node)) return "getter";
  if (ts.isSetAccessorDeclaration(node)) return "setter";
  if (ts.isMethodDeclaration(node) || ts.isMethodSignature(node)) return "method";
  if (ts.isArrowFunction(node)) return "arrow_function";
  if (ts.isFunctionExpression(node)) return "function_expression";
  if (ts.isFunctionDeclaration(node)) {
    return node.parent && !ts.isSourceFile(node.parent) ? "function" : "function";
  }
  return "";
}

function isSupportedDeclaration(ts, node) {
  return Boolean(declarationKind(ts, node) && declarationName(ts, node));
}

function isCallableDeclaration(ts, node) {
  return ts.isFunctionDeclaration(node)
    || ts.isMethodDeclaration(node)
    || ts.isMethodSignature(node)
    || ts.isConstructorDeclaration(node)
    || ts.isGetAccessorDeclaration(node)
    || ts.isSetAccessorDeclaration(node)
    || ts.isArrowFunction(node)
    || ts.isFunctionExpression(node)
    || ts.isClassDeclaration(node)
    || ts.isClassExpression(node);
}

function lexicalQualifiedName(ts, node, ownName) {
  const names = [ownName];
  let current = node.parent;
  while (current && !ts.isSourceFile(current)) {
    if (
      ts.isClassDeclaration(current)
      || ts.isClassExpression(current)
      || ts.isInterfaceDeclaration(current)
      || ts.isFunctionDeclaration(current)
      || ts.isMethodDeclaration(current)
      || ts.isFunctionExpression(current)
      || ts.isArrowFunction(current)
      || ts.isModuleDeclaration(current)
    ) {
      const name = declarationName(ts, current);
      if (name) names.push(name);
    }
    current = current.parent;
  }
  return names.reverse().join(".");
}

function sourceAnchor(sourceFile, node, relativePath) {
  const start = node.getStart(sourceFile, false);
  const end = node.getEnd();
  const startPosition = sourceFile.getLineAndCharacterOfPosition(start);
  const endPosition = sourceFile.getLineAndCharacterOfPosition(end);
  return {
    path: relativePath,
    start_line: startPosition.line + 1,
    start_col: startPosition.character,
    end_line: endPosition.line + 1,
    end_col: endPosition.character,
  };
}

function effectiveDeclaration(ts, declaration) {
  if (declaration && ts.isVariableDeclaration(declaration) && declaration.initializer) {
    if (ts.isArrowFunction(declaration.initializer) || ts.isFunctionExpression(declaration.initializer) || ts.isClassExpression(declaration.initializer)) {
      return declaration.initializer;
    }
  }
  if (declaration && ts.isPropertyDeclaration(declaration) && declaration.initializer) {
    if (ts.isArrowFunction(declaration.initializer) || ts.isFunctionExpression(declaration.initializer) || ts.isClassExpression(declaration.initializer)) {
      return declaration.initializer;
    }
  }
  return declaration;
}

function main() {
  const compilerPath = process.argv[2];
  if (!compilerPath) {
    fail("missing TypeScript compiler path");
    return;
  }
  let ts;
  try {
    ts = require(path.resolve(compilerPath));
  } catch (error) {
    fail(`cannot load TypeScript compiler: ${error}`);
    return;
  }

  let input;
  try {
    input = readInput();
  } catch (error) {
    fail(`invalid provider input: ${error}`);
    return;
  }
  const repoRoot = normalizedAbsolute(input.repo_root || ".");
  const requested = new Map();
  for (const rawPath of Array.isArray(input.paths) ? input.paths : []) {
    const relativePath = String(rawPath).replaceAll("\\", "/").replace(/^\.\//, "");
    const absolutePath = normalizedAbsolute(path.join(repoRoot, relativePath));
    if (repoRelative(repoRoot, absolutePath)) {
      requested.set(absolutePath, relativePath);
    }
  }
  const analysisPaths = new Set();
  for (const rawPath of Array.isArray(input.analysis_paths) ? input.analysis_paths : input.paths || []) {
    const relativePath = String(rawPath).replaceAll("\\", "/").replace(/^\.\//, "");
    const absolutePath = normalizedAbsolute(path.join(repoRoot, relativePath));
    if (requested.has(absolutePath)) analysisPaths.add(absolutePath);
  }

  const groups = new Map();
  for (const absolutePath of analysisPaths) {
    const config = nearestConfig(absolutePath, repoRoot);
    const key = config || "<inferred>";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(absolutePath);
  }

  const symbols = new Map();
  const calls = new Map();
  const analyzedPaths = new Set();
  const failedPaths = new Set();
  const diagnostics = [];

  for (const [configPath, rootNames] of groups) {
    let options;
    if (configPath !== "<inferred>") {
      const config = ts.readConfigFile(configPath, ts.sys.readFile);
      if (config.error) {
        diagnostics.push({ path: repoRelative(repoRoot, configPath), message: ts.flattenDiagnosticMessageText(config.error.messageText, "\n") });
      }
      const parsed = ts.parseJsonConfigFileContent(config.config || {}, ts.sys, path.dirname(configPath), undefined, configPath);
      options = { ...parsed.options };
    } else {
      options = {
        target: ts.ScriptTarget.ES2022 || ts.ScriptTarget.ESNext,
        module: ts.ModuleKind.ESNext,
        moduleResolution: ts.ModuleResolutionKind.Bundler || ts.ModuleResolutionKind.NodeJs,
        jsx: ts.JsxEmit.ReactJSX,
      };
    }
    options.allowJs = true;
    options.checkJs = false;
    options.noEmit = true;
    options.skipLibCheck = true;

    let program;
    try {
      program = ts.createProgram({ rootNames, options });
    } catch (error) {
      for (const fileName of rootNames) failedPaths.add(requested.get(fileName));
      diagnostics.push({ path: configPath === "<inferred>" ? "" : repoRelative(repoRoot, configPath), message: String(error) });
      continue;
    }
    const checker = program.getTypeChecker();
    const descriptorByDeclaration = new Map();
    const groupDescriptors = new Map();

    function descriptorFor(rawDeclaration) {
      const declaration = effectiveDeclaration(ts, rawDeclaration);
      if (!declaration || !isSupportedDeclaration(ts, declaration)) return null;
      const sourceFile = declaration.getSourceFile();
      const canonicalFile = normalizedAbsolute(sourceFile.fileName);
      const relativePath = requested.get(canonicalFile);
      if (!relativePath) return null;
      if (descriptorByDeclaration.has(declaration)) return descriptorByDeclaration.get(declaration);
      const name = declarationName(ts, declaration);
      const kind = declarationKind(ts, declaration);
      const anchor = sourceAnchor(sourceFile, declaration, relativePath);
      const providerSymbolId = [
        "typescript_compiler",
        relativePath,
        lexicalQualifiedName(ts, declaration, name),
        kind,
        anchor.start_line,
        anchor.start_col,
        anchor.end_line,
        anchor.end_col,
      ].join(":");
      const descriptor = {
        path: relativePath,
        provider: "typescript_compiler",
        provider_symbol_id: providerSymbolId,
        language: /\.[cm]?tsx?$/.test(relativePath) ? "typescript" : "javascript",
        kind,
        name,
        qualified_name: lexicalQualifiedName(ts, declaration, name),
        anchor,
        declaration,
      };
      descriptorByDeclaration.set(declaration, descriptor);
      groupDescriptors.set(providerSymbolId, descriptor);
      symbols.set(providerSymbolId, descriptor);
      return descriptor;
    }

    function declarationsForSymbol(rawSymbol) {
      let symbol = rawSymbol;
      if (symbol && (symbol.flags & ts.SymbolFlags.Alias)) {
        try {
          symbol = checker.getAliasedSymbol(symbol);
        } catch (_) {
          return [];
        }
      }
      return symbol && Array.isArray(symbol.declarations) ? symbol.declarations : [];
    }

    function targetForCall(node) {
      if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
        const declarations = declarationsForSymbol(checker.getSymbolAtLocation(node.tagName));
        const candidates = declarations.map(descriptorFor).filter(Boolean);
        return candidates.length === 1 ? candidates[0] : null;
      }
      const signature = checker.getResolvedSignature(node);
      if (!signature) return null;
      let declaration = signature.getDeclaration ? signature.getDeclaration() : signature.declaration;
      if (!declaration && signature.declaration) declaration = signature.declaration;
      let descriptor = descriptorFor(declaration);
      if (descriptor) return descriptor;
      if (ts.isNewExpression(node)) {
        const declarations = declarationsForSymbol(checker.getSymbolAtLocation(node.expression));
        const candidates = declarations.map(descriptorFor).filter(Boolean);
        if (candidates.length === 1) descriptor = candidates[0];
      }
      return descriptor;
    }

    function collectCalls(caller) {
      const declaration = caller.declaration;
      const sourceFile = declaration.getSourceFile();
      const body = declaration.body;
      if (!body) return;
      function visit(node) {
        if (node !== body && ts.isFunctionLike(node)) return;
        if (ts.isCallExpression(node) || ts.isNewExpression(node) || ts.isTaggedTemplateExpression(node) || ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
          const callee = targetForCall(node);
          if (callee && callee.provider_symbol_id !== caller.provider_symbol_id) {
            const anchor = sourceAnchor(sourceFile, node, caller.path);
            const key = [caller.provider_symbol_id, callee.provider_symbol_id, anchor.start_line, anchor.start_col].join("|");
            calls.set(key, {
              path: caller.path,
              provider: "typescript_compiler",
              caller_provider_symbol_id: caller.provider_symbol_id,
              callee_provider_symbol_id: callee.provider_symbol_id,
              language: caller.language,
              scope: caller.path === callee.path ? "same_file" : "cross_file_import",
              anchor,
            });
          }
        }
        ts.forEachChild(node, visit);
      }
      visit(body);
    }

    const programFiles = new Map();
    for (const sourceFile of program.getSourceFiles()) {
      const canonical = normalizedAbsolute(sourceFile.fileName);
      if (requested.has(canonical)) programFiles.set(canonical, sourceFile);
    }
    for (const fileName of rootNames) {
      const relativePath = requested.get(fileName);
      const sourceFile = programFiles.get(fileName);
      if (!sourceFile) {
        failedPaths.add(relativePath);
        continue;
      }
      const syntaxErrors = program.getSyntacticDiagnostics(sourceFile).filter((diagnostic) => diagnostic.category === ts.DiagnosticCategory.Error);
      if (syntaxErrors.length) {
        failedPaths.add(relativePath);
        diagnostics.push(...syntaxErrors.map((diagnostic) => ({ path: relativePath, message: ts.flattenDiagnosticMessageText(diagnostic.messageText, "\n") })));
        continue;
      }
      analyzedPaths.add(relativePath);
      function collect(node) {
        if (isSupportedDeclaration(ts, node)) descriptorFor(node);
        ts.forEachChild(node, collect);
      }
      collect(sourceFile);
    }

    for (const descriptor of groupDescriptors.values()) {
      if (analyzedPaths.has(descriptor.path) && isCallableDeclaration(ts, descriptor.declaration)) {
        collectCalls(descriptor);
      }
    }
  }

  for (const absolutePath of analysisPaths) {
    const relativePath = requested.get(absolutePath);
    if (!analyzedPaths.has(relativePath) && !failedPaths.has(relativePath)) failedPaths.add(relativePath);
  }
  const referencedSymbolIds = new Set();
  for (const value of calls.values()) {
    referencedSymbolIds.add(value.caller_provider_symbol_id);
    referencedSymbolIds.add(value.callee_provider_symbol_id);
  }
  const retainedSymbols = [...symbols.values()].filter(
    (value) => analyzedPaths.has(value.path) || referencedSymbolIds.has(value.provider_symbol_id),
  );
  const retainedSymbolIds = new Set(retainedSymbols.map((value) => value.provider_symbol_id));
  const publicSymbols = retainedSymbols.map(({ declaration, ...value }) => value);
  publicSymbols.sort((a, b) => a.provider_symbol_id.localeCompare(b.provider_symbol_id));
  const publicCalls = [...calls.values()].filter(
    (value) => retainedSymbolIds.has(value.caller_provider_symbol_id) && retainedSymbolIds.has(value.callee_provider_symbol_id),
  );
  publicCalls.sort((a, b) => `${a.caller_provider_symbol_id}|${a.callee_provider_symbol_id}|${a.anchor.start_line}|${a.anchor.start_col}`.localeCompare(`${b.caller_provider_symbol_id}|${b.callee_provider_symbol_id}|${b.anchor.start_line}|${b.anchor.start_col}`));
  process.stdout.write(JSON.stringify({
    ok: true,
    provider: "typescript_compiler",
    compiler_version: String(ts.version || ""),
    analyzed_paths: [...analyzedPaths].sort(),
    failed_paths: [...failedPaths].filter(Boolean).sort(),
    symbols: publicSymbols,
    calls: publicCalls,
    diagnostics,
  }));
}

try {
  main();
} catch (error) {
  fail(error && error.stack ? error.stack : error);
}
