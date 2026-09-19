# Deployment — September 19, 2026

The architect authorized deployment in the follow-up task. This is an installed
code release, not a funded-world or mainnet launch.

- Host: existing `superdarkfactory`, `152.42.221.133`.
- Active source pointer: `/srv/factorylab/repo` →
  `/srv/factorylab/releases/a4e022622894`.
- Release digest:
  `a4e022622894d9064fdc26e840c08be52344202b4569be24a2f52d8eec13bd2a`.
- Package tree:
  `156de86277e0bc8a574c27afbe91f7b614fc45054d9193772f0d6d5f647a8492`.
- Lock hash:
  `a7c0fc7a20bc98d2aaec3688cf5f5f40cb767db596d087387f3a0a831fa8a8eb`.
- Base commit: `6c4c86455963f1b9f45edeff75cb99438ade050e`, **plus the tested
  uncommitted working-tree changes**. This is not a claim that these follow-up
  changes are committed or merged. The release digest binds their actual bytes.
- Preserved source archive:
  `/srv/factorylab/releases/a4e022622894.tar.gz`.
- Archive SHA-256:
  `09ce095ce27e9bee79fba43e16e795872524c548fb5518978c3b502f9368171b`.
- Previous installation preserved at
  `/srv/factorylab/releases/pre-20260919`.

Verification on the host:

1. Archive checksum matched before extraction; `uv sync --frozen` installed the
   locked environment.
2. Host release, package and lock digests exactly matched local validation.
3. The Linux jail probe passed as user `factory` under the service's protected
   home/filesystem settings. Python is the existing service-accessible
   `/opt/factorylab-python/cpython-3.13.15-linux-x86_64-gnu/bin/python3.13`.
4. A 50-event scripted smoke run passed under those restrictions, with
   `wallet_conservation: true`, `ledger_verify: true`, `live: false`, and no
   uncertain fake-exchange orders. Its report is
   `/srv/factorylab/releases/a4e022622894-smoke.json`.
5. Before switching the source pointer, the factory service was inactive and no
   Python/factory process was running on the host. After switching:

```
ActiveState=inactive
SubState=dead
UnitFileState=disabled
```

The first staging attempt used a root-private Python installation and failed the
service-user execution check before any source switch. Rebuilding that staged
environment against the existing `/opt` interpreter resolved it. No live world
was upgraded or restarted.

Rollback, only while no world is running: remove the new `/srv/factorylab/repo`
symlink and move `pre-20260919` back to that exact original path. Its old editable
environment expects that path, so do not treat the preserved directory as an
independently relocated installation. Leave funded services stopped unless a
separate launch has been authorized.

No credential files or manifest named `funded` were read or packaged. SSH used
the existing authenticated deployment identity; no new host was purchased.
