"""BibReview command-line interface."""

from __future__ import annotations

import argparse
import json
from importlib import import_module
import sys

from . import __version__
from .arxiv import ArxivError, TemporaryArxivError
from .audit_resolution import (
    audit_resolution_path,
    format_audit_resolution_candidate,
    load_project_audit_resolutions,
    parse_custom_resolution_value,
    record_audit_resolution,
    save_project_audit_resolutions,
    unresolved_resolution_candidates,
)
from .backfill_resolution import (
    backfill_resolution_path,
    format_backfill_resolution_candidate,
    load_project_backfill_resolutions,
    record_backfill_resolution,
    save_project_backfill_resolutions,
    unresolved_backfill_candidates,
)
from .campaign import campaign_progress
from .config import ConfigError, load_config
from .pipeline.authors import author_mapping_plan_data, format_author_mapping_plan
from .pipeline.backfill import BACKFILL_FIELDS
from .provider_diagnostics import diagnose_providers, format_provider_diagnostics
from .pipeline.merge import MergeError
from .hygiene import format_abstract_hygiene_report
from .hygiene_resolution import (
    format_hygiene_resolution_candidate,
    hygiene_resolution_path,
    load_project_hygiene_resolutions,
    record_hygiene_resolution,
    save_project_hygiene_resolutions,
    unresolved_hygiene_candidates,
)
from .project import (
    ProjectStateError,
    apply_project_author_mappings,
    apply_project_collection,
    apply_project_discovery,
    apply_project_merge,
    plan_project_author_mappings,
    plan_project_collection,
    plan_project_discovery,
    plan_project_merge,
)
from .project_arxiv import apply_project_arxiv, plan_project_arxiv
from .project_hygiene import (
    apply_project_hygiene_proposals,
    format_project_hygiene_review,
    hygiene_review_path,
    load_project_hygiene_review,
    plan_project_hygiene_proposals,
    project_abstract_hygiene,
)
from .project_hygiene_apply import (
    apply_project_hygiene_apply,
    plan_project_hygiene_apply,
)
from .project_backfill import (
    apply_project_backfill_plan,
    load_project_backfill_review,
    plan_project_backfill,
)
from .project_backfill_apply import (
    apply_project_backfill_apply,
    plan_project_backfill_apply,
)
from .project_audit import (
    apply_project_audit_plan,
    execute_project_audit_batch,
    format_project_audit_review,
    plan_project_audit_batch,
    plan_project_audit_reclassify,
    project_audit_review,
)
from .project_audit_apply import (
    apply_project_audit_apply,
    format_project_audit_apply_plan,
    plan_project_audit_apply,
)
from .project_refresh import (
    apply_project_refresh,
    format_project_refresh_review,
    load_project_refresh_review,
    plan_project_refresh,
    refresh_review_path,
)
from .project_refresh_apply import (
    apply_project_refresh_apply,
    plan_project_refresh_apply,
)
from .project_render import apply_project_render, plan_project_render
from .refresh_resolution import (
    format_backfill_resolution_candidate as format_refresh_resolution_candidate,
    load_project_refresh_resolutions,
    record_backfill_resolution as record_refresh_resolution,
    refresh_resolution_path,
    refresh_resolution_summary,
    save_project_refresh_resolutions,
    unresolved_refresh_candidates,
)
from .reporting import Reporter
from .runtime import (
    build_audit_services,
    build_collection_services,
    build_discovery_services,
)
from .storage import StorageError


def _enable_interactive_line_editing() -> None:
    """Enable terminal line editing for input() when Python readline is available."""
    try:
        import_module("readline")
    except ImportError:
        return


