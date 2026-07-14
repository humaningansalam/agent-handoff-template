using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Web.Script.Serialization;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;
using Microsoft.CodeAnalysis.Operations;
using Microsoft.CodeAnalysis.Text;

public sealed class ProviderInput
{
    public string repo_root { get; set; }
    public ProjectInput[] projects { get; set; }
}

public sealed class ProjectInput
{
    public string name { get; set; }
    public string[] paths { get; set; }
    public string[] references { get; set; }
    public string[] defines { get; set; }
}

public sealed class CompilationBundle
{
    public ProjectInput Input;
    public CSharpCompilation Compilation;
    public Dictionary<string, SyntaxTree> TreesByPath;
    public HashSet<string> FailedPaths;
}

public sealed class SymbolDescriptor
{
    public ISymbol Symbol;
    public SyntaxNode Declaration;
    public string Path;
    public string ProviderSymbolId;
    public string Kind;
    public string Name;
    public string QualifiedName;
    public Dictionary<string, object> Anchor;
}

public sealed class CallWalker : CSharpSyntaxWalker
{
    private readonly SemanticModel model;
    private readonly Action<SyntaxNode, ISymbol> onCall;

    public CallWalker(SemanticModel model, Action<SyntaxNode, ISymbol> onCall)
    {
        this.model = model;
        this.onCall = onCall;
    }

    public override void VisitInvocationExpression(InvocationExpressionSyntax node)
    {
        var info = model.GetSymbolInfo(node);
        if (info.Symbol != null)
            onCall(node, info.Symbol);
        base.VisitInvocationExpression(node);
    }

    public override void VisitObjectCreationExpression(ObjectCreationExpressionSyntax node)
    {
        var info = model.GetSymbolInfo(node);
        if (info.Symbol != null)
            onCall(node, info.Symbol);
        base.VisitObjectCreationExpression(node);
    }

    public override void VisitAnonymousMethodExpression(AnonymousMethodExpressionSyntax node) { }
    public override void VisitSimpleLambdaExpression(SimpleLambdaExpressionSyntax node) { }
    public override void VisitParenthesizedLambdaExpression(ParenthesizedLambdaExpressionSyntax node) { }
    public override void VisitLocalFunctionStatement(LocalFunctionStatementSyntax node) { }
}

static string NormalizePath(string value)
{
    return Path.GetFullPath(value).Replace('\\', '/');
}

static string RelativePath(string repoRoot, string value)
{
    var root = NormalizePath(repoRoot).TrimEnd('/') + "/";
    var path = NormalizePath(value);
    if (!path.StartsWith(root, StringComparison.Ordinal))
        return "";
    return path.Substring(root.Length);
}

static Dictionary<string, object> Anchor(SyntaxNode node, string path)
{
    var span = node.SyntaxTree.GetLineSpan(node.Span);
    return new Dictionary<string, object>
    {
        { "path", path },
        { "start_line", span.StartLinePosition.Line + 1 },
        { "start_col", span.StartLinePosition.Character },
        { "end_line", span.EndLinePosition.Line + 1 },
        { "end_col", span.EndLinePosition.Character },
    };
}

static string KindFor(ISymbol symbol, SyntaxNode declaration)
{
    var type = symbol as INamedTypeSymbol;
    if (type != null)
    {
        switch (type.TypeKind)
        {
            case TypeKind.Class: return "class";
            case TypeKind.Struct: return "struct";
            case TypeKind.Interface: return "interface";
            case TypeKind.Enum: return "enum";
            case TypeKind.Delegate: return "delegate";
            default: return "type";
        }
    }
    var method = symbol as IMethodSymbol;
    if (method != null)
    {
        if (declaration is AnonymousFunctionExpressionSyntax) return "lambda";
        switch (method.MethodKind)
        {
            case MethodKind.Constructor:
            case MethodKind.StaticConstructor: return "constructor";
            case MethodKind.LocalFunction: return "function";
            case MethodKind.PropertyGet: return "getter";
            case MethodKind.PropertySet: return "setter";
            case MethodKind.UserDefinedOperator:
            case MethodKind.Conversion: return "operator";
            default: return method.ContainingType != null ? "method" : "function";
        }
    }
    if (symbol is IPropertySymbol) return "property";
    return "symbol";
}

