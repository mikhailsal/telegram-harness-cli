/// The `structure` command: functions, references, and part status per file.
///
/// Each file is parsed on its own with the latest language version. A file
/// that does not parse, or cannot be read, reports an error and no metrics.
library;

import 'dart:io';

import 'package:analyzer/dart/analysis/utilities.dart';

import 'functions.dart';
import 'imports.dart';
import 'protocol.dart';

/// The response to a `structure` request, files in request order.
Map<String, Object?> structure(StructureRequest request) => {
  'protocol': protocol,
  'files': [for (final file in request.files) measure(file, request.package)],
};

/// The metrics of one file of [package].
Map<String, Object?> measure(String file, Package package) {
  final String content;
  try {
    content = File(file).readAsStringSync();
  } on FileSystemException catch (error) {
    return _failed(file, null, 'cannot read $file: ${error.message}');
  }
  final parsed = parseString(
    content: content,
    path: file,
    throwIfDiagnostics: false,
  );
  final lines = parsed.lineInfo;
  if (parsed.errors.isNotEmpty) {
    final first = parsed.errors.first;
    final line = lines.getLocation(first.offset).lineNumber;
    return _failed(file, line, first.message, part: isPart(parsed.unit));
  }
  return {
    'file': file,
    'part': isPart(parsed.unit),
    'functions': [
      for (final metrics in functions(parsed.unit, lines))
        {
          'name': metrics.name,
          'line': metrics.line,
          'code_lines': metrics.codeLines,
          'complexity': metrics.complexity,
          'nesting': metrics.nesting,
          'parameters': metrics.parameters,
        },
    ],
    'imports': [
      for (final reference in references(parsed.unit, lines, file, package))
        {
          'specifier': reference.specifier,
          'line': reference.line,
          'resolved': reference.resolved,
        },
    ],
    'error': null,
  };
}

Map<String, Object?> _failed(
  String file,
  int? line,
  String message, {
  bool part = false,
}) => {
  'file': file,
  'part': part,
  'functions': const <Object?>[],
  'imports': const <Object?>[],
  'error': {'line': line, 'message': message},
};
