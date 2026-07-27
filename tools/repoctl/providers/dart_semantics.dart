import 'dart:convert';
import 'dart:io';

import 'package:analyzer/dart/analysis/analysis_context_collection.dart';
import 'package:analyzer/dart/analysis/results.dart';
import 'package:analyzer/dart/ast/ast.dart';
import 'package:analyzer/dart/ast/visitor.dart';
import 'package:analyzer/dart/element/element.dart';
import 'package:analyzer/dart/element/type.dart';
import 'package:analyzer/file_system/physical_file_system.dart';
import 'package:path/path.dart' as p;

Never _fail(Object error) {
  stdout.write(jsonEncode({'ok': false, 'error': error.toString()}));
  exitCode = 1;
  throw StateError(error.toString());
}

String _relativePath(String repoRoot, String sourcePath, Set<String> eligible) {
  final relative = p.posix.normalize(
    p
        .relative(p.normalize(sourcePath), from: p.normalize(repoRoot))
        .replaceAll(p.separator, '/'),
  );
  if (relative == '..' ||
      relative.startsWith('../') ||
      p.posix.isAbsolute(relative))
    return '';
  return eligible.contains(relative) ? relative : '';
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

Map<String, dynamic> _anchor(
  String path,
  dynamic lineInfo,
  int start,
  int end,
) {
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
  _Descriptor(
    this.element,
    this.path,
    this.id,
    this.kind,
    this.name,
    this.qualifiedName,
    this.anchor,
  );

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

final class _RpcArgumentBindings {
  _RpcArgumentBindings(
    this.actualByFormalId,
    this.countByFormalId,
    this.contract,
  );

  final Map<int, Expression> actualByFormalId;
  final Map<int, int> countByFormalId;
  final Map<String, dynamic> contract;
}

enum _RpcReceiverOwnership { supabaseClient, other, unknown }

final class _RpcCollector extends RecursiveAstVisitor<void> {
  _RpcCollector(this.path, this.lineInfo);

  static const _supabasePackage = 'supabase';

  final String path;
  final dynamic lineInfo;
  final List<Map<String, dynamic>> facts = [];
  bool enumerationComplete = true;

  bool _isSupabaseLibrary(Uri library) {
    return library.scheme == 'package' &&
        library.pathSegments.isNotEmpty &&
        library.pathSegments.first == _supabasePackage;
  }

  bool _isSupabaseClientElement(Element element) {
    if (element is! ClassElement || element.displayName != 'SupabaseClient') {
      return false;
    }
    final library = element.library?.uri;
    return library != null && _isSupabaseLibrary(library);
  }

  _RpcReceiverOwnership _receiverOwnershipForType(DartType? receiverType) {
    if (receiverType is! InterfaceType) {
      return _RpcReceiverOwnership.unknown;
    }
    final interfaces = [receiverType, ...receiverType.allSupertypes];
    return interfaces.any((type) => _isSupabaseClientElement(type.element3))
        ? _RpcReceiverOwnership.supabaseClient
        : _RpcReceiverOwnership.other;
  }

  _RpcReceiverOwnership _receiverOwnership(MethodInvocation node) {
    return _receiverOwnershipForType(node.realTarget?.staticType);
  }

  Expression _unparenthesized(Expression expression) {
    var current = expression;
    while (current is ParenthesizedExpression) {
      current = current.expression;
    }
    return current;
  }

  Element? _tearOffElement(Expression expression) {
    final function = _unparenthesized(expression);
    if (function is PrefixedIdentifier) return function.identifier.element;
    if (function is PropertyAccess) return function.propertyName.element;
    if (function is SimpleIdentifier) return function.element;
    return null;
  }

  DartType? _tearOffReceiverType(Expression expression) {
    final function = _unparenthesized(expression);
    if (function is PrefixedIdentifier) return function.prefix.staticType;
    if (function is PropertyAccess) return function.realTarget.staticType;
    return null;
  }

  MethodElement? _supabaseRpcElement(Element? rawElement) {
    final element = rawElement?.baseElement;
    if (element is! MethodElement || element.displayName != 'rpc') return null;
    final library = element.library?.uri;
    if (library == null || !_isSupabaseLibrary(library)) return null;
    final owner = element.enclosingElement;
    if (owner is! ClassElement || owner.displayName != 'SupabaseClient') {
      return null;
    }
    return element;
  }

  bool _isDirectInvocationFunction(AstNode node) {
    AstNode current = node;
    while (current.parent is ParenthesizedExpression) {
      current = current.parent!;
    }
    final parent = current.parent;
    return parent is FunctionExpressionInvocation &&
        identical(parent.function, current);
  }

  _RpcArgumentBindings _bindArguments(
    InvocationExpression node,
    MethodElement element,
  ) {
    final actualByFormalId = <int, Expression>{};
    final bindingCounts = <int, int>{};
    final bindingNames = <int, String>{};
    var unmatchedArgumentCount = 0;
    for (final argument in node.argumentList.arguments) {
      final parameter = argument.correspondingParameter?.baseElement;
      if (parameter == null) {
        unmatchedArgumentCount += 1;
        continue;
      }
      actualByFormalId.putIfAbsent(
        parameter.id,
        () => argument is NamedExpression ? argument.expression : argument,
      );
      bindingCounts.update(
        parameter.id,
        (count) => count + 1,
        ifAbsent: () => 1,
      );
      bindingNames[parameter.id] = parameter.displayName;
    }
    final missingRequiredParameterNames =
        element.formalParameters
            .where(
              (parameter) =>
                  parameter.isRequired &&
                  !bindingCounts.containsKey(parameter.baseElement.id),
            )
            .map((parameter) => parameter.displayName)
            .toSet()
            .toList()
          ..sort();
    final duplicateParameterNames =
        bindingCounts.entries
            .where((entry) => entry.value > 1)
            .map((entry) => bindingNames[entry.key] ?? '')
            .toSet()
            .toList()
          ..sort();
    final defectKinds = [
      unmatchedArgumentCount > 0,
      missingRequiredParameterNames.isNotEmpty,
      duplicateParameterNames.isNotEmpty,
    ].where((present) => present).length;
    if (defectKinds == 0) {
      return _RpcArgumentBindings(actualByFormalId, bindingCounts, {
        'status': 'valid',
        'unmatched_argument_count': 0,
        'missing_required_parameter_names': <String>[],
        'duplicate_parameter_names': <String>[],
      });
    }
    final reasonCode = defectKinds > 1
        ? 'argument_contract_mismatch'
        : unmatchedArgumentCount > 0
        ? 'unexpected_argument'
        : missingRequiredParameterNames.isNotEmpty
        ? 'missing_required_argument'
        : 'duplicate_argument';
    return _RpcArgumentBindings(actualByFormalId, bindingCounts, {
      'status': 'invalid',
      'reason_code': reasonCode,
      'unmatched_argument_count': unmatchedArgumentCount,
      'missing_required_parameter_names': missingRequiredParameterNames,
      'duplicate_parameter_names': duplicateParameterNames,
    });
  }

  Map<String, dynamic> _routineEvidence(
    MethodElement element,
    _RpcArgumentBindings bindings,
  ) {
    FormalParameterElement? routineFormal;
    for (final parameter in element.formalParameters) {
      if (parameter.isRequiredPositional) {
        routineFormal = parameter.baseElement;
        break;
      }
    }
    if (routineFormal == null) {
      return {'status': 'unknown', 'reason_code': 'routine_formal_unavailable'};
    }
    final expression = bindings.actualByFormalId[routineFormal.id];
    if (expression == null) {
      return {'status': 'unknown', 'reason_code': 'routine_argument_missing'};
    }
    if (expression is StringLiteral) {
      final value = expression.stringValue;
      if (value != null) return {'status': 'known', 'value': value};
    }
    return {'status': 'unknown', 'reason_code': 'routine_not_static_string'};
  }

  Map<String, dynamic> _paramsEvidence(
    MethodElement element,
    _RpcArgumentBindings bindings,
  ) {
    FormalParameterElement? paramsFormal;
    for (final parameter in element.formalParameters) {
      if (parameter.isNamed && parameter.displayName == 'params') {
        paramsFormal = parameter.baseElement;
        break;
      }
    }
    if (paramsFormal == null) {
      return {
        'status': 'unknown',
        'known_names': <String>[],
        'reason_code': 'params_formal_unavailable',
      };
    }
    final occurrences = bindings.countByFormalId[paramsFormal.id] ?? 0;
    if (occurrences == 0) {
      return {'status': 'complete', 'known_names': <String>[]};
    }
    final expression = bindings.actualByFormalId[paramsFormal.id];
    if (occurrences != 1 || expression == null) {
      return {
        'status': 'unknown',
        'known_names': <String>[],
        'reason_code': 'params_argument_ambiguous',
      };
    }
    if (expression is! SetOrMapLiteral || !expression.isMap) {
      return {
        'status': 'unknown',
        'known_names': <String>[],
        'reason_code': 'params_not_map_literal',
      };
    }
    final names = <String>{};
    var complete = true;
    for (final element in expression.elements) {
      if (element is! MapLiteralEntry) {
        complete = false;
        continue;
      }
      final key = element.key;
      final value = key is StringLiteral ? key.stringValue : null;
      if (value == null) {
        complete = false;
      } else {
        names.add(value);
      }
    }
    final knownNames = names.toList()..sort();
    if (complete) return {'status': 'complete', 'known_names': knownNames};
    return {
      'status': 'partial',
      'known_names': knownNames,
      'reason_code': 'params_map_not_fully_static',
    };
  }

  void _recordMethodInvocation(MethodInvocation node) {
    if (node.methodName.name != 'rpc') return;
    final rawElement = node.methodName.element;
    final element = _supabaseRpcElement(rawElement);
    if (element == null) {
      if (rawElement != null) return;
      if (_receiverOwnership(node) != _RpcReceiverOwnership.other) {
        enumerationComplete = false;
      }
      return;
    }
    _recordResolvedInvocation(
      node,
      element,
      node.realTarget?.staticType?.getDisplayString() ?? '',
    );
  }

  void _recordFunctionExpressionInvocation(FunctionExpressionInvocation node) {
    final element = _supabaseRpcElement(_tearOffElement(node.function));
    if (element == null) return;
    _recordResolvedInvocation(
      node,
      element,
      _tearOffReceiverType(node.function)?.getDisplayString() ?? '',
    );
  }

  void _recordResolvedInvocation(
    InvocationExpression node,
    MethodElement element,
    String receiverType,
  ) {
    final library = element.library!.uri;
    final owner = element.enclosingElement as ClassElement;
    final libraryUri = library.toString();
    final bindings = _bindArguments(node, element);
    facts.add({
      'path': path,
      'start_offset': node.offset,
      'end_offset': node.end,
      'resolved_callee_identity':
          '$libraryUri#${owner.displayName}.${element.displayName}',
      'receiver_type': receiverType,
      'invocation': bindings.contract,
      'schema_selection': {
        'status': 'unknown',
        'reason_code': 'schema_not_observed',
      },
      'routine': _routineEvidence(element, bindings),
      'params': _paramsEvidence(element, bindings),
      'syntactic_argument_count': node.argumentList.arguments.length,
      'anchor': _anchor(path, lineInfo, node.offset, node.end),
    });
  }

  @override
  void visitMethodInvocation(MethodInvocation node) {
    _recordMethodInvocation(node);
    super.visitMethodInvocation(node);
  }

  @override
  void visitFunctionExpressionInvocation(FunctionExpressionInvocation node) {
    _recordFunctionExpressionInvocation(node);
    super.visitFunctionExpressionInvocation(node);
  }

  @override
  void visitPrefixedIdentifier(PrefixedIdentifier node) {
    _recordTearOff(
      node,
      node.identifier.element,
      node.identifier.name,
      node.prefix.staticType,
    );
    super.visitPrefixedIdentifier(node);
  }

  @override
  void visitPropertyAccess(PropertyAccess node) {
    _recordTearOff(
      node,
      node.propertyName.element,
      node.propertyName.name,
      node.realTarget.staticType,
    );
    super.visitPropertyAccess(node);
  }

  @override
  void visitSimpleIdentifier(SimpleIdentifier node) {
    final parent = node.parent;
    final belongsToHandledInvocation =
        parent is MethodInvocation && identical(parent.methodName, node);
    final belongsToHandledSelector =
        (parent is PrefixedIdentifier && identical(parent.identifier, node)) ||
        (parent is PropertyAccess && identical(parent.propertyName, node));
    if (!belongsToHandledInvocation && !belongsToHandledSelector) {
      _recordTearOff(node, node.element, node.name, null);
    }
    super.visitSimpleIdentifier(node);
  }

  void _recordTearOff(
    AstNode node,
    Element? rawElement,
    String name,
    DartType? receiverType,
  ) {
    if (name != 'rpc') return;
    final element = _supabaseRpcElement(rawElement);
    if (element != null) {
      if (!_isDirectInvocationFunction(node)) enumerationComplete = false;
      return;
    }
    if (rawElement != null) return;
    if (_receiverOwnershipForType(receiverType) !=
        _RpcReceiverOwnership.other) {
      enumerationComplete = false;
    }
  }
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
    final sourcePath = _relativePath(
      repoRoot,
      libraryFragment.source.fullName,
      eligiblePaths,
    );
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
    final descriptor = _Descriptor(
      element,
      sourcePath,
      id,
      kind,
      name,
      qualifiedName,
      anchor,
    );
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
    final key =
        '${caller.id}|${callee.id}|${anchor['start_line']}|${anchor['start_col']}';
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
    _withCaller(
      node.declaredFragment?.element,
      () => node.functionExpression.body.accept(this),
    );
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
  void visitRedirectingConstructorInvocation(
    RedirectingConstructorInvocation node,
  ) {
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
      for (final value
          in input['paths'] is List
              ? input['paths'] as List
              : const <dynamic>[])
        p.posix.normalize(value.toString().replaceAll('\\', '/')),
    };
    final selected = {
      for (final value
          in input['analysis_paths'] is List
              ? input['analysis_paths'] as List
              : const <dynamic>[])
        p.posix.normalize(value.toString().replaceAll('\\', '/')),
    }.intersection(eligible);

    final provider = PhysicalResourceProvider.INSTANCE;
    final selectedFiles = selected
        .map((relative) => p.normalize(p.join(repoRoot, relative)))
        .toList();
    final collection = AnalysisContextCollection(
      includedPaths: selectedFiles,
      excludedPaths: [p.join(repoRoot, '.git'), p.join(repoRoot, 'build')],
      resourceProvider: provider,
      sdkPath: input['sdk_path']?.toString(),
    );
    final analyzedPaths = <String>{};
    final failedPaths = <String>{};
    final rpcAnalyzedPaths = <String>{};
    final rpcFailedPaths = <String>{};
    final symbols = <String, _Descriptor>{};
    final calls = <String, Map<String, dynamic>>{};
    final rpcInvocations = <Map<String, dynamic>>[];
    try {
      for (final relative in selected.toList()..sort()) {
        final absolute = p.normalize(p.join(repoRoot, relative));
        try {
          final result = await collection
              .contextFor(absolute)
              .currentSession
              .getResolvedUnit(absolute);
          if (result is! ResolvedUnitResult) {
            failedPaths.add(relative);
            rpcFailedPaths.add(relative);
            continue;
          }
          final collector = _Collector(
            repoRoot,
            relative,
            result.lineInfo,
            eligible,
          );
          result.unit.accept(collector);
          symbols.addAll(collector.symbols);
          calls.addAll(collector.calls);
          final rpcCollector = _RpcCollector(relative, result.lineInfo);
          result.unit.accept(rpcCollector);
          rpcInvocations.addAll(rpcCollector.facts);
          if (rpcCollector.enumerationComplete) {
            rpcAnalyzedPaths.add(relative);
          } else {
            rpcFailedPaths.add(relative);
          }
          analyzedPaths.add(relative);
        } catch (_) {
          failedPaths.add(relative);
          rpcFailedPaths.add(relative);
        }
      }
    } finally {
      await collection.dispose();
    }
    final symbolValues = symbols.values.toList()
      ..sort((left, right) => left.id.compareTo(right.id));
    final symbolIds = symbolValues.map((value) => value.id).toSet();
    final callValues =
        calls.values
            .where(
              (value) =>
                  symbolIds.contains(value['caller_provider_symbol_id']) &&
                  symbolIds.contains(value['callee_provider_symbol_id']),
            )
            .toList()
          ..sort((left, right) {
            final leftKey =
                '${left['caller_provider_symbol_id']}|${left['callee_provider_symbol_id']}|${left['anchor']['start_line']}|${left['anchor']['start_col']}';
            final rightKey =
                '${right['caller_provider_symbol_id']}|${right['callee_provider_symbol_id']}|${right['anchor']['start_line']}|${right['anchor']['start_col']}';
            return leftKey.compareTo(rightKey);
          });
    stdout.write(
      jsonEncode({
        'ok': true,
        'provider': 'dart_analyzer',
        'analyzed_paths': analyzedPaths.toList()..sort(),
        'failed_paths': failedPaths.toList()..sort(),
        'rpc_analyzed_paths': rpcAnalyzedPaths.toList()..sort(),
        'rpc_failed_paths': rpcFailedPaths.toList()..sort(),
        'symbols': symbolValues.map((value) => value.toJson()).toList(),
        'calls': callValues,
        'rpc_invocations': rpcInvocations,
      }),
    );
  } catch (error, stack) {
    if (exitCode == 0) {
      stdout.write(jsonEncode({'ok': false, 'error': '$error\n$stack'}));
      exitCode = 1;
    }
  }
}
