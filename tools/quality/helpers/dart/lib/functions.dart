/// Every measured function of a compilation unit, with its qualified name.
///
/// Functions are measured at any depth, each on its own: a nested function's
/// lines also count toward the enclosing body, but its decisions and nesting
/// do not.
library;

import 'package:analyzer/dart/ast/ast.dart';
import 'package:analyzer/source/line_info.dart';

import 'complexity.dart';
import 'lines.dart';
import 'naming.dart';
import 'nesting.dart';
import 'tree.dart';

/// The metrics of one function, as the protocol reports them.
typedef FunctionMetrics = ({
  String name,
  int line,
  int codeLines,
  int complexity,
  int nesting,
  int parameters,
});

/// The functions of [unit] in source order.
List<FunctionMetrics> functions(CompilationUnit unit, LineInfo lines) {
  final found = <FunctionMetrics>[];
  _visit(unit, const [], lines, found);
  return found;
}

void _visit(
  AstNode node,
  List<String> scope,
  LineInfo lines,
  List<FunctionMetrics> found,
) {
  for (final child in children(node)) {
    final shape = shapeOf(child);
    var inner = scope;
    if (shape != null) {
      inner = [...scope, functionName(child)];
      if (hasBody(shape)) {
        found.add(_measure(child, shape, inner.join('.'), lines));
      }
    } else if (scopeName(child) case final segment?) {
      inner = [...scope, segment];
    }
    _visit(child, inner, lines, found);
  }
}

FunctionMetrics _measure(
  AstNode node,
  Shape shape,
  String name,
  LineInfo lines,
) {
  final nodes = bodyNodes(shape);
  return (
    name: name,
    line: lines.getLocation(_start(node)).lineNumber,
    codeLines: codeLines(shape, lines),
    complexity: complexity(nodes),
    nesting: nesting(nodes),
    parameters: shape.parameters?.parameters.length ?? 0,
  );
}

/// The offset where a function starts: a declaration after its doc comment
/// and annotations.
int _start(AstNode node) => switch (node) {
  AnnotatedNode(:final firstTokenAfterCommentAndMetadata) =>
    firstTokenAfterCommentAndMetadata.offset,
  _ => node.offset,
};
