"""Small explicit CLI; importing the package never starts a model or a container."""

import argparse
import json
import os
import subprocess
from pathlib import Path

import httpx

from .config import V2_SHA, Config


def main(argv=None):
    parser = argparse.ArgumentParser(description="Same-family Decisions supervision on SWE-bench Pro V2")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch-v2", help="Download the fixed official V2 task checkout, no inference")
    fetch.add_argument("--dest", required=True, type=Path)
    for name in ("validate-config", "doctor", "run"):
        command = commands.add_parser(name)
        command.add_argument("--config", required=True, type=Path)
        if name == "run":
            command.add_argument("--checkout", required=True, type=Path)
            command.add_argument("--instance-id", required=True)
            command.add_argument("--arm", choices=["A_r1", "A_r2", "A_r3", "B", "C"], required=True)
            command.add_argument("--output", required=True, type=Path)
    grade = commands.add_parser("grade", help="Replay one sealed patch in a fresh V2 container")
    grade.add_argument("--checkout", required=True, type=Path)
    grade.add_argument("--output", required=True, type=Path)
    alias = commands.add_parser("alias-c", help="Reference predeclared A_r1 without another model call")
    alias.add_argument("--source", required=True, type=Path)
    alias.add_argument("--output", required=True, type=Path)
    summary = commands.add_parser("summarize")
    summary.add_argument("--runs", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "fetch-v2":
            if args.dest.exists():
                raise ValueError("Choose a new destination; existing files are never replaced")
            subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout",
                            "https://github.com/scaleapi/SWE-bench_Pro-os.git", str(args.dest)], check=True)
            subprocess.run(["git", "-C", str(args.dest), "checkout", "--detach", V2_SHA], check=True)
            result = {"checkout": str(args.dest.resolve()), "sha": V2_SHA}
        elif args.command == "grade":
            from .grade import grade
            result = grade(checkout=args.checkout, output=args.output)
        elif args.command == "alias-c":
            from .runner import alias_c
            result = alias_c(args.source, args.output)
        elif args.command == "summarize":
            from .analysis import summarize
            result = summarize(args.runs)
        else:
            cfg = Config.load(args.config)
            if args.command == "validate-config":
                result = {"valid": True, "config": cfg.snapshot(), "inference_started": False}
            elif args.command == "doctor":
                from .environment import docker
                from .router import fetch_catalog
                with httpx.Client() as client:
                    catalog = fetch_catalog(client, cfg)
                result = {"inference_started": False, "api_key_present": bool(os.getenv(cfg.api_key_env)),
                          "catalog_models": list(catalog),
                          "docker": docker("info", "--format", "{{.OSType}}").stdout.strip()}
                if result["docker"] != "linux":
                    raise ValueError("Docker must use Linux containers")
            else:
                from .runner import run
                result = run(cfg, checkout=args.checkout, instance_id=args.instance_id,
                             output=args.output, arm=args.arm)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "run" and not result.get("patch_sealed"):
            return 2
        if args.command == "grade" and result["resolved"] is None:
            return 2
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, httpx.HTTPError) as exc:
        # No secrets, HTTP request headers, or tracebacks in ordinary CLI errors.
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        return 2
