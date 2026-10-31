import 'dart:convert';
import 'dart:io';

import 'package:quality_dart_helper/cli.dart';
import 'package:quality_dart_helper/protocol.dart';
import 'package:test/test.dart';

import '../bin/main.dart' as entry;

late Directory scratch;

/// Write [content] to [name] in the scratch directory; return its path.
String write(String name, String content) {
  final file = File('${scratch.path}/$name')..writeAsStringSync(content);
  return file.path;
}

/// A request document for [files] of a package rooted in the scratch
/// directory.
String request(List<String> files) => jsonEncode({
  'protocol': 1,
  'package': {'name': 'pkg', 'root': scratch.path},
  'files': files,
});

typedef Outcome = ({int status, String out, String err});

Outcome runWith(List<String> arguments, {Map<String, Command>? available}) {
  final out = StringBuffer();
  final err = StringBuffer();
  final status = run(arguments, out, err, available: available);
  return (status: status, out: '$out', err: '$err');
}

/// Expect a bad request: status 2, nothing on stdout, and one line on stderr
/// that contains [message].
void expectRejected(Outcome outcome, String message) {
  expect(outcome.status, 2);
  expect(outcome.out, isEmpty);
  expect(outcome.err, endsWith('\n'));
  expect(outcome.err.trimRight(), isNot(contains('\n')));
  expect(outcome.err, contains(message));
}

/// Expect [document] to be rejected as a `structure` request.
void expectInvalid(String document, String message) {
  expectRejected(
    runWith(['structure', write('request.json', document)]),
    message,
  );
}

void main() {
  setUp(() => scratch = Directory.systemTemp.createTempSync('dart-helper-'));
  tearDown(() => scratch.deleteSync(recursive: true));
  group('arguments', _arguments);
  group('request documents', _documents);
  group('packages', _packages);
  group('files', _files);
  group('responses', _responses);
  group('entry point', _entry);
}

void _arguments() {
  test('a command and a request file are required', () {
    expectRejected(runWith([]), 'usage: main.dart <command> <request.json>');
    expectRejected(runWith(['structure']), 'usage:');
    expectRejected(runWith(['structure', 'a', 'b']), 'usage:');
  });
  test('an unknown command is rejected before the request is read', () {
    expectRejected(
      runWith(['measure', '/missing']),
      'unknown command "measure"',
    );
  });
}

void _documents() {
  test('an unreadable request file is rejected', () {
    expectRejected(
      runWith(['structure', '${scratch.path}/missing.json']),
      'cannot read the request',
    );
  });
  test('a request that is not a JSON object is rejected', () {
    expectInvalid('{"protocol": 1', 'the request is not JSON');
    expectInvalid('[1]', 'the request is not a JSON object');
  });
  test('a request of another or no protocol is rejected', () {
    expectInvalid('{"protocol": 2}', 'does not use protocol 1');
    expectInvalid('{"protocol": "1"}', 'does not use protocol 1');
    expectInvalid('{}', 'does not use protocol 1');
  });
}

/// A valid `structure` request document with [fields] replaced.
String document(Map<String, Object?> fields) => jsonEncode({
  'protocol': 1,
  'package': {'name': 'pkg', 'root': '/pkg'},
  'files': <String>[],
  ...fields,
});

void _packages() {
  test('the package must be an object with a name and an absolute root', () {
    expectInvalid(document({'package': 'pkg'}), '"package" must be an object');
    expectInvalid(document({'package': null}), '"package" must be an object');
    expectInvalid(
      document({
        'package': {'root': '/pkg'},
      }),
      '"name" must be a non-empty string',
    );
    expectInvalid(
      document({
        'package': {'name': '', 'root': '/pkg'},
      }),
      '"name" must be a non-empty string',
    );
    expectInvalid(
      document({
        'package': {'name': 'pkg', 'root': 1},
      }),
      '"root" must be a non-empty string',
    );
    expectInvalid(
      document({
        'package': {'name': 'pkg', 'root': 'pkg'},
      }),
      'the package root pkg is not absolute',
    );
  });
}

void _files() {
  test('the files must be a list of absolute paths', () {
    expectInvalid(document({'files': null}), '"files" must be a list of paths');
    expectInvalid(document({'files': '/a.dart'}), '"files" must be a list');
    expectInvalid(
      document({
        'files': [1],
      }),
      '"files" must be a list of paths',
    );
    expectInvalid(
      document({
        'files': ['/a.dart', 'b.dart'],
      }),
      'the file b.dart is not absolute',
    );
  });
  test('a request error reads as its message', () {
    expect('${RequestError('bad request')}', 'bad request');
  });
  test('a message of a bad request stays on one line', () {
    final outcome = runWith(['structure', '${scratch.path}/bad\nname.json']);
    expectRejected(outcome, 'cannot read the request');
  });
}

void _responses() {
  test('a response is one JSON line on stdout with status 0', () {
    final source = write('a.dart', "import 'b.dart';\nint f() => 1;\n");
    final outcome = runWith([
      'structure',
      write('r.json', request([source])),
    ]);
    expect(outcome.status, 0);
    expect(outcome.err, isEmpty);
    expect(outcome.out.trimRight(), isNot(contains('\n')));
    expect(jsonDecode(outcome.out), {
      'protocol': 1,
      'files': [
        {
          'file': source,
          'part': false,
          'functions': [
            {
              'name': 'f',
              'line': 2,
              'code_lines': 1,
              'complexity': 1,
              'nesting': 0,
              'parameters': 0,
            },
          ],
          'imports': [
            {
              'specifier': 'b.dart',
              'line': 1,
              'resolved': '${scratch.path}/b.dart',
            },
          ],
          'error': null,
        },
      ],
    });
  });
  test('an unexpected failure exits 1 with the first line of the error', () {
    Map<String, Object?> fail(Map<String, Object?> request) =>
        throw StateError('first line\nsecond line');
    final outcome = runWith(
      ['boom', write('r.json', request([]))],
      available: {'boom': fail},
    );
    expect(outcome.status, 1);
    expect(outcome.out, isEmpty);
    expect(outcome.err, 'Bad state: first line\n');
  });
}

void _entry() {
  test('the entry point sets the exit status', () {
    addTearDown(() => exitCode = 0);
    exitCode = 3;
    entry.main(['structure', write('r.json', request([]))]);
    expect(exitCode, 0);
  });
}
