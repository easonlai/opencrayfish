# opencrayfish-skill-echo

**Reference third-party Skill** for [OpenCrayFish](https://github.com/easonlai/OpenCrayFish).

This is the canonical "hello world" plugin — it echoes the operator's
query back. Copy this directory, run a `sed s/echo/<your_skill>/g`,
and you have a working plugin skeleton.

## Install

From the OpenCrayFish repo root:

```bash
pip install -e examples/opencrayfish-skill-echo
```

…then restart OpenCrayFish. The skill is auto-discovered via the
`opencrayfish.skills` entry-point group. Look for these lines in the
boot log:

```
SKILL registered name=echo protocol=skill-protocol/1 ...
SKILL Discovered 1 external skill(s) via entry-points: echo
```

## Validate the manifest before publishing

```bash
opencrayfish skill validate opencrayfish_skill_echo:EchoSkill
```

Expected output:

```
ok: resolved manifest for 'echo'
  description    = '...'
  compat_version = skill-protocol/1
  plan_verb      = 'ECHO'
  cost_tier      = free
  ...
ok: bootstrap_validate clean (tool checks skipped — no ToolRegistry)
```

## Anatomy

| File | Purpose |
| --- | --- |
| `pyproject.toml` | Declares the `opencrayfish.skills` entry-point so OpenCrayFish's boot-time discovery finds the skill. |
| `opencrayfish_skill_echo/__init__.py` | The skill itself: `SkillManifest` + async `execute` verb + `aclose` hook. |
| `tests/test_echo.py` | Manifest resolution + dry-register sanity test. |

## What this example does NOT show

* **Tool invocation** via `ctx.tools.call(...)` — see the `research`
  skill in the OpenCrayFish source for that.
* **Multi-verb packages** — one package can export multiple Skills
  via additional `[project.entry-points."opencrayfish.skills"]` rows.

## License

Same as the parent OpenCrayFish project (MIT).
