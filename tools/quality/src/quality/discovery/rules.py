"""Rules reported by discovery and classification."""

from __future__ import annotations

from quality.diagnostics import Rule, Severity

UNCLASSIFIED = Rule(
    id="discovery.unclassified",
    title="File type is unknown",
    requirement="R03",
    explanation=(
        "Every candidate file gets a category. A file whose type cannot be detected from "
        "its name, extension, or shebang is not assumed to be harmless."
    ),
    remedy="Add a narrow [[classification]] entry with a category and a reason.",
)
UNSUPPORTED_LANGUAGE = Rule(
    id="discovery.unsupported-language",
    title="Code in a language without a supported profile",
    requirement="R03",
    explanation=(
        "Only Python, TypeScript/JavaScript on Node, Dart, and PHP code can be validated. "
        "Other code would pass without any check."
    ),
    remedy="Rewrite it in a supported language, remove it, or classify vendored code.",
)
UNOWNED_SOURCE = Rule(
    id="discovery.unowned-source",
    title="Code outside declared source and test roots",
    requirement="R03",
    explanation=(
        "Source files must belong to a component's declared source or test roots so that "
        "their gates apply. Code elsewhere is ambiguous."
    ),
    remedy="Move the file into a declared root or add the path to a component's roots.",
)
LANGUAGE_MISMATCH = Rule(
    id="discovery.language-mismatch",
    title="Source file does not match the component profile",
    requirement="R03",
    explanation="A component validates one profile; other languages in its roots go unchecked.",
    remedy="Move the file to a component of the matching profile.",
)
CLASSIFICATION_CONFLICT = Rule(
    id="discovery.classification-conflict",
    title="Several classification patterns match one file",
    requirement="R03",
    explanation="Each file must match at most one [[classification]] pattern.",
    remedy="Narrow the patterns so that each file matches only one.",
)
CLASSIFICATION_RELABEL = Rule(
    id="discovery.classification-relabel",
    title="Classification relabels source or test files",
    requirement="R03",
    explanation=(
        "Files inside declared source roots keep the source category; only generated files "
        "that carry a generated marker may be classified there. Classification cannot turn "
        "test or source files into source."
    ),
    remedy="Remove the classification or add the generator's marker to the file header.",
)
UNUSED_CLASSIFICATION = Rule(
    id="discovery.unused-classification",
    title="Classification pattern matches no file",
    requirement="R03",
    explanation="Stale classification entries hide future files from their real category.",
    remedy="Remove the entry or correct its pattern.",
    severity=Severity.WARNING,
)
MANIFEST_OUTSIDE_COMPONENT = Rule(
    id="discovery.manifest-outside-component",
    title="Package manifest outside declared components",
    requirement="R03",
    explanation=(
        "A manifest that declares a package must sit at the root of a declared component, "
        "unless it only declares a workspace. Otherwise its code would have no gate."
    ),
    remedy="Declare a component with this root in quality.toml.",
)
PROFILE_MISMATCH = Rule(
    id="discovery.profile-mismatch",
    title="Manifest does not match the component profile",
    requirement="R03",
    explanation="A component's root manifest must belong to the component's profile.",
    remedy="Correct the component profile or move the manifest.",
)
MANIFEST_INVALID = Rule(
    id="discovery.manifest-invalid",
    title="Package manifest cannot be parsed",
    requirement="R03",
    explanation="Manifests are parsed as data to find packages and workspaces.",
    remedy="Fix the manifest syntax.",
)
MISSING_ROOT = Rule(
    id="discovery.missing-root",
    title="Declared root has no files in the candidate",
    requirement="R03",
    explanation="A declared component, source, or test root without tracked files is stale.",
    remedy="Add the files to the candidate or remove the declaration.",
)
SUBMODULE = Rule(
    id="discovery.submodule-unsupported",
    title="Submodules are not validated",
    requirement="R03",
    explanation=(
        "Submodule content is not part of the candidate tree. A submodule is accepted only "
        "when it is classified as vendor content, and it is then materialized empty."
    ),
    remedy="Vendor the code, remove the submodule, or classify it as vendor with a reason.",
)
RULES = (
    UNCLASSIFIED,
    UNSUPPORTED_LANGUAGE,
    UNOWNED_SOURCE,
    LANGUAGE_MISMATCH,
    CLASSIFICATION_CONFLICT,
    CLASSIFICATION_RELABEL,
    UNUSED_CLASSIFICATION,
    MANIFEST_OUTSIDE_COMPONENT,
    PROFILE_MISMATCH,
    MANIFEST_INVALID,
    MISSING_ROOT,
    SUBMODULE,
)