def _audit_progress_data(campaign) -> dict[str, object]:
    progress = campaign_progress(campaign)
    return {
        **progress.data(),
        "exhausted": progress.exhausted,
        "successful": progress.successful,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bibreview")
    parser.add_argument("--config", default="bibreview.yml", help="Project configuration file")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("-v", "--verbose", action="count", default=0)
    output.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Validate and plan mutating commands without writing files")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="Validate project configuration/state")
    commands.add_parser("status", help="Show the current project configuration summary")
    providers = commands.add_parser(
        "providers",
        help="Inspect provider credentials/configuration and optionally perform live checks",
    )
    providers.add_argument(
        "--check",
        action="store_true",
        help="Perform one sanitized live request per provider that is ready to use",
    )
    providers.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Print diagnostics as JSON instead of a human report",
    )
    hygiene = commands.add_parser(
        "hygiene",
        help="Scan or review canonical abstract structured-markup hygiene",
    )
    hygiene_actions = hygiene.add_mutually_exclusive_group()
    hygiene_actions.add_argument(
        "--propose",
        action="store_true",
        help="Persist reviewed historical normalization proposals from the current canon",
    )
    hygiene_actions.add_argument(
        "--review",
        action="store_true",
        help="Show persisted historical hygiene proposals without modifying project state",
    )
    hygiene_actions.add_argument(
        "--resolve",
        action="store_true",
        help="Interactively resolve persisted historical hygiene proposals",
    )
    hygiene_actions.add_argument(
        "--apply",
        action="store_true",
        help="Stage completed historical hygiene decisions for ordinary merge",
    )
    hygiene.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Print complete hygiene inventory/review data as JSON",
    )
    audit = commands.add_parser(
        "audit",
        help="Audit one stable batch of existing canonical publications",
    )
    audit.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Override the size of the next new audit batch; campaign default otherwise",
    )
    audit_actions = audit.add_mutually_exclusive_group()
    audit_actions.add_argument(
        "--full",
        action="store_true",
        help="Re-audit every current canonical publication instead of only new/retryable items",
    )
    audit_actions.add_argument(
        "--reclassify",
        action="store_true",
        help="Reclassify the existing audit report offline using current rules",
    )
    audit_actions.add_argument(
        "--review",
        action="store_true",
        help="Show the current actionable audit review without provider requests",
    )
    audit_actions.add_argument(
        "--resolve",
        action="store_true",
        help="Interactively resolve actionable audit findings without changing canonical metadata",
    )
    audit_actions.add_argument(
        "--apply",
        action="store_true",
        help="Apply completed audit resolutions to reviewable staging and tracked BibTeX",
    )
    audit.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Print the audit result as JSON",
    )
    commands.add_parser("discover", help="Discover and screen new DOI candidates")
    commands.add_parser("collect", help="Collect pending DOI metadata into canonical staging state")
    backfill = commands.add_parser(
        "backfill",
        help="Fill selected missing canonical fields without replacing reviewed metadata",
    )
    backfill.add_argument(
        "--field",
        dest="backfill_fields",
        action="append",
        choices=sorted(BACKFILL_FIELDS),
        default=[],
        help="Missing canonical field to propose; repeat for multiple fields",
    )
    backfill.add_argument(
        "--type",
        dest="backfill_types",
        action="append",
        default=[],
        help="Restrict proposal generation to one publication type; repeat for multiple types",
    )
    backfill_actions = backfill.add_mutually_exclusive_group()
    backfill_actions.add_argument(
        "--resolve",
        action="store_true",
        help="Interactively review persisted backfill proposals",
    )
    backfill_actions.add_argument(
        "--apply",
        action="store_true",
        help="Apply completed human backfill decisions to collected staging",
    )
    refresh = commands.add_parser(
        "refresh",
        help="Review stale existing publications without overwriting canonical metadata",
    )
    refresh_actions = refresh.add_mutually_exclusive_group()
    refresh_actions.add_argument(
        "--review",
        action="store_true",
        help="Show the persisted safe refresh review without provider requests",
    )
    refresh_actions.add_argument(
        "--resolve",
        action="store_true",
        help="Interactively resolve safe missing-field refresh proposals",
    )
    refresh_actions.add_argument(
        "--apply",
        action="store_true",
        help="Apply completed safe refresh decisions to collected staging",
    )
    authors = commands.add_parser("authors", help="Inspect author identities and optionally apply safe mappings")
    authors.add_argument(
        "--apply-safe",
        action="store_true",
        help="Add unique new authors with no plausible existing match",
    )
    authors.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Print the analysis as JSON instead of a human report",
    )
    commands.add_parser("merge", help="Merge the collected staging bibliography into project state")
    commands.add_parser("render", help="Render and reconcile configured static-site artifacts")
    commands.add_parser("arxiv", help="Refresh the optional configured arXiv cache")
    return parser


