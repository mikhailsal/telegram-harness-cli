/// Requests of the helper protocol: one JSON document, version 1.
library;

import 'dart:convert';
import 'dart:io';

import 'imports.dart';

const protocol = 1;

/// A request the helper cannot accept; the command line exits with status 2.
final class RequestError implements Exception {
  RequestError(this.message);

  final String message;

  @override
  String toString() => message;
}

/// A `structure` request: the package and the files to measure.
typedef StructureRequest = ({Package package, List<String> files});

/// Read the JSON request in the file at [path].
Map<String, Object?> readRequest(String path) {
  final String text;
  try {
    text = File(path).readAsStringSync();
  } on FileSystemException catch (error) {
    throw RequestError('cannot read the request $path: ${error.message}');
  }
  final Object? document;
  try {
    document = jsonDecode(text);
  } on FormatException catch (error) {
    throw RequestError('the request is not JSON: ${error.message}');
  }
  if (document is! Map<String, Object?>) {
    throw RequestError('the request is not a JSON object');
  }
  if (document['protocol'] != protocol) {
    throw RequestError('the request does not use protocol $protocol');
  }
  return document;
}

/// Validate a `structure` request.
StructureRequest structureRequest(Map<String, Object?> document) {
  final package = document['package'];
  if (package is! Map<String, Object?>) {
    throw RequestError('"package" must be an object');
  }
  return (
    package: (
      name: _text(package, 'name'),
      root: _absolute(_text(package, 'root'), 'package root'),
    ),
    files: _files(document['files']),
  );
}

List<String> _files(Object? value) {
  if (value is! List<Object?> || value.any((path) => path is! String)) {
    throw RequestError('"files" must be a list of paths');
  }
  return [for (final path in value.cast<String>()) _absolute(path, 'file')];
}

String _text(Map<String, Object?> object, String key) {
  final value = object[key];
  if (value is! String || value.isEmpty) {
    throw RequestError('"$key" must be a non-empty string');
  }
  return value;
}

String _absolute(String path, String what) {
  if (!File(path).isAbsolute) {
    throw RequestError('the $what $path is not absolute');
  }
  return path;
}
