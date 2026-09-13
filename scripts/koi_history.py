#!/usr/bin/env python3
"""koi-history — operator CLI for the document version history store (Stream A).

Step 1 of ~/.claude/plans/history-preserving-website-sensor.md installs ONLY this
dispatcher. Each subcommand lands with the implementation step that builds its
underlying capability, and NEVER ahead of it — a CLI that offers a verb the system
cannot perform is a lie the operator discovers at the worst moment.

Registration is therefore explicit and staged:

    status          step 5   invariant queries + drift lines, one screen
    versions        step 10  the flat chain_id walk behind GET /documents/{rid}/versions
    search          step 11  substring over superseded rows + the git-archive blob scan
    restore-chunks  step 12b re-ingest an archived version's bytes, hash-checked first
    set-policy      step 15  the audited history_policy change, prints its blast radius
    release         step 15  release a claim (stops it protecting the bytes)
    unstage         step 15  operator escape hatch for a stuck staged successor

Until a verb is registered, asking for it exits 2 and says which step provides it,
rather than failing with an obscure traceback or — worse — appearing to work.
"""
from __future__ import annotations

import argparse
import sys
from typing import Callable, Dict, NamedTuple


class Verb(NamedTuple):
    """A subcommand that may or may not be implemented yet."""

    step: str          # the plan step that lands it
    summary: str       # one line for --help
    handler: Callable[[argparse.Namespace], int] | None  # None == not yet built


# The single source of truth for what this CLI can do. A verb whose handler is None
# is ANNOUNCED but not offered: `koi-history status` exits 2 with a pointer, so the
# gap is visible rather than mysterious.
VERBS: Dict[str, Verb] = {
    "status": Verb("step 5", "invariant queries + drift lines, one screen", None),
    "versions": Verb("step 10", "walk a document's version chain from any member rid", None),
    "search": Verb("step 11", "search superseded versions (DB substring + git archive)", None),
    "restore-chunks": Verb("step 12b", "restore an archived version's chunks, hash-checked", None),
    "set-policy": Verb("step 15", "change a source's history_policy (audited, --confirm)", None),
    "release": Verb("step 15", "release a claim so it stops protecting the bytes", None),
    "unstage": Verb("step 15", "clear a stuck staged successor", None),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="koi-history",
        description=(
            "Operator CLI for the personal-KOI document version history store. "
            "Subcommands are registered as the plan's implementation steps land them."
        ),
        epilog="\n".join(
            ["verbs:"]
            + [
                "  {:<15} {:<9} {}{}".format(
                    name,
                    v.step,
                    v.summary,
                    "" if v.handler else "   [NOT YET BUILT]",
                )
                for name, v in VERBS.items()
            ]
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "verb",
        nargs="?",
        choices=sorted(VERBS),
        help="subcommand to run (see the list below)",
    )
    parser.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="arguments forwarded to the subcommand",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)

    if ns.verb is None:
        parser.print_help()
        return 0

    verb = VERBS[ns.verb]
    if verb.handler is None:
        # Exit 2, not 0 and not 1: this is "you asked for something that does not
        # exist yet", which is a usage error, and it must never be mistaken for the
        # verb having run. Pointing at the step is what keeps the gap legible.
        print(
            f"koi-history: '{ns.verb}' is not built yet — it lands in {verb.step}.\n"
            f"  ({verb.summary})\n"
            f"Plan: ~/.claude/plans/history-preserving-website-sensor.md",
            file=sys.stderr,
        )
        return 2

    return verb.handler(ns)


if __name__ == "__main__":
    raise SystemExit(main())