def _run_hygiene_resolution(config, args) -> int:
    """Run resumable human review for historical canonical abstract cleanup."""
    if args.quiet:
        raise ProjectStateError(
            "--quiet cannot be used with interactive hygiene --resolve"
        )
    if args.json_output:
        raise ProjectStateError(
            "--json cannot be used with interactive hygiene --resolve"
        )

    review = load_project_hygiene_review(config)
    state = load_project_hygiene_resolutions(config, review)
    candidates = unresolved_hygiene_candidates(review, state)
    path = hygiene_resolution_path(config)

    if not candidates:
        prefix = "Dry run: " if args.dry_run else ""
        print(prefix + state.summary())
        print("No unresolved hygiene proposals.")
        print(f"Resolutions: {path}")
        return 0

    _enable_interactive_line_editing()

    for candidate in candidates:
        print(format_hygiene_resolution_candidate(candidate))
        review_required = candidate.proposal.review_required
        prompt = (
            "Decision [n/f VALUE/s/q]: "
            if review_required
            else "Decision [Y/n/f VALUE/s/q]: "
        )
        while True:
            try:
                raw = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                print(
                    "Hygiene resolution stopped; previous decisions are preserved."
                )
                print(state.summary())
                print(f"Resolutions: {path}")
                return 0

            choice = raw.lower()
            try:
                if raw == "" or choice in {"y", "yes"}:
                    if review_required:
                        print(
                            "No safe automatic normalized abstract is available; "
                            "use f VALUE for a reviewed custom value, n to reject, "
                            "or s to defer."
                        )
                        continue
                    state = record_hygiene_resolution(
                        state,
                        candidate,
                        decision="accepted",
                    )
                    break

                if choice in {"n", "no"}:
                    state = record_hygiene_resolution(
                        state,
                        candidate,
                        decision="rejected",
                    )
                    break

                if choice in {"s", "skip"}:
                    state = record_hygiene_resolution(
                        state,
                        candidate,
                        decision="deferred",
                    )
                    break

                if choice in {"q", "quit"}:
                    print(state.summary())
                    print(f"Resolutions: {path}")
                    return 0

                if choice == "f" or choice.startswith("f "):
                    custom = raw[1:].strip()
                    if not custom:
                        try:
                            custom = input("Custom abstract: ").strip()
                        except (EOFError, KeyboardInterrupt):
                            print()
                            print(
                                "Hygiene resolution stopped; previous decisions "
                                "are preserved."
                            )
                            print(state.summary())
                            print(f"Resolutions: {path}")
                            return 0
                    state = record_hygiene_resolution(
                        state,
                        candidate,
                        decision="custom",
                        resolved_value=custom,
                    )
                    break
            except ProjectStateError as error:
                print(f"Invalid resolution: {error}")
                continue

            if review_required:
                print("Please enter n, f VALUE, s, or q.")
            else:
                print("Please enter Y, n, f VALUE, s, or q.")

        if not args.dry_run:
            save_project_hygiene_resolutions(config, state)
        print()

    prefix = "Dry run: " if args.dry_run else ""
    print(prefix + state.summary())
    print(f"Resolutions: {path}")
    return 0


def _run_audit_resolution(config, args) -> int:
    """Run the resumable interactive resolver for actionable audit findings."""
    if args.batch_size is not None:
        raise ProjectStateError("--batch-size cannot be used with --resolve")
    if args.json_output:
        raise ProjectStateError("--json cannot be used with interactive --resolve")
    if args.quiet:
        raise ProjectStateError("--quiet cannot be used with interactive --resolve")

    review = project_audit_review(config)
    state = load_project_audit_resolutions(config, review)
    candidates = unresolved_resolution_candidates(review, state)
    path = audit_resolution_path(config)

    if not candidates:
        prefix = "Dry run: " if args.dry_run else ""
        print(prefix + state.summary())
        print("No unresolved actionable findings.")
        print(f"Resolutions: {path}")
        return 0

    _enable_interactive_line_editing()

    for candidate in candidates:
        print(format_audit_resolution_candidate(candidate))
        while True:
            prompt = (
                "Decision [Y/n/f VALUE/s/q]: "
                if candidate.proposed_value is not None
                else "Decision [f VALUE/n/s/q]: "
            )
            try:
                raw = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                print("Resolution session stopped; previous decisions are preserved.")
                print(state.summary())
                print(f"Resolutions: {path}")
                return 0

            choice = raw.lower()
            try:
                if raw == "" or choice in {"y", "yes"}:
                    if candidate.proposed_value is None:
                        print(
                            "No exact common provider representation is available; "
                            "use f VALUE, n, s, or q."
                        )
                        continue
                    state = record_audit_resolution(
                        state,
                        candidate,
                        decision="accepted",
                    )
                    break

                if choice in {"n", "no"}:
                    state = record_audit_resolution(
                        state,
                        candidate,
                        decision="rejected",
                    )
                    break

                if choice in {"s", "skip"}:
                    state = record_audit_resolution(
                        state,
                        candidate,
                        decision="deferred",
                    )
                    break

                if choice in {"q", "quit"}:
                    print(state.summary())
                    print(f"Resolutions: {path}")
                    return 0

                if choice == "f" or choice.startswith("f "):
                    custom_text = raw[1:].strip()
                    if not custom_text:
                        try:
                            custom_text = input("Custom value: ").strip()
                        except (EOFError, KeyboardInterrupt):
                            print()
                            print(
                                "Resolution session stopped; previous decisions "
                                "are preserved."
                            )
                            print(state.summary())
                            print(f"Resolutions: {path}")
                            return 0
                    custom = parse_custom_resolution_value(
                        custom_text,
                        candidate,
                    )
                    state = record_audit_resolution(
                        state,
                        candidate,
                        decision="custom",
                        resolved_value=custom,
                    )
                    break
            except ProjectStateError as error:
                print(f"Invalid resolution: {error}")
                continue

            print("Please enter Y, n, f VALUE, s, or q.")

        if not args.dry_run:
            save_project_audit_resolutions(config, state)
        print()

    prefix = "Dry run: " if args.dry_run else ""
    print(prefix + state.summary())
    print(f"Resolutions: {path}")
    return 0


