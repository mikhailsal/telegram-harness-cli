# 0007 Common gates

Status: accepted (P03); amended in P06 and P08.

## Scope and order

`quality check` materializes the candidate
([decision 0004](0004-candidate-snapshots.md)), validates the policy, builds
the inventory ([decision 0006](0006-discovery-and-classification.md)), and
then checks every candidate file, not only the changed ones:

1. files: blob size, source file length, UTF-8 encoding;
2. configuration syntax;
3. inline suppression markers;
4. documentation rules;
5. Markdown structure lint (pymarkdown);
6. secret scan (gitleaks) of the snapshot;
7. profile gates of each active component. Python, TypeScript, Dart, and
   PHP components get structure metrics and module architecture.

Exceptions are applied last. Rules that need the network never run here:
external links have their own command, and dependency audits run in merge CI
(P10).

## Category policy

| Rule | Source | Tests | Docs | Config | Generated, vendor, asset |
| --- | --- | --- | --- | --- | --- |
| `files.blob-size` | yes | yes | yes | yes | yes |
| `files.source-lines` | yes | yes | no | no | no |
| `files.encoding` | yes | yes | yes | yes | no |
| `config.syntax` | no | no | no | yes | no |
| `suppressions.inline` tool markers | yes | yes | Markdown lint markers | yes | no |
| `suppressions.inline` scanner marker | yes | yes | yes | yes | yes |
| `docs.*`, `markdown.lint` | no | no | yes | no | no |
| `secrets.detected` | yes | yes | yes | yes | yes |
| `structure.*` | `.py` | `.py` | no | no | no |

Type declaration stubs (`.pyi`) have no bodies and are not measured.
Configuration syntax covers JSON (duplicate keys are errors), TOML, and YAML.
JSONC files (`tsconfig*.json`, `jsconfig*.json`, `.eslintrc.json`, and files
under `.vscode/` or `.devcontainer/`) are left to their native tools.

## Python counting rules

The source of truth is `tools/quality/src/quality/adapters/python/structure.py`,
which uses only the standard `ast` and `tokenize` modules.

- Functions are `def` and `async def` at any depth: methods, nested
  functions, and functions in compound statements. Each one is measured on its
  own. A nested function's lines also count towards the enclosing body.
  Lambdas belong to the enclosing function.
- Code lines are the physical lines of the body that hold a token other than
  comments, blank lines, and indentation. The function's docstring does not
  count; a multi-line expression counts every line it spans.
- Cyclomatic complexity is 1 plus one for each `if`, `elif`, conditional
  expression, `for`, `async for`, `while`, `except` clause, and comprehension
  `for` and `if`. A boolean operator chain adds one per extra operand
  (`a and b or c` adds 2). Each `match` case adds one, except an irrefutable
  final case (`case _` or a bare capture without a guard). `try`, `else`,
  `finally`, `with`, `assert`, and `return` add nothing. This is cyclomatic,
  not cognitive, complexity: nesting does not change it.
- Nesting depth is the deepest chain of `if`, `for`, `while`, `with`, `try`,
  and `match` blocks inside the body; an `elif` chain stays at one level and
  the body of a nested function starts at zero.
- Parameters count positional-only, ordinary, keyword-only, `*args`, and
  `**kwargs`. The receiver (`self`, `cls`) of a method defined directly in a
  class is excluded unless the method is a `staticmethod`.
- File length counts physical lines, and a last line without a newline still
  counts. The formatter check ([decision 0009](0009-python-profile.md)) makes
  the staged file canonical, so the count is taken after canonical
  formatting.

