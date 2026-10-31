import pytest

from quality.diagnostics import Severity
from quality.policy import Category
from quality.rules.docs import check_docs
from quality.rules.model import CandidateFile
from quality.testkit.candidates import candidate
from quality.testkit.policies import build_policy

DEFAULTS = build_policy().thresholds
CYRILLIC_40 = "ж" * 40


def doc(path: str, text: str) -> CandidateFile:
    return candidate(path, text, Category.DOCS)


def agents(*pages: str) -> CandidateFile:
    return doc("AGENTS.md", "".join(f"[p]({page})\n" for page in pages))


def run(*files: CandidateFile) -> list[tuple[str, str, str]]:
    return [
        (d.rule, d.message, d.location.render() if d.location else "")
        for d in check_docs(files, DEFAULTS, {})
    ]


@pytest.mark.parametrize(
    ("text", "warns"),
    [
        (CYRILLIC_40, True),
        ("ж" * 39 + " 123 ...", False),
        ("a" * 20 + " " + "ж" * 20, False),
        ("a" * 19 + " " + "ж" * 21, True),
        (
            "这是一个用中文写的段落\uff0c它包含足够多的汉字来触发语言检测的建议性警告信息内容。再写几个字确保超过四十个字母。",
            True,
        ),
        ("Ceci est une phrase française avec des accents: été, déjà, garçon, où, très.", False),
        ("Die Größe der Übersicht hängt von der Länge ab; Straße, Bücher, schön.", False),
        ("The Russian word «спасибо» means thanks; keep such quotes short in docs.", False),
        ("Ελληνικά", False),
    ],
)
def test_non_latin_prose_is_an_advisory_warning(text: str, warns: bool) -> None:
    files = [agents("doc.md"), doc("doc.md", f"# Title\n\n{text}\n")]
    findings = [
        (d.rule, d.severity, d.location.render() if d.location else "")
        for d in check_docs(files, DEFAULTS, {})
    ]
    assert findings == ([("docs.non-english", Severity.WARNING, "doc.md:3")] if warns else [])


def test_non_english_is_reported_once_per_file() -> None:
    text = f"# Title\n\n{CYRILLIC_40}\n\n{CYRILLIC_40}\n\n```text\n{CYRILLIC_40}\n```\n"
    assert run(agents("doc.md"), doc("doc.md", text)) == [
        (
            "docs.non-english",
            "prose is mostly in a non-Latin script; documentation is in English",
            "doc.md:3",
        )
    ]


def test_code_is_not_language_checked() -> None:
    text = f"# Title\n\n`{CYRILLIC_40}`\n\n```sh\necho {CYRILLIC_40}\n```\n"
    assert run(agents("doc.md"), doc("doc.md", text)) == []
