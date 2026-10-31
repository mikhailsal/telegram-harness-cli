import 'package:analyzer/dart/analysis/utilities.dart';
import 'package:quality_dart_helper/functions.dart';

/// The measured functions of [source], in source order.
List<FunctionMetrics> measuredList(String source) {
  final parsed = parseString(content: source, throwIfDiagnostics: false);
  if (parsed.errors.isNotEmpty) {
    throw StateError('fixture does not parse: ${parsed.errors.first.message}');
  }
  return functions(parsed.unit, parsed.lineInfo);
}

/// The measured functions of [source], by qualified name.
Map<String, FunctionMetrics> measured(String source) => {
  for (final metrics in measuredList(source)) metrics.name: metrics,
};

/// The metrics of the function [name] in [source].
FunctionMetrics only(String source, [String name = 'f']) {
  final found = measured(source)[name];
  if (found == null) {
    throw StateError('no function $name in ${measured(source).keys}');
  }
  return found;
}
