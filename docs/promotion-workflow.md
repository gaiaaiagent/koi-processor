# Promotion Workflow — regen-prod → stable

This repo uses a two-branch topology:

| Branch | Role | Consumers |
|--------|------|-----------|
| `regen-prod` | Development tip. Fast-moving. Unvalidated dev work lands here. | Laptop personal KOI (localhost:8351), NUC (via Dobby's `deploy.sh` rsync from local working tree) |
| `stable` | Protected production target. Only validated commits. | **RegenAI production** (`darren@202.61.196.119`) |
| `server/stable` | Historical marker. Untouched. | Legacy reference only |

**Do not merge `regen-prod` into `stable` in bulk.** Promotion is explicit per-commit.

---

## Primary workflow — cherry-pick

Promote one (or a range) of validated commits from `regen-prod` into `stable`:

```bash
cd ~/projects/regenai/koi-processor

# 1. On a validated commit <sha>, cherry-pick onto stable:
git checkout stable
git fetch origin
git cherry-pick <sha>               # or a range: git cherry-pick <from>..<to>
git push origin stable

# 2. Deploy to production:
ssh darren@202.61.196.119 "cd /opt/projects/koi-processor && \
  git pull origin stable && \
  bash scripts/setup.sh"
```

If a cherry-pick conflicts, investigate — do not force. Likely means the target commit depends on
earlier commits not yet on `stable`; cherry-pick those first, or bring the dependency chain along
as a range.

---

## Optional — fast-forward merge

If `stable` is an ancestor of the target commit (no divergence has accumulated), fast-forward is
cheaper and preserves linear history:

```bash
git checkout stable
git fetch origin
git merge --ff-only <sha>           # errors out if stable has diverged — safe to attempt
git push origin stable
```

Attempting `--ff-only` and falling back to cherry-pick on error is a reasonable default for
scripted promotions.

---

## Divergence note

As of 2026-04-14 initial cutover, `stable` has one commit ahead of its `regen-prod` ancestor (a
README update to reference `stable` as the deploy target). This means fast-forward merges from
`regen-prod` will fail until that commit is also present in `regen-prod`'s ancestry — which it
already is (the README commit on `regen-prod` is an ancestor of the cherry-pick on `stable`, but
they have different SHAs, so git doesn't treat them as equivalent).

**Practical consequence:** default to cherry-pick for promotions. Fast-forward only works when
`stable` is strictly behind `regen-prod` — which will gradually become true as more commits land
on both.

---

## What "validated" means

Before cherry-picking a commit onto `stable`, the commit should have at minimum:

- Manual smoke test on laptop personal KOI (localhost:8351) — the commit was already running in
  dev for long enough to trust
- Any eval suite or automated test for the changed area (if one exists)
- Visual code review — diff inspected, understood, not a WIP/debug commit

For high-risk changes (migrations, service-restart behavior, embedding dimensions): also verify on
NUC before promoting.

---

## Operational guardrail

**Do not run the production deploy sequence** (`git pull origin stable && bash scripts/setup.sh`)
between a `git push origin stable` and the intended deploy. The push makes the commit available;
the deploy applies it. Running the deploy between them could pick up a commit you didn't intend —
or worse, the wrong commit if someone else pushed in the interim.

Single-operator discipline: only deploy when you just pushed and know what you pushed.

---

## Rollback

To roll a production deploy back to a prior `stable` commit:

```bash
# Find the target commit
cd ~/projects/regenai/koi-processor
git log stable --oneline -10

# Reset stable (requires push --force, only for rollback)
git reset --hard <target-sha>
git push --force-with-lease origin stable

# Deploy
ssh darren@202.61.196.119 "cd /opt/projects/koi-processor && \
  git pull origin stable && \
  bash scripts/setup.sh"
```

`--force-with-lease` ensures the push is rejected if anyone else pushed to `stable` since your
fetch — prevents stomping on concurrent work.

**This is the one case force-push is acceptable.** Rollback is authorized; all other writes to
`stable` must be append-only.
