# Maintenance

Every update to the gate is an ordinary reviewed change: a diff in a branch
that the hooks check and the code owners approve. Nothing updates itself,
and no update needs the network once its files are committed, apart from
`./quality bootstrap` filling the caches.

## Pinned tools

The standalone tools, Node, the Dart SDK, Composer, and Chromium are pinned in
[`toolchains.toml`](../../tools/quality/toolchains.toml) with a URL and a
SHA-256 checksum per architecture
([decision 0001](../decisions/0001-toolchain-pins.md)). To move one to
another release:

```sh
./quality tools update gitleaks 8.31.0
./quality tools install gitleaks
./quality check --all
git diff tools/quality/toolchains.toml
```

`tools update` derives the new URLs and archive members by replacing the
version, takes their checksums from the vendor's checksum file that the
tool's `checksums` entry names, and downloads and hashes the tool's extra
files. It rewrites only that tool's lines, keeps comments and order, and
refuses with `tools.update-invalid` when a URL does not contain the version
or the result would change anything else. It never installs or stages
anything: install, check, review the diff, then stage and commit it.

A `checksums` entry is an HTTPS URL, or `{url}` followed by a suffix for a
checksum file beside each download; `{version}` stands for the new version.
When a vendor changes its file names, edit the tool by hand with the
checksums from the vendor's release page.

Chromium has no vendor checksum file, so `tools update` refuses it. Move it
together with `@playwright/test`: take the headless shell version from the
`chromium-headless-shell` entry of the new playwright-core's `browsers.json`,
edit the version, URLs, and members by hand, and pin the SHA-256 of each
download after its MD5 matches the `x-goog-hash` header of the bucket.
Bootstrap and doctor fail with `typescript.browser-version` until the
browser and the locked Playwright agree
([decision 0017](../decisions/0017-browser-frontend-profile.md#chromium)).

## uv and CPython

The pinned uv version is also the `required-version` under `[tool.uv]` of
`tools/quality/pyproject.toml` and `tools/quality/launcher/pyproject.toml`;
a unit test fails until all three agree. CPython is pinned by the
`.python-version` files of the core and the launcher. After changing either,
run `./quality bootstrap`, which installs the new versions beside the old
ones.

## Locked packages

- **The core's Python packages** (Ruff, Pyright, pytest, pydantic, and the
  rest) are locked in `tools/quality/uv.lock`, and the launcher's in
  `tools/quality/launcher/uv.lock`. Change the version in `pyproject.toml`,
  run `uv lock` with the pinned uv from that directory, then
  `./quality bootstrap`.
- **Profile packages** that every component must lock are pinned under
  `[packages.npm]`, `[packages.pub]`, and `[packages.composer]` of
  `toolchains.toml`; browser components also lock `[packages.npm-browser]`.
  Change the pin, update each component's own lockfile
  with its package manager (`npm install`, `dart pub upgrade`,
  `composer update`), and update the helper bundles in
  `tools/quality/helpers` and the reference application in `examples/web`
  the same way. A lockfile that disagrees with the pin fails the profile's
  configuration check. A new `@playwright/test` pin moves Chromium with it,
  as described under [Pinned tools](#pinned-tools).
- **System runtimes**: `[runtimes]` names the PHP and Xdebug minor versions
  the PHP profile accepts.

`./quality audit` matches every lockfile against the OSV advisories; run it
after each package update.

## Policy

Thresholds, components, classification, and exceptions change only in
[`quality.toml`](../../quality.toml). Validate an edit with
`./quality policy validate`, and regenerate the schemas with
`./quality policy schema --write` after a change to the policy models. A
change to the policy affects every component, so the next check runs every
active component's suites. Tightening a threshold may surface findings in
untouched files; fix them in the same change or declare reviewed
[exceptions](exceptions.md).

## Updating a project from a newer template

A project's root commit says which template commit it came from, in its
`Quality-Template` trailer; every later template update records the same
trailer. Find the last one with:

```sh
git log --grep=Quality-Template --format=%B
```

In a clone of the template, export both that commit and the new one, then
apply the difference between them to the project as one reviewable change:

```sh
./quality template export --rev OLD ../template-old
./quality template export ../template-new
cd ../my-project
git fetch ../template-old main:template-old
git fetch ../template-new main:template-new
git diff --binary template-old template-new | git apply --3way
```

`git apply --3way` stages the changes it applies cleanly and leaves conflict
markers where the project changed the same lines; resolve them, run
`./quality bootstrap` and `./quality check --all`, and commit with a
`Quality-Template` trailer that names the new template commit. Delete the
two fetched branches afterwards. Because the difference comes from two
exports, maintainer-only content never reaches the project.
