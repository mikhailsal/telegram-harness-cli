import json
from pathlib import Path

from quality.adapters.typescript.buildconfig import EXTERNAL, IMPORTS, vite_config, wrapper


def settings_of(text: str) -> dict[str, object]:
    start = text.index("const settings = ") + len("const settings = ")
    return json.loads(text[start : text.index(";\n\nconst manifests")])


def test_the_configuration_is_found_in_vite_order(tmp_path: Path) -> None:
    assert vite_config(tmp_path) is None
    (tmp_path / "vite.config.ts").write_text("")
    assert vite_config(tmp_path) == tmp_path / "vite.config.ts"
    (tmp_path / "vite.config.mjs").write_text("")
    assert vite_config(tmp_path) == tmp_path / "vite.config.mjs"
    # Vitest's own configuration does not configure the build.
    (tmp_path / "vitest.config.js").write_text("")
    assert vite_config(tmp_path) == tmp_path / "vite.config.mjs"


def test_the_wrapper_imports_the_component_configuration(tmp_path: Path) -> None:
    (tmp_path / "vite.config.ts").write_text("")
    run = tmp_path / "run"
    text = wrapper(tmp_path, run, servers=[tmp_path / "b.ts", tmp_path / "a.ts"], packages=["pg"])
    assert text.startswith(IMPORTS)
    assert f"import * as loaded from {json.dumps(str(tmp_path / 'vite.config.ts'))};" in text
    assert f"const EXTERNAL = {json.dumps(EXTERNAL)};" in text
    assert settings_of(text) == {
        "outDir": str(run / "dist"),
        "envDir": str(run / "env"),
        "report": str(run / "boundaries.json"),
        "serverModules": [str(tmp_path / "a.ts"), str(tmp_path / "b.ts")],
        "serverPackages": ["pg"],
    }
    for override in ("envDir: settings.envDir", "emptyOutDir: true", "manifest: true"):
        assert override in text


def test_a_component_without_configuration_builds_with_the_defaults(tmp_path: Path) -> None:
    text = wrapper(tmp_path, tmp_path / "run", servers=[], packages=[])
    assert "const loaded = {};" in text
    assert "import * as loaded" not in text
    assert settings_of(text)["serverModules"] == []
