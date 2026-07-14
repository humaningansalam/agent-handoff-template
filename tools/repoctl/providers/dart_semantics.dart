import 'dart:convert';
import 'dart:io';

import 'package:analyzer/dart/analysis/analysis_context_collection.dart';
import 'package:analyzer/dart/analysis/results.dart';
import 'package:analyzer/dart/ast/ast.dart';
import 'package:analyzer/dart/ast/visitor.dart';
import 'package:analyzer/dart/element/element.dart';
import 'package:analyzer/file_system/overlay_file_system.dart';
import 'package:analyzer/file_system/physical_file_system.dart';
import 'package:path/path.dart' as p;
import 'package:yaml/yaml.dart';

Never _fail(Object error) {
  stdout.write(jsonEncode({'ok': false, 'error': error.toString()}));
  exitCode = 1;
  throw StateError(error.toString());
}

String _relativePath(String repoRoot, String sourcePath, Set<String> eligible) {
  final relative = p.posix.normalize(
    p.relative(p.normalize(sourcePath), from: p.normalize(repoRoot)).replaceAll(p.separator, '/'),
  );
  if (relative == '..' || relative.startsWith('../') || p.posix.isAbsolute(relative)) return '';
  return eligible.contains(relative) ? relative : '';
}

String _projectName(String repoRoot) {
  try {
    final document = loadYaml(File(p.join(repoRoot, 'pubspec.yaml')).readAsStringSync());
    if (document is YamlMap) return document['name']?.toString() ?? '';
  } catch (_) {}
  return '';
}

String _directoryUri(String path) => Uri.directory(p.normalize(path)).toString();

String _repairedPackageConfig(Map<String, dynamic> input, String repoRoot) {
  final configPath = input['product_package_config']?.toString() ?? '';
  Map<String, dynamic> config = {'configVersion': 2, 'packages': <dynamic>[]};
  if (configPath.isNotEmpty) {
    try {
      final decoded = jsonDecode(File(configPath).readAsStringSync());
      if (decoded is Map<String, dynamic>) config = decoded;
    } catch (_) {}
  }
  final projectName = _projectName(repoRoot);
  final packages = <Map<String, dynamic>>[];
  var projectPresent = false;
  for (final raw in config['packages'] is List ? config['packages'] as List : const <dynamic>[]) {
    if (raw is! Map) continue;
    final package = Map<String, dynamic>.from(raw.cast<String, dynamic>());
    final name = package['name']?.toString() ?? '';
    if (name.isEmpty) continue;
    projectPresent = projectPresent || name == projectName;
    final rawRoot = package['rootUri']?.toString() ?? '';
    Uri? rootUri;
    try {
      final parsed = Uri.parse(rawRoot);
      rootUri = parsed.isAbsolute
          ? parsed
          : (configPath.isNotEmpty ? File(configPath).parent.uri.resolveUri(parsed) : null);
    } catch (_) {}
    String rootPath = rootUri?.scheme == 'file' ? rootUri!.toFilePath() : '';
    if (name == projectName && projectName.isNotEmpty) {
      rootPath = repoRoot;
    }
    final insideRepo = rootPath == repoRoot || p.isWithin(repoRoot, rootPath);
    if (rootPath.isNotEmpty && insideRepo && Directory(rootPath).existsSync()) {
      package['rootUri'] = _directoryUri(rootPath);
      packages.add(package);
    }
  }
  if (projectName.isNotEmpty && !projectPresent) {
    packages.add({
      'name': projectName,
      'rootUri': _directoryUri(repoRoot),
      'packageUri': 'lib/',
      'languageVersion': Platform.version.split(' ').first.split('.').take(2).join('.'),
    });
  }
  packages.sort((left, right) => left['name'].toString().compareTo(right['name'].toString()));
  return jsonEncode({'configVersion': 2, 'packages': packages});
}

String _kind(Element element) {
  if (element is ClassElement) return 'class';
  if (element is EnumElement) return 'enum';
  if (element is MixinElement) return 'mixin';
  if (element is ExtensionElement) return 'extension';
  if (element is ExtensionTypeElement) return 'extension_type';
  if (element is ConstructorElement) return 'constructor';
  if (element is MethodElement) return 'method';
  if (element is GetterElement) return 'getter';
  if (element is SetterElement) return 'setter';
  if (element is TopLevelFunctionElement) return 'function';
  return '';
}

String _qualifiedName(Element element) {
  final names = <String>[];
  Element? current = element;
  while (current != null && current is! LibraryElement) {
    final name = current.displayName;
    if (name.isNotEmpty) names.add(name);
    current = current.enclosingElement;
  }
  return names.reversed.join('.');
}

Map<String, dynamic> _anchor(String path, dynamic lineInfo, int start, int end) {
  final startLocation = lineInfo.getLocation(start);
  final endLocation = lineInfo.getLocation(end);
  return {
    'path': path,
    'start_line': startLocation.lineNumber,
    'start_col': startLocation.columnNumber - 1,
    'end_line': endLocation.lineNumber,
    'end_col': endLocation.columnNumber - 1,
  };
}

