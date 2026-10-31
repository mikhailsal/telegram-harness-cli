# Reference browser application

A small React + Vite application, checked by the quality gate as the
`example-web` component in [`quality.toml`](../../quality.toml). It shows what
a browser component of this template proves
([decision 0018](../../docs/decisions/0018-browser-builds-and-scenarios.md)):

- two pages and a not-found page with client-side navigation, so deep links
  and the browser's history work;
- a feedback form whose problems are named by each field's accessible
  description and take the focus, operable from the keyboard alone;
- CSS Modules, an imported image asset, and a public icon;
- the loading, success, and error states of a request to `/api/feedback`.

There is no backend. The unit tests in `tests/` stub `fetch`; the Playwright
scenarios in `e2e/` answer the request with a route in the browser, the
boundary that [`scenarios.toml`](scenarios.toml) names. They prove nothing
about a real API.

## Commands

From the repository root, after `./quality bootstrap`:

```sh
./quality test --component example-web          # unit suites with coverage
./quality check --stage merge --all              # also the build and scenarios
```

The merge stage builds the candidate for production, serves the build itself,
and runs every scenario in the pinned Chromium at the `narrow` and `wide`
viewports, with accessibility scans of the form's states.

To work on the application with the development server of the locked Vite:

```sh
cd examples/web
npm ci
npm run dev
```

The example role keeps the component dormant in a project made from the
template until the project changes it or runs in maintainer mode.
