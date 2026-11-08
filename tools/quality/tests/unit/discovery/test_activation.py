from quality.discovery import active_components, is_active
from quality.testkit.policies import ComponentSpec, build_policy

APP = ComponentSpec(id="app", root="apps/api")
TOOL = ComponentSpec(id="tool", root="tools/quality", role="tooling")
TS_HELPER = ComponentSpec(id="ts-helper", root="helpers/ts", profile="typescript", role="helper")
PY_HELPER = ComponentSpec(id="py-helper", root="helpers/py", role="helper")


def ids(*specs: ComponentSpec, mode: str = "project", changed: tuple[str, ...] = ()) -> list[str]:
    return [c.id for c in active_components(build_policy(*specs, mode=mode), changed)]


def test_application_and_tooling_components_always_run() -> None:
    assert ids(APP, TOOL) == ["app", "tool"]


def test_helpers_of_an_application_profile_run() -> None:
    assert ids(APP, PY_HELPER, TS_HELPER) == ["app", "py-helper"]


def test_dormant_helpers_wake_when_their_files_change() -> None:
    changed = ("helpers/ts/src/index.ts", "README.md")
    assert ids(APP, TS_HELPER, changed=changed) == ["app", "ts-helper"]
    assert ids(APP, TS_HELPER, changed=("helpers/tsx/a.ts",)) == ["app"]


def test_maintainer_mode_runs_every_helper() -> None:
    assert ids(APP, TS_HELPER, mode="maintainer") == ["app", "ts-helper"]


def test_is_active_defaults_to_no_changes() -> None:
    policy = build_policy(APP, TS_HELPER)
    assert not is_active(policy, policy.components[1])


EXAMPLE = ComponentSpec(id="example", root="examples/web", profile="typescript", role="example")
WEB = ComponentSpec(id="web", root="apps/web", profile="typescript", capabilities=("ui",))


def test_a_shipped_example_never_wakes_because_of_another_component() -> None:
    assert ids(WEB, TS_HELPER, EXAMPLE) == ["web", "ts-helper"]
    assert ids(APP, EXAMPLE, changed=("apps/api/src/app.py", "helpers/ts/a.ts")) == ["app"]


def test_a_shipped_example_runs_when_it_changes_or_in_maintainer_mode() -> None:
    assert ids(APP, EXAMPLE, changed=("examples/web/src/App.tsx",)) == ["app", "example"]
    assert ids(APP, EXAMPLE, changed=("examples/website/a.ts",)) == ["app"]
    assert ids(APP, EXAMPLE, mode="maintainer") == ["app", "example"]
