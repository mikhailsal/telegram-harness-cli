# Extending the gate

A new language profile or coverage format is a change to the quality core,
made like any other change: in a branch, with tests, through the hooks, and
reviewed by the core's code owners. Adding one never needs a switch that
turns a gate off. Until a profile exists, a source file of its language
fails with `discovery.unsupported-language`, so nothing passes by omission.
Make the change in the template's own repository rather than in one
project, so every project receives it with its next template update
([maintenance guide](maintenance.md)). The architecture is in
[decision 0003](../decisions/0003-architecture.md), and the existing
profiles are listed in the [profile support matrix](../reference/profiles.md).

## A new language profile

Each step names the module that owns it under `tools/quality/src/quality`.

1. **The profile name.** Add a member to `Profile` in `policy/enums.py`, then
   regenerate the schemas with `./quality policy schema --write`.
2. **Files and manifests.** Map the language's extensions in
   `discovery/filetypes.py` and list them in `PROFILE_LANGUAGES`. Register
   the manifest parser in `PARSERS` of `discovery/manifests.py`; a parser
   reads the manifest as data and never executes it.
3. **Conventions.** Add the conventional source and unit test directories to
   `CONVENTIONS` in `cli/component_cmd.py`, so `component add` detects the
   profile.
4. **The adapter.** Create `adapters/<language>/` with a class that
   implements `ProfileAdapter` from `engine/profiles.py`:
   - `prepare` checks the component's native configuration and builds its
     locked environment offline inside the snapshot's work area;
   - `static` runs the formatter check, linter, and type checker, and
     returns their findings under the profile's own `rules`;
   - `measure` returns structure metrics and imports from the language's
     own parser, or `None` when the common gates measure the files;
   - `run` executes one suite with branch coverage and returns its parsed
     test results, and `coverage` parses the coverage report;
   - `expected` says which source files must appear in that report.
5. **Setup and fixes.** Implement `ProfileSetup` (download in `provision`,
   verify offline in `probe`) and `ProfileFixer`, then register all three in
   `local_profiles`, `local_setups`, and `local_fixers` of `cli/local.py`.
6. **Rules.** Give every finding a rule with an explanation and a remedy,
   and add the rules to the catalog in `catalog.py`, so `./quality explain`
   knows them.
7. **Tools.** Pin every standalone tool in `tools/quality/toolchains.toml`
   with its vendor checksum file in `checksums`, and every package the
   component must lock under `[packages.<manager>]`
   ([maintenance guide](maintenance.md)).
8. **A helper, when the language's parser is not Python.** Put it in
   `tools/quality/helpers/<language>` as a `helper` component with its own
   lockfile and tests; it stays dormant in projects without that profile.

Write a decision record for the profile, like
[decision 0013](../decisions/0013-php-profile.md), and add it to the
documentation index and the support matrix.

## A new coverage format

Coverage parsers live in `coverage/` and return the normalized
`CoverageReport` of `coverage/model.py`: per file, the covered and missed
lines and branches. A parser must reject an empty, truncated, or
unrecognized report with `ReportError` rather than return an empty report,
and must refuse paths outside the snapshot. Read reports with the bounded
readers of `boundedio.py`. The adapter's `coverage` method calls the parser;
the coverage rules then apply the floors of the policy unchanged.

## Contract tests

`tools/quality/tests/unit/test_profile_contract.py` runs the same tests for
every member of `Profile`. A new profile fails them until:

- every stage above knows it;
- its rules are registered and explained;
- its adapter expects coverage of its own source files only;
- an empty or malformed coverage report never passes;
- source of another or an unsupported language in its component fails
  classification.

Add the profile's sample source path to the contract's `SOURCES` table, then
add the profile's own unit tests beside the existing ones, and acceptance
tests that run a scratch project of the language through real hooks.
Coverage of the core stays at the policy minimum, as for any other change.
