import 'dart:io';

import 'package:quality_dart_helper/cli.dart';

/// Measure Dart files for the quality gate; see `lib/cli.dart`.
void main(List<String> arguments) {
  exitCode = run(arguments, stdout, stderr);
}
