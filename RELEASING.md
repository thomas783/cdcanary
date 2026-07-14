# Releasing

Releases are published to PyPI by CI via [trusted publishing] — no API tokens
are stored in the repository or its secrets.

[trusted publishing]: https://docs.pypi.org/trusted-publishers/

## Cutting a release

```bash
# 1. bump `version` in pyproject.toml, land it on main via PR
# 2. tag and push
git tag v0.2.0
git push origin v0.2.0
```

The `Release` workflow lints, tests, builds the sdist + wheel, and publishes
to PyPI. Then create a GitHub release for the tag and verify:

```bash
pip install --upgrade cdcanary && cdcanary --version
```

## Publisher configuration (already set up)

The PyPI trusted publisher and the GitHub `pypi` environment are already
configured for this repository. You only need this again if you fork the
project or re-create the PyPI project:

| field | value |
|---|---|
| PyPI project name | `cdcanary` |
| Owner | `thomas783` |
| Repository | `cdcanary` |
| Workflow name | `release.yml` |
| Environment | `pypi` |

Register these under **PyPI → project → Publishing** (or *pending publisher*
if the project doesn't exist yet), and create a matching `pypi` environment
in GitHub **Settings → Environments** — optionally with a required reviewer
to gate releases.
