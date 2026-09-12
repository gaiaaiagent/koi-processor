# Backup and recovery

What is backed up, where it goes, and — the part that did not exist until
2026-09-12 — **how to actually get it back**.

An adversarial review on that date found no file anywhere in this repo, in
`CLAUDE.md`, or in the vault that mentioned `gaia`, `offsite`, or any restore
procedure. The machinery existed and the instructions did not, which meant the
recovery path had never been written down, let alone walked.

## What exists

| What | Where it lives | Off-host copy | Encrypted off-host |
|---|---|---|---|
| `personal_koi` (28 GB, ~12.5 GB dump) | `~/koi-backups/` | `gaia:koi-offsite/` | yes |
| `eliza`, `salishsee_public`, `indigenomics_koi`, `infinite_regen` | `~/koi-backups/` | `gaia:koi-offsite/` | yes |
| Source archive (`~/Documents/koi-source-archive`, 913 MB) | that repo | `gaia:koi-archive-offsite/` | yes |
| Obsidian vault | `~/Documents/Notes` | `dobby:backups/git/vault-nuc.git` | no (own machine) |

Local retention: 7 daily, Sunday dumps kept 28 days. Off-host retention: newest
7 per database. **These differ** — off-host has no weekly tier, so off-host
depth is ~7 days against ~28 locally. That is a deliberate simplification, not
an oversight; if it ever matters, the weekly logic lives in `koi_backup.sh`.

## Jobs

| launchd label | When | Script |
|---|---|---|
| `com.personal-koi.backup` | daily 03:15 | [`koi_backup_all.sh`](../scripts/koi_backup_all.sh) |
| `com.personal-koi.archive-offsite` | daily 08:30 | [`koi_archive_offsite.sh`](../scripts/koi_archive_offsite.sh) |
| `com.personal-koi.website-sensor` | Sundays 06:40 | website sensor (koi-processor-runtime) |
| `com.personal-koi.backup-check` | daily 12:00 | [`koi_backup_check.sh`](../scripts/koi_backup_check.sh) |

## Keys — read this before you need it

Everything off-host is encrypted to **`F5EE933A8DC407E4`**
(fingerprint `1F1E907D76675C3A35B1DE4BF5EE933A8DC407E4`), encryption-only, no
expiry, **no passphrase on the private key**.

The private key is deliberately **not on gaia**, which holds the ciphertext.
Neither host alone can read anything. It exists in three places:

1. This laptop's GnuPG keyring.
2. `dobby:~/keys/koi-archive-key-secret.asc` (the NUC).
3. macOS login Keychain, account `koi-archive-backup`.

Retrieve it from the Keychain with:

```
security find-generic-password -a koi-archive-backup \
  -s "KOI archive GPG private key (F5EE933A8DC407E4)" -w | xxd -r -p > key.asc
```

**The `xxd -r -p` is not optional.** `security -w` returns the secret
hex-encoded, so without it you get 6963 bytes of hex that look like a corrupt
key. (The Keychain copy is one byte shorter than the original — the trailing
newline — and imports to the same fingerprint. Verified.)

Reaching gaia needs an authorised SSH key. As of 2026-09-12 two are authorised:
this laptop's, and the NUC's (`dobby@nuc`). Before that date only the laptop's
was, which meant the machine holding the decryption key could not fetch the
ciphertext and the machine that could fetch it had died with the laptop. **If
you add a recovery machine, authorise its key on gaia at the same time as you
give it the GPG key — one without the other recovers nothing.**

## Restoring a database

Requires on the target: PostgreSQL with extensions `vector` (0.8.0), `pg_trgm`,
`uuid-ossp`, `fuzzystrmatch`. `pg_restore` will fail on the extension
statements otherwise. Roles are **not** needed — every object is owned by
`darrenzal` and `salishsee_reader` holds no grants, so `pg_dumpall
--globals-only` is unnecessary.

