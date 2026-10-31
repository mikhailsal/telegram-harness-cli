/// Names of functions and of the declarations that qualify them.
library;

import 'package:analyzer/dart/ast/ast.dart';

const anonymous = '<anonymous>';

/// The own name of a function-like node, without its scopes.
String functionName(AstNode node) => switch (node) {
  FunctionDeclaration(:final name, :final isGetter, :final isSetter) =>
    _accessor(name.lexeme, getter: isGetter, setter: isSetter),
  MethodDeclaration(:final name, :final isOperator) when isOperator =>
    'operator ${name.lexeme}',
  MethodDeclaration(:final name, :final isGetter, :final isSetter) => _accessor(
    name.lexeme,
    getter: isGetter,
    setter: isSetter,
  ),
  ConstructorDeclaration(:final name) => name?.lexeme ?? 'new',
  _ => _closureName(node),
};

/// The scope segment a type or extension declaration adds, if any.
String? scopeName(AstNode node) => switch (node) {
  ClassDeclaration(:final namePart) ||
  EnumDeclaration(:final namePart) ||
  ExtensionTypeDeclaration(:final namePart) => namePart.typeName.lexeme,
  MixinDeclaration(:final name) => name.lexeme,
  ExtensionDeclaration(:final name) => name?.lexeme ?? anonymous,
  _ => null,
};

String _accessor(String name, {required bool getter, required bool setter}) =>
    getter
    ? 'get $name'
    : setter
    ? 'set $name'
    : name;

/// A closure takes the name of what it initializes, is passed as, or is
/// assigned to.
String _closureName(AstNode closure) => switch (closure.parent) {
  VariableDeclaration(:final name) => name.lexeme,
  NamedArgument(:final name) => name.lexeme,
  // A closure is never the target of an assignment, only its value.
  AssignmentExpression(:final leftHandSide) => _target(leftHandSide),
  _ => anonymous,
};

String _target(Expression target) => switch (target) {
  SimpleIdentifier(:final name) => name,
  PrefixedIdentifier(:final identifier) => identifier.name,
  PropertyAccess(:final propertyName) => propertyName.name,
  _ => anonymous,
};
