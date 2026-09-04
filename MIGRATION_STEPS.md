# v2 -> v3 migration

1. Create a branch from current `main`, e.g. `v3-canonical-layout`.
2. Replace/add the files from this bundle.
3. Commit the code-only change and merge it to `main`.
4. Do **not** enable large prune on a scheduled run.
5. In GitHub Actions, manually run `Sync CDC NHANES Docs` on `main` with:
   - `allow_large_prune = true`
   - `skip_content_verify = false`
6. The migration run will:
   - use official `Cycle=` + 5 public components;
   - add the real `2017-2020` P_* release;
   - create canonical `2021-2023` from legacy 2021/2023 copies where possible;
   - remove pseudo `delayed/`, false demographic duplicates, and legacy 2021/2023 directories;
   - auto-commit the document migration if successful.
7. Send the completed Actions run URL for a final audit.

Safety notes:
- All official indexes are scanned and validated before local changes begin.
- Normal prune is capped at 100 deletions.
- The large migration requires explicit `--allow-large-prune` / workflow input.