```
# 1. fetch (from any machine authorised on gaia)
scp claudeuser@152.53.37.180:koi-offsite/personal_koi-YYYYMMDD-HHMMSS.dump.gpg .

# 2. confirm it is what you think it is, before spending time on it
gpg --list-packets personal_koi-*.dump.gpg | head -1
#    -> "encrypted with RSA key, ID F5EE933A8DC407E4"

# 3. decrypt
gpg --batch --pinentry-mode loopback --passphrase '' \
    -o personal_koi.dump -d personal_koi-*.dump.gpg

# 4. sanity-check the plaintext BEFORE restoring
pg_restore --list personal_koi.dump | head

# 5. restore
createdb personal_koi_restored
pg_restore -d personal_koi_restored -j 4 personal_koi.dump
```

**The off-host copy is checksum-verified, not restore-verified.** gaia has
`sha256sum` but no `pg_restore`, so the nightly job proves the bytes arrived
intact and cannot prove the dump restores. The local integrity check
(`pg_restore --list`) only reads the table of contents, so it catches a
truncated or 0-byte dump and would not catch corruption inside a data block.
If you want a real guarantee, restore one periodically — that is the only test
that settles it.

## Restoring the source archive

```
scp claudeuser@152.53.37.180:koi-archive-offsite/koi-source-archive-*.bundle.gpg .
gpg --batch --pinentry-mode loopback --passphrase '' \
    -o archive.bundle -d koi-source-archive-*.bundle.gpg
git clone archive.bundle koi-source-archive
```

Walked end-to-end on the NUC on 2026-09-12 with the laptop assumed destroyed:
recovered tree `f3fb091d2f9898bbdfc035acb272f0253219e437`, byte-identical to
source, 39 commits, 297 files.

That repo's README says "no remote, never pushed" — that is a rule about what
**material** may leave the machine, not a durability preference. Do not satisfy
a backup concern by pushing it somewhere; a push puts readable copyrighted
content on a second host. The encrypted bundle satisfies both.

## Is it still working?

```
scripts/koi_backup_check.sh
```

Exit 0 = everything fresh. Exit 1 = names what is stale or missing. It reads
the per-database markers `~/koi-backups/.last-offhost-sync-<db>` and
`~/koi-backups/.last-archive-offsite`, which before 2026-09-12 were written
faithfully and **read by nothing**.

It also flags a `START` line in `backup.log` with no matching `OK`/`FAIL`/
`ABORT`. That is not a hypothetical pattern: the 2026-09-11 backup never
completed, the machine having restarted mid-run so the cleanup trap never
fired, and there was no dump for that date and no error anywhere. It was
noticed a day later by a human reading the log for an unrelated reason.

## Things that look like guards and are not

Kept here because each one cost real time to find, and each looked correct.

- **`file "$f" | grep -qi "PGP\|GPG"`** matches the *filename* when the file is
  named `*.gpg`, because `file` without `-b` echoes the path. It returns true
  for cleartext, an empty file, and random bytes. Use `file -b`, and pair it
  with a check that can fail the other way — `pg_restore --list` or
  `git bundle verify` must **reject** genuine ciphertext.
- **`plutil -lint`** accepts a plist that is not well-formed XML (`--` inside a
  comment). Validate with `python3 -c 'import plistlib; plistlib.loads(...)'`.
- **`stat`** on this machine resolves to a third-party `/usr/local/bin/stat`
  that is SIGKILLed on every path, `/etc/hosts` included. Use `/usr/bin/stat`.
  A failure from it says nothing about the path you pointed it at.
- **A test suite that prints `fail=3` and exits 0** reports success to every
  automated caller. All three suites here now end with `[ "$fail" -eq 0 ] || exit 1`.
- **`rc=$?` inside a signal trap** is the last *completed* command's status,
  which is 0 whenever the signal lands after a success. Re-raise the signal.
- **macOS has no `timeout(1)`** and gtimeout is not installed. Without a bound,
  a hung `git`/`ssh`/`rsync` blocks the job forever with no output and no
  failure. `bounded()` in the offsite scripts uses perl's `alarm`, which works
  inside `$( )` unlike a background-and-kill helper.
