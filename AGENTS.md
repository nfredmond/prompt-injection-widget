# Repository Guidelines

## Project Structure & Module Organization

This repository is currently a fresh project directory with no committed source tree yet. As implementation is added, keep the layout predictable:

- `src/` for application and widget source code.
- `tests/` for unit and integration tests.
- `assets/` for static files such as images, icons, and sample prompts.
- `docs/` for design notes, threat-model notes, and contributor documentation.

Keep feature code grouped by responsibility. Place prompt parsing, UI rendering, and security policy logic in separate modules.

## Build, Test, and Development Commands

No package manifest or build system is present yet. After adding one, document the canonical commands here. Recommended examples:

- `npm install` installs JavaScript dependencies.
- `npm run dev` starts a local development server.
- `npm run build` creates a production build.
- `npm test` runs the test suite.
- `npm run lint` checks formatting and static analysis.

If a different toolchain is chosen, such as `pnpm`, `uv`, or `make`, update this section in the same change.

## Coding Style & Naming Conventions

Prefer small, explicit modules with clear names, such as `promptPolicy.ts`, `InjectionWidget.tsx`, or `sanitizePrompt.test.ts`.

For JavaScript or TypeScript, use 2-space indentation, `camelCase` for functions and variables, `PascalCase` for components and classes, and `SCREAMING_SNAKE_CASE` for constants. Prefer structured parsing and escaping helpers over ad hoc string manipulation, especially for prompt or HTML content.

## Testing Guidelines

Add tests alongside new behavior. Use `*.test.*` or `*.spec.*` naming. Include both expected-use cases and adversarial prompt-injection cases when touching security-sensitive logic.

When a test framework is introduced, document the exact command and any coverage target here. Do not rely only on manual browser checks for core logic.

## Commit & Pull Request Guidelines

There is no Git history in this directory yet, so no existing convention can be inferred. Use concise, imperative commit messages, for example `Add prompt policy validator` or `Fix widget focus handling`.

Pull requests should include a short summary, validation performed, and screenshots or recordings for visible UI changes. Link related issues when available. Call out security implications for changes to prompt handling, sanitization, rendering, or configuration.

## Security & Configuration Tips

Treat user-provided prompts and rendered model output as untrusted input. Avoid committing secrets, API keys, credentials, or local environment files. Keep configuration examples in templates such as `.env.example`, and document required variables without real values.
