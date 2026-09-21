"""BibReview command-line interface."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .arxiv import ArxivError, TemporaryArxivError
from .config import ConfigError, load_config
from .pipeline.authors import author_mapping_plan_data, format_author_mapping_plan
from .provider_diagnostics import diagnose_providers, format_provider_diagnostics
from .pipeline.merge import MergeError
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
from .project_refresh import apply_project_refresh, plan_project_refresh
from .project_render import apply_project_render, plan_project_render
from .reporting import Reporter
from .runtime import build_collection_services, build_discovery_services
from .storage import StorageError


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
    commands.add_parser("discover", help="Discover and screen new DOI candidates")
    commands.add_parser("collect", help="Collect pending DOI metadata into canonical staging state")
    commands.add_parser("refresh", help="Recollect stale existing publications into canonical staging state")
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

    if args.command == "refresh":
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
            if args.dry_run:
                if not args.quiet:
                    print(f"Dry run: {plan.summary()}")
                    for backup in plan.bibtex_backups:
                        print(f"Would create BibTeX backup: {backup}")
                return 0
            apply_project_refresh(plan)
        except (OSError, StorageError, ValueError, TypeError) as error:
            print(f"bibreview refresh: {error}", file=sys.stderr)
            return 1
        if not args.quiet:
            print(plan.summary())
            for backup in plan.bibtex_backups:
                print(f"BibTeX backup: {backup}")
            if not plan.changed:
                print("No refresh state changes.")
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