def _run_backfill_resolution(config, args) -> int:
    """Run resumable human review for persisted backfill proposals."""
    if args.backfill_fields or args.backfill_types:
        raise ProjectStateError(
            "--field/--type cannot be used with backfill --resolve"
        )
    if args.quiet:
        raise ProjectStateError(
            "--quiet cannot be used with interactive backfill --resolve"
        )

    review = load_project_backfill_review(config)
    state = load_project_backfill_resolutions(config, review)
    candidates = unresolved_backfill_candidates(review, state)
    path = backfill_resolution_path(config)

    if not candidates:
        prefix = "Dry run: " if args.dry_run else ""
        print(prefix + state.summary())
        print("No unresolved backfill proposals.")
        print(f"Resolutions: {path}")
        return 0

    _enable_interactive_line_editing()

    for candidate in candidates:
        print(format_backfill_resolution_candidate(candidate))
        review_required = candidate.proposal.review_required
        prompt = (
            "Decision [n/f VALUE/s/q]: "
            if review_required
            else "Decision [Y/n/f VALUE/s/q]: "
        )
        while True:
            try:
                raw = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                print(
                    "Backfill resolution stopped; previous decisions are preserved."
                )
                print(state.summary())
                print(f"Resolutions: {path}")
                return 0

            choice = raw.lower()
            try:
                if raw == "" or choice in {"y", "yes"}:
                    if review_required:
                        print(
                            "No safe automatic value is available; use f VALUE "
                            "for a reviewed custom value, n to reject, or s to defer."
                        )
                        continue
                    state = record_backfill_resolution(
                        state,
                        candidate,
                        decision="accepted",
                    )
                    break

                if choice in {"n", "no"}:
                    state = record_backfill_resolution(
                        state,
                        candidate,
                        decision="rejected",
                    )
                    break

                if choice in {"s", "skip"}:
                    state = record_backfill_resolution(
                        state,
                        candidate,
                        decision="deferred",
                    )
                    break

                if choice in {"q", "quit"}:
                    print(state.summary())
                    print(f"Resolutions: {path}")
                    return 0

                if choice == "f" or choice.startswith("f "):
                    custom = raw[1:].strip()
                    if not custom:
                        try:
                            custom = input("Custom value: ").strip()
                        except (EOFError, KeyboardInterrupt):
                            print()
                            print(
                                "Backfill resolution stopped; previous decisions "
                                "are preserved."
                            )
                            print(state.summary())
                            print(f"Resolutions: {path}")
                            return 0
                    state = record_backfill_resolution(
                        state,
                        candidate,
                        decision="custom",
                        resolved_value=custom,
                    )
                    break
            except ProjectStateError as error:
                print(f"Invalid resolution: {error}")
                continue

            if review_required:
                print("Please enter n, f VALUE, s, or q.")
            else:
                print("Please enter Y, n, f VALUE, s, or q.")

        if not args.dry_run:
            save_project_backfill_resolutions(config, state)
        print()

    prefix = "Dry run: " if args.dry_run else ""
    print(prefix + state.summary())
    print(f"Resolutions: {path}")
    return 0