static string NameFor(ISymbol symbol, SyntaxNode declaration, Dictionary<string, object> anchor)
{
    if (declaration is AnonymousFunctionExpressionSyntax)
        return "<lambda@" + anchor["start_line"] + ":" + anchor["start_col"] + ">";
    var method = symbol as IMethodSymbol;
    if (method != null && (method.MethodKind == MethodKind.Constructor || method.MethodKind == MethodKind.StaticConstructor))
        return method.ContainingType != null ? method.ContainingType.Name : "constructor";
    return symbol.Name;
}

static string QualifiedNameFor(ISymbol symbol, string ownName)
{
    var parts = new List<string> { ownName };
    var current = symbol.ContainingSymbol;
    while (current != null)
    {
        var ns = current as INamespaceSymbol;
        if (ns != null)
        {
            if (!ns.IsGlobalNamespace && !String.IsNullOrEmpty(ns.Name))
                parts.Add(ns.Name);
        }
        else if (current is INamedTypeSymbol || current is IMethodSymbol)
        {
            if (!String.IsNullOrEmpty(current.Name))
                parts.Add(current.Name);
        }
        current = current.ContainingSymbol;
    }
    parts.Reverse();
    return String.Join(".", parts);
}

static ISymbol SymbolForNode(SemanticModel model, SyntaxNode node)
{
    var anonymous = node as AnonymousFunctionExpressionSyntax;
    if (anonymous != null)
    {
        var operation = model.GetOperation(anonymous) as IAnonymousFunctionOperation;
        return operation != null ? operation.Symbol : null;
    }
    return model.GetDeclaredSymbol(node);
}

static IEnumerable<SyntaxNode> DeclarationNodes(SyntaxNode root)
{
    foreach (var node in root.DescendantNodesAndSelf())
    {
        if (node is BaseTypeDeclarationSyntax
            || node is DelegateDeclarationSyntax
            || node is MethodDeclarationSyntax
            || node is ConstructorDeclarationSyntax
            || node is DestructorDeclarationSyntax
            || node is AccessorDeclarationSyntax
            || node is LocalFunctionStatementSyntax
            || node is OperatorDeclarationSyntax
            || node is ConversionOperatorDeclarationSyntax
            || node is PropertyDeclarationSyntax
            || node is AnonymousFunctionExpressionSyntax)
            yield return node;
    }
}

static SyntaxNode ExecutableBody(SyntaxNode declaration)
{
    var method = declaration as MethodDeclarationSyntax;
    if (method != null) return (SyntaxNode)method.Body ?? (method.ExpressionBody != null ? method.ExpressionBody.Expression : null);
    var ctor = declaration as ConstructorDeclarationSyntax;
    if (ctor != null) return (SyntaxNode)ctor.Body ?? (ctor.ExpressionBody != null ? ctor.ExpressionBody.Expression : null);
    var dtor = declaration as DestructorDeclarationSyntax;
    if (dtor != null) return (SyntaxNode)dtor.Body ?? (dtor.ExpressionBody != null ? dtor.ExpressionBody.Expression : null);
    var accessor = declaration as AccessorDeclarationSyntax;
    if (accessor != null) return (SyntaxNode)accessor.Body ?? (accessor.ExpressionBody != null ? accessor.ExpressionBody.Expression : null);
    var local = declaration as LocalFunctionStatementSyntax;
    if (local != null) return (SyntaxNode)local.Body ?? (local.ExpressionBody != null ? local.ExpressionBody.Expression : null);
    var op = declaration as OperatorDeclarationSyntax;
    if (op != null) return (SyntaxNode)op.Body ?? (op.ExpressionBody != null ? op.ExpressionBody.Expression : null);
    var conversion = declaration as ConversionOperatorDeclarationSyntax;
    if (conversion != null) return (SyntaxNode)conversion.Body ?? (conversion.ExpressionBody != null ? conversion.ExpressionBody.Expression : null);
    var property = declaration as PropertyDeclarationSyntax;
    if (property != null) return property.ExpressionBody != null ? property.ExpressionBody.Expression : null;
    var simple = declaration as SimpleLambdaExpressionSyntax;
    if (simple != null) return simple.Body;
    var parenthesized = declaration as ParenthesizedLambdaExpressionSyntax;
    if (parenthesized != null) return parenthesized.Body;
    var anonymous = declaration as AnonymousMethodExpressionSyntax;
    if (anonymous != null) return anonymous.Block;
    return null;
}

static ISymbol NormalizeSymbol(ISymbol symbol)
{
    var method = symbol as IMethodSymbol;
    if (method != null)
    {
        if (method.ReducedFrom != null) method = method.ReducedFrom;
        return method.OriginalDefinition;
    }
    return symbol.OriginalDefinition;
}

