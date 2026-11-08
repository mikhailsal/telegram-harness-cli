/// The references of a compilation unit and the files they resolve to.
///
/// A reference is the URI of an `import`, `export`, or `part` directive, and
/// of each configuration of a conditional import or export. Only references
/// into the measured package can be dependency edges: `dart:` libraries, other
/// schemes, and other packages, workspace members included, resolve to null.
library;

import 'package:analyzer/dart/ast/ast.dart';
import 'package:analyzer/source/line_info.dart';

/// The package whose files are measured: its pubspec name and root directory.
typedef Package = ({String name, String root});

/// One reference: the URI as written, its line, and the file it names.
typedef Reference = ({String specifier, int line, String? resolved});

/// The references of [unit], a file of [package] at [file], in source order.
List<Reference> references(
  CompilationUnit unit,
  LineInfo lines,
  String file,
  Package package,
) => [
  for (final directive in unit.directives)
    for (final uri in _uris(directive)) _reference(uri, lines, file, package),
];

/// Whether [unit] is a part of another library.
bool isPart(CompilationUnit unit) =>
    unit.directives.any((directive) => directive is PartOfDirective);

List<StringLiteral> _uris(Directive directive) => switch (directive) {
  NamespaceDirective(:final uri, :final configurations) => [
    uri,
    for (final configuration in configurations) configuration.uri,
  ],
  PartDirective(:final uri) => [uri],
  _ => const [],
};

Reference _reference(
  StringLiteral uri,
  LineInfo lines,
  String file,
  Package package,
) {
  // An interpolated URI has no value: it is reported as written and names no
  // file.
  final value = uri.stringValue;
  return (
    specifier: value ?? uri.toSource(),
    line: lines.getLocation(uri.offset).lineNumber,
    resolved: value == null ? null : resolve(value, file, package),
  );
}

/// The absolute path a reference from [file] names, or null outside [package].
String? resolve(String specifier, String file, Package package) {
  final uri = Uri.tryParse(specifier);
  if (uri == null) {
    return null;
  }
  if (uri.isScheme('package')) {
    return _packagePath(uri, package);
  }
  if (uri.hasScheme) {
    return null;
  }
  return Uri.file(file).resolveUri(uri).toFilePath();
}

String? _packagePath(Uri uri, Package package) {
  final segments = uri.pathSegments;
  if (segments.isEmpty || segments.first != package.name) {
    return null;
  }
  final library = Uri.directory(package.root).resolve('lib/');
  final path = library.resolveUri(Uri(pathSegments: segments.skip(1)));
  final inside = path.path.startsWith(library.path) && path != library;
  return inside ? path.toFilePath() : null;
}
