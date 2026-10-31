/// The syntax tree walk shared by the metrics: children and function shapes.
library;

import 'package:analyzer/dart/ast/ast.dart';

/// The parts of a function-like node that its metrics read.
typedef Shape = ({
  FunctionBody body,
  FormalParameterList? parameters,
  List<ConstructorInitializer> initializers,
});

/// The child nodes of [node], in source order.
Iterable<AstNode> children(AstNode node) =>
    node.childEntities.whereType<AstNode>();

/// The shape of a function-like node, or null for any other node.
///
/// The function expression of a declaration belongs to the declaration, so
/// only a closure is a function of its own.
Shape? shapeOf(AstNode node) => switch (node) {
  FunctionDeclaration(:final functionExpression) => (
    body: functionExpression.body,
    parameters: functionExpression.parameters,
    initializers: const [],
  ),
  MethodDeclaration(:final body, :final parameters) => (
    body: body,
    parameters: parameters,
    initializers: const [],
  ),
  ConstructorDeclaration(:final body, :final parameters, :final initializers) =>
    (body: body, parameters: parameters, initializers: initializers),
  FunctionExpression(:final parent) when parent is! FunctionDeclaration => (
    body: node.body,
    parameters: node.parameters,
    initializers: const [],
  ),
  _ => null,
};

/// Whether a function has code to measure: a body or an initializer list.
///
/// Abstract and external members, redirecting factory constructors, and
/// constructors without initializers have an empty body.
bool hasBody(Shape shape) =>
    shape.body is BlockFunctionBody ||
    shape.body is ExpressionFunctionBody ||
    shape.initializers.isNotEmpty;

/// The nodes a function's complexity and nesting are counted over.
List<AstNode> bodyNodes(Shape shape) => [...shape.initializers, shape.body];
