import 'dart:io';

import 'package:quality_dart_helper/structure.dart';
import 'package:test/test.dart';

/// The helper's own Dart files; the tests run in the package directory.
List<String> ownFiles() => [
  for (final root in ['bin', 'lib', 'test'])
    for (final entity in Directory(root).listSync(recursive: true))
      if (entity is File && entity.path.endsWith('.dart')) entity.absolute.path,
]..sort();

void main() {
  test('the helper meets the structure limits it measures', _limits);
  test('the helper resolves its own references to existing files', _edges);
}

void _limits() {
  expect([for (final file in ownFiles()) ...violations(file)], isEmpty);
}

/// The structure limits [file] exceeds, or why it cannot be measured.
List<String> violations(String file) {
  final package = (name: 'quality_dart_helper', root: Directory.current.path);
  final result = measure(file, package);
  return [
    if (result['error'] != null) '$file: ${result['error']}',
    if (File(file).readAsLinesSync().length > 250) '$file: over 250 lines',
    for (final function in result['functions'] as List<Object?>)
      for (final MapEntry(:key, :value) in limits.entries)
        if (((function as Map<String, Object?>)[key] as int) > value)
          '$file ${function['name']}: $key ${function[key]} > $value',
  ];
}

const limits = {
  'complexity': 10,
  'nesting': 3,
  'parameters': 5,
  'code_lines': 50,
};

void _edges() {
  final package = (name: 'quality_dart_helper', root: Directory.current.path);
  final resolved = [
    for (final file in ownFiles())
      for (final reference
          in measure(file, package)['imports'] as List<Object?>)
        if ((reference as Map<String, Object?>)['resolved']
            case final String path)
          path,
  ];
  expect(resolved, isNotEmpty);
  for (final path in resolved) {
    expect(File(path).existsSync(), isTrue, reason: path);
  }
}
