"""Command line for one pipeline stage at a time."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline.air import ai, case, derive, group, manifest, prep, publish, review, sync
from pipeline.air.paths import library_root


def _extract_library(argv: list[str]) -> tuple[str | None, list[str]]:
    library = None
    cleaned: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--library" and index + 1 < len(argv):
            library = argv[index + 1]
            index += 2
            continue
        if item.startswith("--library="):
            library = item.split("=", 1)[1]
            index += 1
            continue
        cleaned.append(item)
        index += 1
    return library, cleaned


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    library, raw = _extract_library(raw)
    parser = argparse.ArgumentParser(
        prog="air",
        epilog="--library PATH can be placed before or after the command. The default is AIR_LIBRARY or ./library.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    prep_parser = sub.add_parser("prep")
    prep_sub = prep_parser.add_subparsers(dest="prep_command", required=True)
    prep_sub.add_parser("init")
    prep_sub.add_parser("scan")

    sync_parser = sub.add_parser("sync")
    sync_parser.add_argument("--neon", action="store_true")

    ai_parser = sub.add_parser("ai")
    ai_sub = ai_parser.add_subparsers(dest="ai_command", required=True)
    ai_sub.add_parser("exif")
    ai_sub.add_parser("describe")
    ai_sub.add_parser("reason")
    ai_sub.add_parser("validate")
    synth = ai_sub.add_parser("synthesize")
    synth.add_argument("kind", choices=("video", "model"))

    review_parser = sub.add_parser("review")
    review_sub = review_parser.add_subparsers(dest="review_command", required=True)
    tag = review_sub.add_parser("tag")
    tag.add_argument("--name", required=True)
    tag.add_argument("--characteristic", action="append", default=[], metavar="KEY=VALUE")
    override = review_sub.add_parser("override")
    override.add_argument("--temp-id", required=True)
    override.add_argument("--tag", required=True)
    override.add_argument("--value", required=True)
    override.add_argument("--reason", required=True)

    group_parser = sub.add_parser("group")
    group_parser.add_argument("kind", choices=("location", "shots", "before-after", "overlap", "model"))

    case_parser = sub.add_parser("case")
    case_sub = case_parser.add_subparsers(dest="case_command", required=True)
    case_sub.add_parser("build")

    sub.add_parser("id")
    derive_parser = sub.add_parser("derive")
    derive_parser.add_argument("--watermark")
    rewatermark = sub.add_parser("rewatermark")
    rewatermark.add_argument("--watermark", required=True)
    rewatermark.add_argument("--compiled-id")
    sub.add_parser("manifest")
    sub.add_parser("publish")

    run = sub.add_parser("run")
    run.add_argument("--from", dest="start", default="prep")
    run.add_argument("--to", dest="end", required=True)

    args = parser.parse_args(raw)
    root = library_root(library)
    result = _dispatch(root, args)
    print(json.dumps(result, indent=2, default=str))
    return 0


def _dispatch(root: Path, args) -> dict:
    if args.command == "prep" and args.prep_command == "init":
        journal = prep.init_library(root)
        path = str(journal.path)
        journal.close()
        return {"library": str(root), "journal": path}
    if args.command == "prep" and args.prep_command == "scan":
        return prep.scan(root)
    if args.command == "sync":
        return sync.sync_library(root, neon=args.neon)
    if args.command == "ai" and args.ai_command == "exif":
        return ai.exif(root)
    if args.command == "ai" and args.ai_command == "describe":
        return ai.describe(root)
    if args.command == "ai" and args.ai_command == "reason":
        return ai.reason(root)
    if args.command == "ai" and args.ai_command == "validate":
        return ai.validate(root)
    if args.command == "ai" and args.ai_command == "synthesize":
        if args.kind == "video":
            return ai.synthesize_video(root)
        return ai.synthesize_model(root)
    if args.command == "review" and args.review_command == "tag":
        characteristics = {}
        for item in args.characteristic:
            key, _, value = item.partition("=")
            characteristics[key] = value
        return review.add_tag(root, args.name, characteristics)
    if args.command == "review" and args.review_command == "override":
        return review.override_tag(root, args.temp_id, args.tag, args.value, args.reason)
    if args.command == "group":
        return {
            "location": group.group_location,
            "shots": group.group_shots,
            "before-after": group.group_before_after,
            "overlap": group.group_overlap,
            "model": group.group_model,
        }[args.kind](root)
    if args.command == "case" and args.case_command == "build":
        return case.build_cases(root)
    if args.command == "id":
        return group.assign_ids(root)
    if args.command == "derive":
        return derive.derive(root, args.watermark)
    if args.command == "rewatermark":
        return derive.rewatermark(root, args.watermark, args.compiled_id)
    if args.command == "manifest":
        return manifest.build_manifest(root)
    if args.command == "publish":
        return publish.publish(root)
    if args.command == "run":
        return _run(root, args.start, args.end)
    raise SystemExit(f"unknown command: {args.command}")


CHAIN = (
    "prep",
    "sync",
    "exif",
    "describe",
    "reason",
    "validate",
    "synthesize-video",
    "group",
    "synthesize-model",
    "case",
    "id",
    "derive",
    "manifest",
    "publish",
)


def _run(root: Path, start: str, end: str) -> dict:
    if start not in CHAIN or end not in CHAIN:
        raise SystemExit(f"stages are: {', '.join(CHAIN)}")
    if CHAIN.index(start) > CHAIN.index(end):
        raise SystemExit("--from is after --to")
    selected = CHAIN[CHAIN.index(start) : CHAIN.index(end) + 1]
    results = {}
    for stage in selected:
        if stage == "prep":
            results[stage] = prep.scan(root)
        elif stage == "sync":
            results[stage] = sync.sync_library(root)
        elif stage == "exif":
            results[stage] = ai.exif(root)
        elif stage == "describe":
            results[stage] = ai.describe(root)
        elif stage == "reason":
            results[stage] = ai.reason(root)
        elif stage == "validate":
            results[stage] = ai.validate(root)
        elif stage == "synthesize-video":
            results[stage] = ai.synthesize_video(root)
        elif stage == "group":
            results[stage] = {
                "location": group.group_location(root),
                "shots": group.group_shots(root),
                "before_after": group.group_before_after(root),
                "overlap": group.group_overlap(root),
                "model": group.group_model(root),
            }
        elif stage == "synthesize-model":
            results[stage] = ai.synthesize_model(root)
        elif stage == "case":
            results[stage] = case.build_cases(root)
            results["sync_after_case"] = sync.sync_library(root)
        elif stage == "id":
            results[stage] = group.assign_ids(root)
        elif stage == "derive":
            results[stage] = derive.derive(root)
        elif stage == "manifest":
            results[stage] = manifest.build_manifest(root)
        elif stage == "publish":
            results[stage] = publish.publish(root)
    return results