final class _Descriptor {
  _Descriptor(this.element, this.path, this.id, this.kind, this.name, this.qualifiedName, this.anchor);

  final Element element;
  final String path;
  final String id;
  final String kind;
  final String name;
  final String qualifiedName;
  final Map<String, dynamic> anchor;

  Map<String, dynamic> toJson() => {
    'path': path,
    'provider': 'dart_analyzer',
    'provider_symbol_id': id,
    'language': 'dart',
    'kind': kind,
    'name': name,
    'qualified_name': qualifiedName,
    'anchor': anchor,
  };
}

final class _Collector extends RecursiveAstVisitor<void> {
  _Collector(this.repoRoot, this.path, this.lineInfo, this.eligiblePaths);

  final String repoRoot;
  final String path;
  final dynamic lineInfo;
  final Set<String> eligiblePaths;
  final Map<int, _Descriptor> descriptorsByElement = {};
  final Map<String, _Descriptor> symbols = {};
  final Map<String, Map<String, dynamic>> calls = {};
  final List<_Descriptor> callers = [];

  _Descriptor? _descriptor(Element? rawElement) {
    if (rawElement == null) return null;
    final element = rawElement.baseElement;
    final cached = descriptorsByElement[element.id];
    if (cached != null) return cached;
    final kind = _kind(element);
    if (kind.isEmpty) return null;
    final fragment = element.firstFragment;
    final libraryFragment = fragment.libraryFragment;
    if (libraryFragment == null) return null;
    final sourcePath = _relativePath(repoRoot, libraryFragment.source.fullName, eligiblePaths);
    if (sourcePath.isEmpty) return null;
    final name = element.displayName.isEmpty ? 'new' : element.displayName;
    final start = fragment.nameOffset ?? fragment.offset;
    final end = start + (name.isEmpty ? 1 : name.length);
    final anchor = _anchor(sourcePath, libraryFragment.lineInfo, start, end);
    final qualifiedName = _qualifiedName(element);
    final id = [
      'dart_analyzer',
      sourcePath,
      qualifiedName,
      kind,
      anchor['start_line'],
      anchor['start_col'],
      anchor['end_line'],
      anchor['end_col'],
    ].join(':');
    final descriptor = _Descriptor(element, sourcePath, id, kind, name, qualifiedName, anchor);
    descriptorsByElement[element.id] = descriptor;
    symbols[id] = descriptor;
    return descriptor;
  }

  void _withCaller(Element? element, void Function() visitBody) {
    final descriptor = _descriptor(element);
    if (descriptor == null) return;
    callers.add(descriptor);
    try {
      visitBody();
    } finally {
      callers.removeLast();
    }
  }

  void _recordCall(Element? element, AstNode node) {
    if (callers.isEmpty) return;
    final caller = callers.last;
    final callee = _descriptor(element);
    if (callee == null || callee.id == caller.id) return;
    final anchor = _anchor(path, lineInfo, node.offset, node.end);
    final key = '${caller.id}|${callee.id}|${anchor['start_line']}|${anchor['start_col']}';
    calls[key] = {
      'path': path,
      'provider': 'dart_analyzer',
      'caller_provider_symbol_id': caller.id,
      'callee_provider_symbol_id': callee.id,
      'language': 'dart',
      'scope': caller.path == callee.path ? 'same_file' : 'cross_file_import',
      'anchor': anchor,
    };
  }

  void _recordDeclaration(Element? element) {
    _descriptor(element);
  }

  @override
  void visitClassDeclaration(ClassDeclaration node) {
    _recordDeclaration(node.declaredFragment?.element);
    super.visitClassDeclaration(node);
  }

  @override
  void visitEnumDeclaration(EnumDeclaration node) {
    _recordDeclaration(node.declaredFragment?.element);
    super.visitEnumDeclaration(node);
  }

  @override
  void visitMixinDeclaration(MixinDeclaration node) {
    _recordDeclaration(node.declaredFragment?.element);
    super.visitMixinDeclaration(node);
  }

  @override
  void visitExtensionDeclaration(ExtensionDeclaration node) {
    _recordDeclaration(node.declaredFragment?.element);
    super.visitExtensionDeclaration(node);
  }

  @override
  void visitExtensionTypeDeclaration(ExtensionTypeDeclaration node) {
    _recordDeclaration(node.declaredFragment?.element);
    super.visitExtensionTypeDeclaration(node);
  }

  @override
  void visitFunctionDeclaration(FunctionDeclaration node) {
    _withCaller(node.declaredFragment?.element, () => node.functionExpression.body.accept(this));
  }

  @override
  void visitMethodDeclaration(MethodDeclaration node) {
    _withCaller(node.declaredFragment?.element, () => node.body.accept(this));
  }

  @override
  void visitConstructorDeclaration(ConstructorDeclaration node) {
    _withCaller(node.declaredFragment?.element, () {
      for (final initializer in node.initializers) {
        initializer.accept(this);
      }
      node.body.accept(this);
    });
  }

  @override
  void visitFunctionExpression(FunctionExpression node) {
    // Anonymous closures are separate execution scopes. They are not folded
    // into the enclosing callable's CALLS evidence.
  }