def _run_refresh_resolution(config, args) -> int:
    """Run resumable human review for refresh proposals."""
    if args.quiet:
        raise ProjectStateError(
            "--quiet cannot be used with interactive refresh --resolve"
        )

    review = load_project_refresh_review(config)
    state = load_project_refresh_resolutions(config, review)
    candidates = unresolved_refresh_candidates(review, state)
    path = refresh_resolution_path(config)

    if not candidates:
        prefix = "Dry run: " if args.dry_run else ""
        print(prefix + refresh_resolution_summary(state))
        print("No unresolved refresh proposals.")
        print(f"Resolutions: {path}")
        return 0

    _enable_interactive_line_editing()

    for candidate in candidates:
        print(format_refresh_resolution_candidate(candidate))
        review_required = candidate.proposal.review_required
        prompt = (
            "Decision [n/f VALUE/s/q]: "
            if review_required
            else "Decision [Y/n/f VALUE/s/q]: "
        )
        while True:
            try:
                raw = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                print(
                    "Refresh resolution stopped; previous decisions are preserved."
                )
                print(refresh_resolution_summary(state))
                print(f"Resolutions: {path}")
                return 0

            choice = raw.lower()
            try:
                if raw == "" or choice in {"y", "yes"}:
                    if review_required:
                        print(
                            "No safe automatic value is available; use f VALUE "
                            "for a reviewed custom value, n to reject, or s to defer."
                        )
                        continue
                    state = record_refresh_resolution(
                        state,
                        candidate,
                        decision="accepted",
                    )
                    break

                if choice in {"n", "no"}:
                    state = record_refresh_resolution(
                        state,
                        candidate,
                        decision="rejected",
                    )
                    break

                if choice in {"s", "skip"}:
                    state = record_refresh_resolution(
                        state,
                        candidate,
                        decision="deferred",
                    )
                    break

                if choice in {"q", "quit"}:
                    print(refresh_resolution_summary(state))
                    print(f"Resolutions: {path}")
                    return 0

                if choice == "f" or choice.startswith("f "):
                    custom = raw[1:].strip()
                    if not custom:
                        try:
                            custom = input("Custom value: ").strip()
                        except (EOFError, KeyboardInterrupt):
                            print()
                            print(
                                "Refresh resolution stopped; previous decisions "
                                "are preserved."
                            )
                            print(refresh_resolution_summary(state))
                            print(f"Resolutions: {path}")
                            return 0
                    state = record_refresh_resolution(
                        state,
                        candidate,
                        decision="custom",
                        resolved_value=custom,
                    )
                    break
            except ProjectStateError as error:
                print(f"Invalid resolution: {error}")
                continue

            if review_required:
                print("Please enter n, f VALUE, s, or q.")
            else:
                print("Please enter Y, n, f VALUE, s, or q.")

        if not args.dry_run:
            save_project_refresh_resolutions(config, state)
        print()

    prefix = "Dry run: " if args.dry_run else ""
    print(prefix + refresh_resolution_summary(state))
    print(f"Resolutions: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as error:
        print(f"bibreview: {error}", file=sys.stderr)
        return 1

    if args.command == "validate":
        if not args.quiet:
            print(f"Configuration valid: {config.source}")
        return 0

    if args.command == "status":
        if not args.quiet:
            enabled = sorted(name for name, provider in config.providers.items() if provider.enabled)
            print(f"Project: {config.project.name} ({config.project.slug})")
            print(f"Schema: {config.schema_version}")
            print(f"Discovery: {config.discovery.provider} / {config.discovery.query or '(no query)'}")
            refresh_types = ", ".join(config.refresh.types) if config.refresh.types else "disabled"
            print(f"Refresh: {refresh_types}")
            print("Providers: " + (", ".join(enabled) if enabled else "none"))
            print(f"Bibliography: {config.paths.bibliography}")
            print(f"Collected staging: {config.paths.collected}")
            arxiv_status = (
                f"enabled → {config.arxiv.output}"
                if config.arxiv.enabled
                else "disabled"
            )
            print(f"arXiv: {arxiv_status}")
        return 0

    if args.command == "providers":
        reporter = Reporter(-1 if args.quiet else args.verbose)
        try:
            diagnostics = diagnose_providers(
                config,
                check=args.check,
                reporter=reporter,
            )
        except (OSError, ValueError, TypeError) as error:
            print(f"bibreview providers: {error}", file=sys.stderr)
            return 1

        if args.json_output:
            print(
                json.dumps(
                    [item.data() for item in diagnostics],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if not args.quiet:
            print(format_provider_diagnostics(diagnostics))
        return 0

    if args.command == "hygiene":
        if args.apply:
            try:
                plan = plan_project_hygiene_apply(config)
                if not args.dry_run:
                    apply_project_hygiene_apply(plan)
            except (
                OSError,
                StorageError,
                ProjectStateError,
                ValueError,
                TypeError,
            ) as error:
                print(f"bibreview hygiene: {error}", file=sys.stderr)
                return 1

            payload = {
                "dry_run": bool(args.dry_run),
                "changed": plan.changed,
                "changes": [
                    {
                        "publication_id": item.publication_id,
                        "doi": item.doi,
                        "title": item.title,
                        "decision": item.decision,
                        "before": item.before,
                        "after": item.after,
                    }
                    for item in plan.changes
                ],
            }
            if args.json_output:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            elif not args.quiet:
                prefix = "Dry run: " if args.dry_run else ""
                print(prefix + plan.summary())
                if plan.changed:
                    print(f"Staging: {config.paths.collected}")
                else:
                    print("No accepted hygiene changes to stage.")
            return 0

        if args.resolve:
            try:
                return _run_hygiene_resolution(config, args)
            except (
                OSError,
                StorageError,
                ProjectStateError,
                ValueError,
                TypeError,
            ) as error:
                print(f"bibreview hygiene: {error}", file=sys.stderr)
                return 1

        if args.review:
            try:
                review = load_project_hygiene_review(config)
            except (
                OSError,
                StorageError,
                ProjectStateError,
                ValueError,
                TypeError,
            ) as error:
                print(f"bibreview hygiene: {error}", file=sys.stderr)
                return 1

            if args.json_output:
                print(json.dumps(review.data(), ensure_ascii=False, indent=2))
            elif not args.quiet:
                print(
                    format_project_hygiene_review(
                        review,
                        verbose=bool(args.verbose),
                    )
                )
            return 0

        if args.propose:
            try:
                plan = plan_project_hygiene_proposals(config)
                if not args.dry_run:
                    apply_project_hygiene_proposals(plan)
            except (
                OSError,
                StorageError,
                ProjectStateError,
                ValueError,
                TypeError,
            ) as error:
                print(f"bibreview hygiene: {error}", file=sys.stderr)
                return 1

            if args.json_output:
                print(json.dumps(plan.review.data(), ensure_ascii=False, indent=2))
            elif not args.quiet:
                prefix = "Dry run: " if args.dry_run else ""
                print(prefix + plan.summary())
                print(f"Review: {hygiene_review_path(config)}")
            return 0

        try:
            report = project_abstract_hygiene(config)
        except (OSError, StorageError, ValueError, TypeError) as error:
            print(f"bibreview hygiene: {error}", file=sys.stderr)
            return 1

        if args.json_output:
            print(json.dumps(report.data(), ensure_ascii=False, indent=2))
        elif not args.quiet:
            print(
                format_abstract_hygiene_report(
                    report,
                    verbose=bool(args.verbose),
                )
            )
        return 0

    if args.command == "audit":
        reporter = Reporter(-1 if args.quiet else args.verbose)

        if args.apply:
            try:
                if args.batch_size is not None:
                    raise ProjectStateError(
                        "--batch-size cannot be used with --apply"
                    )
                apply_plan = plan_project_audit_apply(config)
                if not args.dry_run:
                    apply_project_audit_apply(apply_plan)
            except (
                OSError,
                StorageError,
                ProjectStateError,
                ValueError,
                TypeError,
            ) as error:
                print(f"bibreview audit: {error}", file=sys.stderr)
                return 1

            payload = {
                "dry_run": bool(args.dry_run),
                "changed": apply_plan.changed,
                "staging": str(config.paths.collected),
                "bibtex_backups": [
                    str(path) for path in apply_plan.bibtex_backups
                ],
                **apply_plan.data(),
            }
            if args.json_output:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            elif not args.quiet:
                prefix = "Dry run: " if args.dry_run else ""
                report = (
                    format_project_audit_apply_plan(apply_plan)
                    if args.verbose
                    else apply_plan.summary()
                )
                print(prefix + report)
                print(f"Staging: {config.paths.collected}")
                for backup in apply_plan.bibtex_backups:
                    label = (
                        "Would create BibTeX backup"
                        if args.dry_run
                        else "BibTeX backup"
                    )
                    print(f"{label}: {backup}")
            return 0

        if args.resolve:
            try:
                return _run_audit_resolution(config, args)
            except (OSError, StorageError, ProjectStateError, ValueError, TypeError) as error:
                print(f"bibreview audit: {error}", file=sys.stderr)
                return 1

        if args.review:
            try:
                if args.batch_size is not None:
                    raise ProjectStateError(
                        "--batch-size cannot be used with --review"
                    )
                review = project_audit_review(config)
            except (OSError, StorageError, ProjectStateError, ValueError, TypeError) as error:
                print(f"bibreview audit: {error}", file=sys.stderr)
                return 1

            if args.json_output:
                print(json.dumps(review.data(), ensure_ascii=False, indent=2))
            elif not args.quiet:
                print(
                    format_project_audit_review(review)
                    if args.verbose
                    else review.summary()
                )
            return 0

        if args.reclassify:
            try:
                if args.batch_size is not None:
                    raise ProjectStateError(
                        "--batch-size cannot be used with --reclassify"
                    )
                reclassify_plan = plan_project_audit_reclassify(config)
                if not args.dry_run:
                    apply_project_audit_plan(reclassify_plan)
            except (OSError, StorageError, ProjectStateError, ValueError, TypeError) as error:
                print(f"bibreview audit: {error}", file=sys.stderr)
                return 1

            payload = {
                "dry_run": bool(args.dry_run),
                "changed": reclassify_plan.changed,
                "report": str(config.audit.report),
                **reclassify_plan.summary_metrics.data(),
            }
            if args.json_output:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            elif not args.quiet:
                prefix = "Dry run: " if args.dry_run else ""
                print(prefix + reclassify_plan.summary())
                print(f"Report: {config.audit.report}")
            return 0

        try:
            plan = plan_project_audit_batch(
                config,
                batch_size=args.batch_size,
                full=args.full,
            )
            if args.dry_run:
                payload = {
                    "dry_run": True,
                    "batch_id": plan.batch.id if plan.batch is not None else None,
                    "keys": list(plan.batch.keys) if plan.batch is not None else [],
                    "progress": _audit_progress_data(plan.campaign),
                    "campaign": str(config.audit.campaign),
                    "report": str(config.audit.report),
                }
                if args.json_output:
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                elif not args.quiet:
                    print(f"Dry run: {plan.summary()}")
                    if plan.batch is not None:
                        print(
                            f"Would audit {len(plan.batch.keys)} publication(s) "
                            f"in {plan.batch.id}."
                        )
                return 0

            apply_project_audit_plan(plan)
            if plan.batch is None:
                progress = campaign_progress(plan.campaign)
                payload = {
                    "batch_id": None,
                    "processed": 0,
                    "progress": _audit_progress_data(plan.campaign),
                    "campaign": str(config.audit.campaign),
                    "report": str(config.audit.report),
                }
                if args.json_output:
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                elif not args.quiet:
                    print("Audit campaign complete.")
                    print(progress.summary())
                    print(f"Report: {config.audit.report}")
                return 0

            services = build_audit_services(config, reporter=reporter)
            execution = execute_project_audit_batch(
                config,
                batch_id=plan.batch.id,
                sources=services.sources,
                reporter=reporter,
            )
        except (OSError, StorageError, ProjectStateError, ValueError, TypeError) as error:
            print(f"bibreview audit: {error}", file=sys.stderr)
            return 1

        payload = {
            "batch_id": execution.batch_id,
            "processed": execution.processed_count,
            "completed": execution.completed_count,
            "retryable": execution.retryable_count,
            "failed": execution.failed_count,
            "progress": _audit_progress_data(execution.campaign),
            "campaign": str(config.audit.campaign),
            "report": str(config.audit.report),
        }
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif not args.quiet:
            print(execution.summary())
            print(f"Report: {config.audit.report}")
        return 0

    if args.command == "discover":
        reporter = Reporter(-1 if args.quiet else args.verbose)
        try:
            services = build_discovery_services(config, reporter=reporter)
            plan = plan_project_discovery(
                config,
                discovery_provider=services.discovery_provider,
                provider=services.provider,
                enrichment_lookup=services.enrichment_lookup,
                reporter=reporter,
            )
            if args.dry_run:
                if not args.quiet:
                    print(f"Dry run: {plan.summary()}")
                return 0
            apply_project_discovery(plan)
        except (OSError, StorageError, ValueError, TypeError) as error:
            print(f"bibreview discover: {error}", file=sys.stderr)
            return 1
        if not args.quiet:
            print(plan.summary())
            if not plan.changed:
                print("No new discovery state changes.")
        return 0

    if args.command == "collect":
        reporter = Reporter(-1 if args.quiet else args.verbose)
        try:
            services = build_collection_services(config, reporter=reporter)
            plan = plan_project_collection(
                config,
                provider=services.provider,
                enrichment_lookup=services.enrichment_lookup,
                citation_lookup=services.citation_lookup,
                bibtex_lookup=services.bibtex_lookup,
                reporter=reporter,
            )
            if args.dry_run:
                if not args.quiet:
                    print(f"Dry run: {plan.summary()}")
                return 0
            apply_project_collection(plan)
        except (OSError, StorageError, ValueError, TypeError) as error:
            print(f"bibreview collect: {error}", file=sys.stderr)
            return 1
        if not args.quiet:
            print(plan.summary())
            if not plan.changed:
                print("No pending DOI state changes.")
        return 0

    if args.command == "backfill":
        if args.resolve:
            try:
                return _run_backfill_resolution(config, args)
            except (
                OSError,
                StorageError,
                ProjectStateError,
                ValueError,
                TypeError,
            ) as error:
                print(f"bibreview backfill: {error}", file=sys.stderr)
                return 1

        if args.apply:
            try:
                if args.backfill_fields or args.backfill_types:
                    raise ProjectStateError(
                        "--field/--type cannot be used with backfill --apply"
                    )
                plan = plan_project_backfill_apply(config)
                if not args.dry_run:
                    apply_project_backfill_apply(plan)
            except (
                OSError,
                StorageError,
                ProjectStateError,
                ValueError,
                TypeError,
            ) as error:
                print(f"bibreview backfill: {error}", file=sys.stderr)
                return 1

            if not args.quiet:
                prefix = "Dry run: " if args.dry_run else ""
                print(prefix + plan.summary())
                if plan.changed:
                    print(f"Staging: {config.paths.collected}")
                else:
                    print("No accepted backfill changes to stage.")
            return 0

        if not args.backfill_fields:
            print(
                "bibreview backfill: at least one --field is required "
                "when generating proposals",
                file=sys.stderr,
            )
            return 1

        reporter = Reporter(-1 if args.quiet else args.verbose)
        try:
            services = build_collection_services(config, reporter=reporter)
            plan = plan_project_backfill(
                config,
                provider=services.provider,
                fields=tuple(args.backfill_fields),
                types=tuple(args.backfill_types),
                enrichment_lookup=services.enrichment_lookup,
                batch_provider=services.batch_provider,
                enrichment_many_lookup=services.enrichment_many_lookup,
                reporter=reporter,
            )
            if not args.dry_run:
                apply_project_backfill_plan(plan)
        except (
            OSError,
            StorageError,
            ProjectStateError,
            ValueError,
            TypeError,
        ) as error:
            print(f"bibreview backfill: {error}", file=sys.stderr)
            return 1

        if not args.quiet:
            prefix = "Dry run: " if args.dry_run else ""
            print(prefix + plan.summary())
            print(f"Review: {config.audit.report.with_name('backfill.json')}")
            if not plan.review.candidates:
                print("No missing-field proposals available.")
        return 0

    if args.command == "refresh":
        if args.review:
            try:
                review = load_project_refresh_review(config)
            except (
                OSError,
                StorageError,
                ProjectStateError,
                ValueError,
                TypeError,
            ) as error:
                print(f"bibreview refresh: {error}", file=sys.stderr)
                return 1
            if not args.quiet:
                print(
                    format_project_refresh_review(
                        review,
                        verbose=bool(args.verbose),
                    )
                )
            return 0

        if args.resolve:
            try:
                return _run_refresh_resolution(config, args)
            except (
                OSError,
                StorageError,
                ProjectStateError,
                ValueError,
                TypeError,
            ) as error:
                print(f"bibreview refresh: {error}", file=sys.stderr)
                return 1

        if args.apply:
            try:
                plan = plan_project_refresh_apply(config)
                if not args.dry_run:
                    apply_project_refresh_apply(plan)
            except (
                OSError,
                StorageError,
                ProjectStateError,
                ValueError,
                TypeError,
            ) as error:
                print(f"bibreview refresh: {error}", file=sys.stderr)
                return 1

            if not args.quiet:
                prefix = "Dry run: " if args.dry_run else ""
                print(prefix + plan.summary())
                if plan.changed:
                    print(f"Staging: {config.paths.collected}")
                for backup in plan.bibtex_backups:
                    label = (
                        "Would create BibTeX backup"
                        if args.dry_run
                        else "BibTeX backup"
                    )
                    print(f"{label}: {backup}")
                if not plan.changed:
                    print("No accepted refresh changes to stage.")
            return 0

        reporter = Reporter(-1 if args.quiet else args.verbose)
        try:
            services = build_collection_services(config, reporter=reporter)
            plan = plan_project_refresh(
                config,
                provider=services.provider,
                enrichment_lookup=services.enrichment_lookup,
                citation_lookup=services.citation_lookup,
                bibtex_lookup=services.bibtex_lookup,
                reporter=reporter,
            )
            if not args.dry_run:
                apply_project_refresh(plan)
        except (
            OSError,
            StorageError,
            ProjectStateError,
            ValueError,
            TypeError,
        ) as error:
            print(f"bibreview refresh: {error}", file=sys.stderr)
            return 1

        if not args.quiet:
            prefix = "Dry run: " if args.dry_run else ""
            print(prefix + plan.summary())
            print(f"Review: {refresh_review_path(config)}")
            if plan.review.collateral:
                print(
                    "Collateral provider differences were retained for review "
                    "and will never be auto-applied."
                )
        return 0

    if args.command == "authors":
        try:
            plan = plan_project_author_mappings(config, apply_safe=args.apply_safe)
            if args.apply_safe and not args.dry_run:
                apply_project_author_mappings(plan)
        except (OSError, StorageError, ValueError, TypeError) as error:
            print(f"bibreview authors: {error}", file=sys.stderr)
            return 1

        report_plan = plan.before if args.dry_run else plan.after
        applied = plan.applied_count if args.apply_safe else 0
        if args.json_output:
            if not args.quiet or args.json_output:
                payload = {
                    "applied": 0 if args.dry_run else applied,
                    "would_apply": applied if args.dry_run else 0,
                    "dry_run": args.dry_run,
                    **author_mapping_plan_data(report_plan),
                }
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if not args.quiet:
            report = format_author_mapping_plan(
                report_plan,
                applied=applied,
                dry_run=args.dry_run,
            )
            print(f"Dry run:\n{report}" if args.dry_run else report)
        return 0

    if args.command == "merge":
        try:
            plan = plan_project_merge(config)
            if args.dry_run:
                if not args.quiet:
                    print(f"Dry run: {plan.summary()}")
                    if plan.backup is not None:
                        print(f"Would create backup: {plan.backup}")
                return 0
            apply_project_merge(plan)
        except (OSError, StorageError, MergeError, ProjectStateError) as error:
            print(f"bibreview merge: {error}", file=sys.stderr)
            return 1
        if not args.quiet:
            print(plan.summary())
            if plan.backup is not None:
                print(f"Backup: {plan.backup}")
            if not plan.changed:
                print("No collected publications; project state unchanged.")
        return 0

    if args.command == "render":
        try:
            plan = plan_project_render(config)
            if args.dry_run:
                if not args.quiet:
                    print(f"Dry run: {plan.summary()}")
                    for orphan in plan.orphan_bibtex:
                        print(f"Unused BibTeX: {orphan}")
                return 0
            apply_project_render(plan)
        except (OSError, StorageError, ValueError, TypeError) as error:
            print(f"bibreview render: {error}", file=sys.stderr)
            return 1
        if not args.quiet:
            print(plan.summary())
            for orphan in plan.orphan_bibtex:
                print(f"Unused BibTeX: {orphan}")
            if not plan.changed:
                print("Rendered site artifacts already up to date.")
        return 0

    if args.command == "arxiv":
        reporter = Reporter(-1 if args.quiet else args.verbose)
        try:
            plan = plan_project_arxiv(config)
            if args.dry_run:
                if not args.quiet:
                    print(f"Dry run: {plan.summary()}")
                return 0
            apply_project_arxiv(plan)
        except TemporaryArxivError as error:
            reporter.warning(
                f"{error}; keeping the existing arXiv cache unchanged"
            )
            return 0
        except (OSError, ArxivError, StorageError, ValueError, TypeError) as error:
            print(f"bibreview arxiv: {error}", file=sys.stderr)
            return 1
        if not args.quiet:
            print(plan.summary())
            if not plan.changed:
                print("arXiv cache already up to date.")
        return 0

    raise AssertionError("unreachable")
