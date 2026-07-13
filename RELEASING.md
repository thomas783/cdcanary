# Releasing

Releases are published to PyPI by CI via [trusted publishing] — no API tokens
are stored in the repository or its secrets.

[trusted publishing]: https://docs.pypi.org/trusted-publishers/

## One-time setup (repository owner)

1. Create/log into a PyPI account and open
   **Publishing → Add a new pending publisher** (or the project's
   *Publishing* settings once it exists) and register:

   | field | value |
   |---|---|
   | PyPI project name | `cdcanary` |
   | Owner | `thomas783` |
   | Repository | `cdcanary` |
   | Workflow name | `release.yml` |
   | Environment | `pypi` |

2. In this GitHub repository: **Settings → Environments → New environment**
   named `pypi` (optionally add yourself as a required reviewer so every
   release needs a manual click).

## Cutting a release

```bash
# 1. bump `version` in pyproject.toml, commit via PR
# 2. tag and push
git tag v0.1.0
git push origin v0.1.0
```

The `Release` workflow then lints, tests, builds the sdist + wheel, and
publishes to PyPI. Verify with:

```bash
pip install cdcanary && cdcanary --version
```