  @override
  void visitMethodInvocation(MethodInvocation node) {
    _recordCall(node.methodName.element, node);
    super.visitMethodInvocation(node);
  }

  @override
  void visitFunctionExpressionInvocation(FunctionExpressionInvocation node) {
    _recordCall(node.element, node);
    super.visitFunctionExpressionInvocation(node);
  }

  @override
  void visitInstanceCreationExpression(InstanceCreationExpression node) {
    _recordCall(node.constructorName.element, node);
    super.visitInstanceCreationExpression(node);
  }

  @override
  void visitRedirectingConstructorInvocation(RedirectingConstructorInvocation node) {
    _recordCall(node.element, node);
    super.visitRedirectingConstructorInvocation(node);
  }

  @override
  void visitSuperConstructorInvocation(SuperConstructorInvocation node) {
    _recordCall(node.element, node);
    super.visitSuperConstructorInvocation(node);
  }

  @override
  void visitPropertyAccess(PropertyAccess node) {
    final element = node.propertyName.element;
    if (element is GetterElement) _recordCall(element, node);
    super.visitPropertyAccess(node);
  }

  @override
  void visitPrefixedIdentifier(PrefixedIdentifier node) {
    final element = node.identifier.element;
    if (element is GetterElement) _recordCall(element, node);
    super.visitPrefixedIdentifier(node);
  }
}

Future<void> main() async {
  try {
    final decoded = jsonDecode(await stdin.transform(utf8.decoder).join());
    if (decoded is! Map) _fail('provider input must be a JSON object');
    final input = Map<String, dynamic>.from(decoded.cast<String, dynamic>());
    final repoRoot = p.normalize(input['repo_root']?.toString() ?? '');
    final eligible = {
      for (final value in input['paths'] is List ? input['paths'] as List : const <dynamic>[])
        p.posix.normalize(value.toString().replaceAll('\\', '/')),
    };
    final selected = {
      for (final value in input['analysis_paths'] is List ? input['analysis_paths'] as List : const <dynamic>[])
        p.posix.normalize(value.toString().replaceAll('\\', '/')),
    }.intersection(eligible);

    final provider = OverlayResourceProvider(PhysicalResourceProvider.INSTANCE);
    final packageConfigPath = p.join(repoRoot, '.dart_tool', 'package_config.json');
    provider.setOverlay(
      packageConfigPath,
      content: _repairedPackageConfig(input, repoRoot),
      modificationStamp: 1,
    );
    final selectedFiles = selected.map((relative) => p.normalize(p.join(repoRoot, relative))).toList();
    final collection = AnalysisContextCollection(
      includedPaths: selectedFiles,
      excludedPaths: [
        p.join(repoRoot, '.git'),
        p.join(repoRoot, 'build'),
      ],
      resourceProvider: provider,
      sdkPath: input['sdk_path']?.toString(),
    );
    final analyzedPaths = <String>{};
    final failedPaths = <String>{};
    final symbols = <String, _Descriptor>{};
    final calls = <String, Map<String, dynamic>>{};
    try {
      for (final relative in selected.toList()..sort()) {
        final absolute = p.normalize(p.join(repoRoot, relative));
        try {
          final result = await collection.contextFor(absolute).currentSession.getResolvedUnit(absolute);
          if (result is! ResolvedUnitResult) {
            failedPaths.add(relative);
            continue;
          }
          final collector = _Collector(repoRoot, relative, result.lineInfo, eligible);
          result.unit.accept(collector);
          symbols.addAll(collector.symbols);
          calls.addAll(collector.calls);
          analyzedPaths.add(relative);
        } catch (_) {
          failedPaths.add(relative);
        }
      }
    } finally {
      await collection.dispose();
    }
    final symbolValues = symbols.values.toList()..sort((left, right) => left.id.compareTo(right.id));
    final symbolIds = symbolValues.map((value) => value.id).toSet();
    final callValues = calls.values
        .where((value) => symbolIds.contains(value['caller_provider_symbol_id']) && symbolIds.contains(value['callee_provider_symbol_id']))
        .toList()
      ..sort((left, right) {
        final leftKey = '${left['caller_provider_symbol_id']}|${left['callee_provider_symbol_id']}|${left['anchor']['start_line']}|${left['anchor']['start_col']}';
        final rightKey = '${right['caller_provider_symbol_id']}|${right['callee_provider_symbol_id']}|${right['anchor']['start_line']}|${right['anchor']['start_col']}';
        return leftKey.compareTo(rightKey);
      });
    stdout.write(jsonEncode({
      'ok': true,
      'provider': 'dart_analyzer',
      'analyzed_paths': analyzedPaths.toList()..sort(),
      'failed_paths': failedPaths.toList()..sort(),
      'symbols': symbolValues.map((value) => value.toJson()).toList(),
      'calls': callValues,
    }));
  } catch (error, stack) {
    if (exitCode == 0) {
      stdout.write(jsonEncode({'ok': false, 'error': '$error\n$stack'}));
      exitCode = 1;
    }
  }
}
