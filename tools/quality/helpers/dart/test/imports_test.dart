import 'package:analyzer/dart/analysis/utilities.dart';
import 'package:quality_dart_helper/imports.dart';
import 'package:test/test.dart';

const Package package = (name: 'pkg', root: '/pkg');
const file = '/pkg/lib/a.dart';

List<Reference> referencesOf(String source) {
  final parsed = parseString(content: source, throwIfDiagnostics: false);
  return references(parsed.unit, parsed.lineInfo, file, package);
}

bool partOf(String source) =>
    isPart(parseString(content: source, throwIfDiagnostics: false).unit);

void main() {
  group('references', _references);
  group('resolution', _resolution);
}

void _references() {
  test('directives and configurations are references in source order', () {
    const source = '''
library;
import 'dart:io';
export 'b.dart' show B;
import 'stub.dart'
    if (dart.library.io) 'io.dart'
    if (dart.library.js_interop) 'web.dart';
part 'p.dart';
void f() {}''';
    expect(referencesOf(source), [
      (specifier: 'dart:io', line: 2, resolved: null),
      (specifier: 'b.dart', line: 3, resolved: '/pkg/lib/b.dart'),
      (specifier: 'stub.dart', line: 4, resolved: '/pkg/lib/stub.dart'),
      (specifier: 'io.dart', line: 5, resolved: '/pkg/lib/io.dart'),
      (specifier: 'web.dart', line: 6, resolved: '/pkg/lib/web.dart'),
      (specifier: 'p.dart', line: 7, resolved: '/pkg/lib/p.dart'),
    ]);
  });
  test('a file without directives has no references', () {
    expect(referencesOf('void f() {}'), isEmpty);
  });
  test('a part of directive is no reference but marks a part', () {
    const source = "part of 'a.dart';\nvoid f() {}";
    expect(referencesOf(source), isEmpty);
    expect(partOf(source), isTrue);
    expect(partOf("import 'b.dart';"), isFalse);
  });
  test('an interpolated URI is reported as written and resolves to null', () {
    const source = "import 'a\${b}.dart';";
    expect(referencesOf(source), [
      (specifier: "'a\${b}.dart'", line: 1, resolved: null),
    ]);
  });
}

void _resolution() {
  test('dart libraries and other schemes are outside the package', () {
    expect(resolve('dart:io', file, package), isNull);
    expect(resolve('file:///pkg/lib/b.dart', file, package), isNull);
    expect(resolve('http://example.com/b.dart', file, package), isNull);
  });
  test('an invalid URI resolves to null', () {
    expect(resolve('http://[bad', file, package), isNull);
  });
  test('a package URI resolves only into the lib of this package', () {
    expect(resolve('package:pkg/b.dart', file, package), '/pkg/lib/b.dart');
    expect(
      resolve('package:pkg/src/c.dart', '/pkg/test/t.dart', package),
      '/pkg/lib/src/c.dart',
    );
    expect(resolve('package:other/b.dart', file, package), isNull);
    expect(resolve('package:pkg', file, package), isNull);
    expect(resolve('package:pkg/', file, package), isNull);
    expect(resolve('package:pkg/../b.dart', file, package), isNull);
    expect(resolve('package:pkg/src/../../b.dart', file, package), isNull);
  });
  test('a package root with a trailing separator resolves the same', () {
    const Package slashed = (name: 'pkg', root: '/pkg/');
    expect(resolve('package:pkg/b.dart', file, slashed), '/pkg/lib/b.dart');
  });
  test('a relative reference resolves against the referring file', () {
    expect(resolve('b.dart', file, package), '/pkg/lib/b.dart');
    expect(resolve('sub/../b.dart', file, package), '/pkg/lib/b.dart');
    expect(resolve('../test/c.dart', file, package), '/pkg/test/c.dart');
    expect(resolve('/elsewhere/d.dart', file, package), '/elsewhere/d.dart');
  });
}