The TypeScript helper implements the same definitions with the TypeScript
parser ([decision 0011](0011-typescript-profile.md#structure-and-architecture));
the Dart helper with package:analyzer
([decision 0012](0012-dart-profile.md#structure-and-architecture)); the PHP
helper with nikic/php-parser
([decision 0013](0013-php-profile.md#structure-and-architecture)).

## Module architecture

A component may declare modules (path globs relative to its root), an ordered
`layers` list (a module may depend only on modules listed after it), and
`forbidden` edges. Imports are resolved from the component's source roots,
including relative imports. Edges into undeclared files are ignored. A file
that matches two modules fails, and so does any cycle between declared
modules. The core declares its own layers in `quality.toml`.

## Documentation

- Page limits apply to documentation pages (`.md`, `.markdown`, `.rst`,
  `.adoc`, `.txt`), except legal notices (`LICENSE`, `COPYING`, `NOTICE`). A
  page fails when it exceeds either the line limit or the prose word limit.
  Prose excludes code blocks and HTML blocks. Root `AGENTS.md` and `CLAUDE.md`
  use the agent limits, and a missing `AGENTS.md` fails.
- Internal links and anchors resolve against candidate paths, case-sensitively.
  Anchors follow GitHub's heading slugs plus explicit `id` and `name`
  attributes. Line anchors (`#L10`) are accepted on non-Markdown files.
- Every Markdown page outside hidden directories must be reachable through
  links from the root `README.md`, `AGENTS.md`, or `CLAUDE.md`.
- Commands written as `quality <command> [<action>]` in code spans and shell
  blocks must exist. Placeholders such as `<command>` are skipped.
- Language is advisory: a prose block with at least 40 letters, under half of
  them Latin, gives a `docs.non-english` warning, at most one per file.
  Non-ASCII text is never an error.
- `quality links` probes external http(s) links with a per-request timeout,
  outside `quality check`. It contacts only hosts whose every address is
  public, connects only to those validated addresses (in resolver order,
  falling through unreachable ones), and reads no response body.
  HTTP 404 and 410 fail; timeouts, refusals, and server errors are warnings.

## Secret scanning

gitleaks scans the snapshot directory with the upstream rule set from
`toolchains.toml`, which is pinned by commit and checksum. At run time the
global allowlist of paths is removed, so dependency directories and lockfiles
are scanned too. The core checks that nothing else in the rules changed. The
candidate cannot change the scan:

- its `.gitleaks.toml` is overridden by `--config`;
- inline allow markers are ignored (`--ignore-gitleaks-allow`), so a marked
  secret is still reported;
- a root `.gitleaksignore` is still read by gitleaks, but its fingerprints
  name files by their snapshot path, a fresh temporary directory, so they
  cannot match; nested ignore files are not read;
- tools run with an allowlisted environment, so `GITLEAKS_*` variables do not
  reach them, and `.gitignore` does not apply in directory mode.

Other gitleaks runs honor allow markers and ignore files, so each marker and
each `.gitleaksignore` entry is also reported as a suppression and needs a
registered exception. Findings are redacted. Binary files are scanned as
bytes, and archives are scanned up to three levels deep.

History checks (`check_history`, wired into the merge gate in P10) cover
every blob that `base..tip` adds, including blobs deleted again before the
tip. They apply the blob size limit and the same secret scan.

## Assets and Git LFS

Assets are size-limited and secret-scanned as bytes; their content is
otherwise not interpreted (no image or document parsing). For LFS files,
only the pointer text is checked; the large object itself is never fetched
or inspected.

## Exceptions and inline suppressions

Every inline marker that turns a check off is a `suppressions.inline`
finding. These are Ruff, Pyright, mypy, coverage, and formatter markers in
Python; ESLint, TypeScript, and coverage markers in TypeScript and
JavaScript; analyzer and coverage markers in Dart; and PHPStan, Psalm, and
PHPUnit markers in PHP. The symbol is the marker text from the marker to the
end of the comment, with whitespace collapsed. Python comments are found with
the tokenizer, and a file that cannot be tokenized is scanned line by line.

An exception in `quality.toml` names the rule, the exact path, and an
optional symbol, and it matches only that combination. A reason and owner
are required, plus `expires` for a temporary exception or `review` for a
permanent one.

- A past `expires` date fails, and the exception no longer applies.
- A past `review` date warns, and the exception still applies.
- An exception that matches nothing fails when its rule ran over the whole
  candidate.
- Rules about the policy, Git, discovery, secrets, exceptions, tests,
  coverage, scenarios, and quarantine cannot be excepted, and neither can
  environment or usage errors. A flaky test is quarantined instead
  ([decision 0014](0014-behavioral-scenarios.md#quarantine)).

A generated or vendor classification is not an exemption: such files keep
the blob limit and the secret scan, and a generated marker alone changes
nothing without an explicit classification.