static string DocumentationKey(ISymbol symbol)
{
    symbol = NormalizeSymbol(symbol);
    var documentationId = symbol.GetDocumentationCommentId();
    var assembly = symbol.ContainingAssembly != null ? symbol.ContainingAssembly.Name : "";
    return !String.IsNullOrEmpty(documentationId) ? assembly + "|" + documentationId : "";
}

static CompilationBundle BuildCompilation(ProjectInput input, string repoRoot)
{
    var defines = (input.defines ?? new string[0]).Where(value => !String.IsNullOrWhiteSpace(value));
    var parseOptions = CSharpParseOptions.Default.WithLanguageVersion(LanguageVersion.Preview).WithPreprocessorSymbols(defines);
    var trees = new Dictionary<string, SyntaxTree>(StringComparer.Ordinal);
    var failed = new HashSet<string>(StringComparer.Ordinal);
    foreach (var relative in input.paths ?? new string[0])
    {
        var absolute = Path.Combine(repoRoot, relative.Replace('/', Path.DirectorySeparatorChar));
        try
        {
            var text = File.ReadAllText(absolute, Encoding.UTF8);
            var tree = CSharpSyntaxTree.ParseText(SourceText.From(text, Encoding.UTF8), parseOptions, absolute);
            trees[relative] = tree;
            if (tree.GetDiagnostics().Any(diagnostic => diagnostic.Severity == DiagnosticSeverity.Error))
                failed.Add(relative);
        }
        catch
        {
            failed.Add(relative);
        }
    }
    var references = new List<MetadataReference>();
    var referencePaths = new HashSet<string>(StringComparer.Ordinal);
    foreach (var value in (input.references ?? new string[0]).Concat(new[]
    {
        typeof(object).Assembly.Location,
        typeof(Enumerable).Assembly.Location,
        typeof(System.Runtime.GCSettings).Assembly.Location,
    }))
    {
        if (String.IsNullOrWhiteSpace(value) || !File.Exists(value)) continue;
        var normalized = NormalizePath(value);
        if (!referencePaths.Add(normalized)) continue;
        try { references.Add(MetadataReference.CreateFromFile(normalized)); } catch { }
    }
    var compilation = CSharpCompilation.Create(
        String.IsNullOrWhiteSpace(input.name) ? "repoctl_csharp" : input.name,
        trees.Values,
        references,
        new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary, allowUnsafe: true));
    return new CompilationBundle { Input = input, Compilation = compilation, TreesByPath = trees, FailedPaths = failed };
}

var serializer = new JavaScriptSerializer { MaxJsonLength = Int32.MaxValue, RecursionLimit = 256 };
ProviderInput providerInput;
try
{
    providerInput = serializer.Deserialize<ProviderInput>(Console.In.ReadToEnd());
}
catch (Exception error)
{
    Console.Out.Write(serializer.Serialize(new Dictionary<string, object> { { "ok", false }, { "error", error.ToString() } }));
    return;
}

var repoRoot = NormalizePath(providerInput.repo_root);
var bundles = (providerInput.projects ?? new ProjectInput[0]).Select(project => BuildCompilation(project, repoRoot)).ToList();
var descriptors = new Dictionary<ISymbol, SymbolDescriptor>(SymbolEqualityComparer.Default);
var descriptorsById = new Dictionary<string, SymbolDescriptor>(StringComparer.Ordinal);
var descriptorsByDocumentation = new Dictionary<string, List<SymbolDescriptor>>(StringComparer.Ordinal);
var analyzedPaths = new HashSet<string>(StringComparer.Ordinal);
var failedPaths = new HashSet<string>(StringComparer.Ordinal);

