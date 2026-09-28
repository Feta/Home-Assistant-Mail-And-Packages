# Contribution guidelines

Contributions to this fork are welcome, including bug fixes, tests, shipper updates, documentation, and improvements to the Super Tracking pipeline.

## Development flow

1. Create a short-lived branch from `master`.
2. Keep changes focused and update documentation when behavior changes.
3. Run `pre-commit run --all-files`.
4. Run the relevant tests locally; `tox` matches the CI test workflow.
5. Open a pull request back to `master`.

CI runs pre-commit, the Python test suite, HACS/Hassfest validation, and CodeQL checks on pull requests.

## Fork and upstream scope

This repository is a fork of [moralmunky/Home-Assistant-Mail-And-Packages](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages). Base-integration fixes from upstream should be reviewed selectively before being ported so they do not break the persistent registry or Super Tracking behavior.

Please keep original attribution intact when carrying upstream work into this fork.

## Bug reports

Use the fork's [issue tracker](../../issues) for bugs involving this repository, especially the persistent package registry, universal scanner, 17TRACK forwarding/enrichment, merchant-order tracking, or fork-specific HACS installation.

For base Mail and Packages documentation, the [upstream wiki](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/wiki) remains the primary reference.

Useful bug reports include:

- A concise description of the problem
- Steps to reproduce it
- What you expected to happen
- What actually happened
- Relevant Home Assistant and Mail and Packages versions
- Diagnostics or log excerpts with secrets and personal data removed

## Coding style

The repository's pre-commit configuration is the source of truth for formatting and linting. Run:

```bash
pre-commit run --all-files
```

before opening a pull request.

## License

By contributing, you agree that your contributions are licensed under the same MIT License that covers this repository.
