# 0002 Installation strategy

Status: accepted (P00); amended in P03, P05, P06, P08, P10, P11, and F02.

Commands that are not implemented yet are named in plain text with the phase
that delivers them; `quality check` fails documentation that presents a
missing command as runnable.

## Prerequisites (documented, not promised away)

- Linux on x86_64 or aarch64 with glibc.
- Git 2.34 or newer (Ubuntu 22.04); `quality doctor` reports an older Git as
  `git.version`. P05 lowered the minimum from 2.40 after the core proved to
  need no newer plumbing ([decision 0006](0006-discovery-and-classification.md#git-version)).
- `python3` 3.10 or newer from the distribution. It runs only the stdlib
  launcher `./quality`; the core itself runs on the pinned CPython 3.14.8.
- Network access while bootstrapping (`./quality bootstrap`, or
  `quality tools install` for the standalone tools), while exporting a
  project from a fresh template clone, and while updating pins with
  `./quality tools update`.
- For the PHP profile only: PHP 8.4 CLI with the `dom`, `mbstring`, `xml`,
  `xmlwriter`, and `tokenizer` extensions, the `zip` extension or the `unzip`
  command for Composer's archives, plus Xdebug 3.5 installed but not
  necessarily enabled (Debian/Ubuntu: `php8.4-cli php8.4-xml php8.4-mbstring
  php8.4-zip php8.4-xdebug`, from the distribution or the ondrej PPA). The
  gate loads Xdebug itself when the system does not; PCOV and phpdbg cannot
  replace it. The core's own acceptance suite also needs PCOV
  (`php8.4-pcov`) to prove that a machine with only PCOV cannot pass
  ([decision 0013](0013-php-profile.md#runtime)).
- For browser components only: the shared libraries that the pinned
  Chromium headless shell links, among them NSS and NSPR, GLib, ATK and
  AT-SPI, D-Bus, ALSA, GBM, expat, udev, xkbcommon, and the X11 client
  libraries (Debian/Ubuntu: `libnss3 libglib2.0-0 libatk-bridge2.0-0
  libasound2 libgbm1 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2`,
  with the `t64` names on Ubuntu 24.04). The `deb.deps` and `rpm.deps` files
  beside the installed browser list the packages, and bootstrap and doctor
  name the libraries the loader cannot find
  ([decision 0017](0017-browser-frontend-profile.md#chromium)).

## Managed downloads

`./quality bootstrap` downloads pinned artifacts into a declared cache
(`$XDG_CACHE_HOME/quality`, default `~/.cache/quality`) and verifies each
SHA-256 against `tools/quality/toolchains.toml` before extraction. Since P03,
`quality tools install` performs the download and verification for the
standalone tools:

- always: uv, then the uv-managed CPython 3.14.8 and the locked core
  environment, gitleaks;
- TypeScript profile active: Node.js (npm is bundled), installed by
  `quality bootstrap` only when a TypeScript component exists, or explicitly
  with `quality tools install node` (since P06);
- Dart profile active: the Dart SDK, installed by `quality bootstrap` only
  when a Dart component exists, or explicitly with `quality tools install
  dart` (since P07);
- PHP profile active: `composer.phar`, installed by `quality bootstrap` only
  when a PHP component exists, or explicitly with `quality tools install
  composer` (since P08);
- browser component active: the Chrome for Testing headless shell, installed
  by `quality bootstrap` only when an active TypeScript component has the
  browser target, or explicitly with `quality tools install chromium`
  (since F02);
- always since P10: actionlint, because every check lints the workflows, and
  osv-scanner, because the core's own tests run it and merge CI runs
  `quality audit`
  ([decision 0015](0015-ci-wrappers-and-merge-gates.md#dependency-gates)).

Since P11 bootstrap sets up only the components that a check would activate.
A helper bundle whose profile no application uses is dormant, so a Python
project downloads none of Node.js, the Dart SDK, and Composer, until the
candidate changes the helper itself
([decision 0003](0003-architecture.md#roles-and-activation)). uv installs
CPython with `--no-bin`, so nothing is added to the user's `~/.local/bin`.

The PHP runtime is the one toolchain not downloaded: portable PHP builds with
Xdebug are not a maintained upstream artifact. Its minor version and the
Xdebug minor version are pinned under `[runtimes]` in
`tools/quality/toolchains.toml` and verified by `quality bootstrap`,
`quality doctor`, and every check that runs a PHP tool. This is the honest
gap: patch versions of PHP and Xdebug follow the system packages.

## Project dependencies

- Python components: `uv sync --frozen` from each component's `uv.lock`.
- TypeScript components: `npm ci --ignore-scripts` from `package-lock.json`,
  with the shared npm cache `$XDG_CACHE_HOME/quality/npm`
  ([decision 0011](0011-typescript-profile.md#environments)).
- Dart components: `dart pub get --enforce-lockfile` from `pubspec.lock`,
  with the shared pub cache `$XDG_CACHE_HOME/quality/pub`
  ([decision 0012](0012-dart-profile.md#environments)).
- PHP components: `composer install --no-scripts --no-plugins` from
  `composer.lock`, with the shared Composer cache
  `$XDG_CACHE_HOME/quality/composer`
  ([decision 0013](0013-php-profile.md#environments)).

Bootstrap fills the package caches. Ordinary checks run the same commands in
offline mode (`--offline`, `COMPOSER_DISABLE_NETWORK=1`), so a check never
fetches floating tools or rules; a cache miss is an actionable error that
names the install command.

## Clean-environment proof

In P11 a clone of the template ran in the container image
`ubuntu@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac`
with only Git 2.43.0, Python 3.12.3, and CA certificates installed, as an
unprivileged user with a Git identity and an empty home. There:

- `./quality template export ../my-project` downloaded uv, CPython 3.14.8,
  and the core's packages and committed the export in 48 seconds;
- in the export, the first `./quality bootstrap` installed gitleaks,
  actionlint, and osv-scanner and left the three helpers dormant; the second
  installed nothing, and `./quality doctor` passed;
- the cache held no profile toolchain, and `~/.local/bin` did not exist;
- `./quality check --all` passed, and a documentation commit passed through
  the hook.

Docker refuses unprivileged user namespaces, so the unit suites ran with the
network and the check warned with `tests.network`
([decision 0008](0008-test-and-coverage-engine.md#network-isolation)).

## Updates

`./quality tools update NAME VERSION` (since P11) rewrites one tool's pins
and checksums in `tools/quality/toolchains.toml` from the vendor's checksum
file, producing a reviewable diff
([maintenance guide](../guides/maintenance.md#pinned-tools)). It never runs
during checks.
