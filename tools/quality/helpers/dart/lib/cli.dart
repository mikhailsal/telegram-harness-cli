/// The command line: `main.dart <command> <request.json>`.
///
/// The response is one JSON document on standard output and exit status 0. A
/// bad request exits 2 and any other failure 1, each with a one-line message
/// on standard error and nothing on standard output.
library;

import 'dart:convert';

import 'protocol.dart';
import 'structure.dart';

/// A command: it answers a validated request document.
typedef Command = Map<String, Object?> Function(Map<String, Object?> request);

/// The commands of the helper.
final Map<String, Command> commands = {
  'structure': (request) => structure(structureRequest(request)),
};

const _usage = 'usage: main.dart <command> <request.json>';

/// Run the command line with [arguments]; return the exit status.
int run(
  List<String> arguments,
  StringSink out,
  StringSink err, {
  Map<String, Command>? available,
}) {
  final known = available ?? commands;
  try {
    if (arguments.length != 2) {
      throw RequestError(_usage);
    }
    final command = known[arguments.first];
    if (command == null) {
      throw RequestError('unknown command "${arguments.first}"; $_usage');
    }
    out.writeln(jsonEncode(command(readRequest(arguments.last))));
    return 0;
  } on RequestError catch (error) {
    err.writeln(_oneLine(error.message));
    return 2;
  } on Object catch (error) {
    err.writeln(_oneLine('$error'));
    return 1;
  }
}

String _oneLine(String text) => text.split('\n').first;