foreach (var bundle in bundles)
{
    foreach (var failed in bundle.FailedPaths) failedPaths.Add(failed);
    foreach (var pair in bundle.TreesByPath)
    {
        if (bundle.FailedPaths.Contains(pair.Key)) continue;
        analyzedPaths.Add(pair.Key);
        var model = bundle.Compilation.GetSemanticModel(pair.Value, true);
        foreach (var declaration in DeclarationNodes(pair.Value.GetRoot()))
        {
            var symbol = SymbolForNode(model, declaration);
            if (symbol == null) continue;
            symbol = NormalizeSymbol(symbol);
            if (descriptors.ContainsKey(symbol)) continue;
            var anchor = Anchor(declaration, pair.Key);
            var name = NameFor(symbol, declaration, anchor);
            if (String.IsNullOrEmpty(name)) continue;
            var kind = KindFor(symbol, declaration);
            var qualifiedName = QualifiedNameFor(symbol, name);
            var providerSymbolId = "csharp_roslyn:" + pair.Key + ":" + qualifiedName + ":" + kind + ":" + anchor["start_line"] + ":" + anchor["start_col"] + ":" + anchor["end_line"] + ":" + anchor["end_col"];
            var descriptor = new SymbolDescriptor
            {
                Symbol = symbol,
                Declaration = declaration,
                Path = pair.Key,
                ProviderSymbolId = providerSymbolId,
                Kind = kind,
                Name = name,
                QualifiedName = qualifiedName,
                Anchor = anchor,
            };
            descriptors[symbol] = descriptor;
            descriptorsById[providerSymbolId] = descriptor;
            var documentationKey = DocumentationKey(symbol);
            if (!String.IsNullOrEmpty(documentationKey))
            {
                List<SymbolDescriptor> values;
                if (!descriptorsByDocumentation.TryGetValue(documentationKey, out values))
                {
                    values = new List<SymbolDescriptor>();
                    descriptorsByDocumentation[documentationKey] = values;
                }
                values.Add(descriptor);
            }
        }
    }
}

Func<ISymbol, SymbolDescriptor> resolveDescriptor = delegate(ISymbol rawSymbol)
{
    var symbol = NormalizeSymbol(rawSymbol);
    SymbolDescriptor direct;
    if (descriptors.TryGetValue(symbol, out direct)) return direct;
    var key = DocumentationKey(symbol);
    List<SymbolDescriptor> candidates;
    if (!String.IsNullOrEmpty(key) && descriptorsByDocumentation.TryGetValue(key, out candidates) && candidates.Count == 1)
        return candidates[0];
    return null;
};

var calls = new Dictionary<string, Dictionary<string, object>>(StringComparer.Ordinal);
foreach (var bundle in bundles)
{
    foreach (var pair in bundle.TreesByPath)
    {
        if (bundle.FailedPaths.Contains(pair.Key)) continue;
        var model = bundle.Compilation.GetSemanticModel(pair.Value, true);
        foreach (var caller in descriptorsById.Values.Where(value => value.Path == pair.Key).ToArray())
        {
            if (!(caller.Symbol is IMethodSymbol)) continue;
            var body = ExecutableBody(caller.Declaration);
            if (body == null) continue;
            var walker = new CallWalker(model, delegate(SyntaxNode callNode, ISymbol rawTarget)
            {
                var callee = resolveDescriptor(rawTarget);
                if (callee == null || callee.ProviderSymbolId == caller.ProviderSymbolId) return;
                var anchor = Anchor(callNode, caller.Path);
                var key = caller.ProviderSymbolId + "|" + callee.ProviderSymbolId + "|" + anchor["start_line"] + "|" + anchor["start_col"];
                calls[key] = new Dictionary<string, object>
                {
                    { "path", caller.Path },
                    { "provider", "csharp_roslyn" },
                    { "caller_provider_symbol_id", caller.ProviderSymbolId },
                    { "callee_provider_symbol_id", callee.ProviderSymbolId },
                    { "language", "csharp" },
                    { "scope", caller.Path == callee.Path ? "same_file" : "cross_file_import" },
                    { "anchor", anchor },
                };
            });
            walker.Visit(body);
        }
    }
}

var symbolOutput = descriptorsById.Values
    .Where(value => analyzedPaths.Contains(value.Path))
    .OrderBy(value => value.ProviderSymbolId, StringComparer.Ordinal)
    .Select(value => (object)new Dictionary<string, object>
    {
        { "path", value.Path },
        { "provider", "csharp_roslyn" },
        { "provider_symbol_id", value.ProviderSymbolId },
        { "language", "csharp" },
        { "kind", value.Kind },
        { "name", value.Name },
        { "qualified_name", value.QualifiedName },
        { "anchor", value.Anchor },
    }).ToArray();

var output = new Dictionary<string, object>
{
    { "ok", true },
    { "provider", "csharp_roslyn" },
    { "analyzed_paths", analyzedPaths.OrderBy(value => value, StringComparer.Ordinal).ToArray() },
    { "failed_paths", failedPaths.OrderBy(value => value, StringComparer.Ordinal).ToArray() },
    { "symbols", symbolOutput },
    { "calls", calls.Values.OrderBy(value => (string)value["caller_provider_symbol_id"], StringComparer.Ordinal).ThenBy(value => (string)value["callee_provider_symbol_id"], StringComparer.Ordinal).ToArray() },
};
Console.Out.Write(serializer.Serialize(output));
