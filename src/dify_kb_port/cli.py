"""Command line entry point. Exit codes: 0 ok/identical, 1 differences, 2 usage/auth, 3 API/indexing."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from pathlib import Path
from typing import Mapping, Sequence

from . import __version__
from .client import DifyClient
from .demo import SEED_NAME, run_demo, seed_source
from .diff import Report, Snapshot, compare, snapshot_bundle, snapshot_instance
from .errors import ApiError, AuthError, ConnectError, IndexingError, PortError, UsageError
from .exporter import export_dataset
from .i18n import resolve_lang, set_lang, t
from .importer import import_bundle
from .mock_server import DEFAULT_KEY, MockDify


def _progress(key: str, **kw: object) -> None:
    print(t(key, **kw), flush=True)


def _client(env: Mapping[str, str], side: str) -> DifyClient:
    url, key = env.get(f"DIFY_{side}_API_URL", ""), env.get(f"DIFY_{side}_API_KEY", "")
    for var, value in ((f"DIFY_{side}_API_URL", url), (f"DIFY_{side}_API_KEY", key)):
        if not value:
            raise UsageError(t("err.missing_env", var=var))
    return DifyClient(url, key)


def print_report(report: Report, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report.to_json(), ensure_ascii=False, indent=2))
        return
    print(t("verify.header", left=report.left, right=report.right))
    print(t("verify.identical", identical=report.identical, total=report.total))
    for key, rows in (("verify.changed", report.changed), ("verify.missing", report.missing), ("verify.added", report.added)):
        if rows:
            print(t(key, count=len(rows)))
            for row in rows:
                if row.get("fields"):
                    print(t("verify.row_fields", fields=", ".join(row["fields"]), **{k: row[k] for k in ("document", "ordinal")}))
                else:
                    print(t("verify.row", document=row["document"], ordinal=row["ordinal"]))
    docs = report.documents
    for key, names in (("verify.docs_missing", docs["missing"]), ("verify.docs_added", docs["added"]),
                       ("verify.docs_changed", [c["document"] + "(" + ",".join(c["fields"]) + ")" for c in docs["changed"]])):
        if names:
            print(t(key, count=len(names), names=", ".join(names)))
    print(t("verify.ok" if report.ok else "verify.fail"))


def _side(spec: str, env: Mapping[str, str]) -> Snapshot:
    if spec.startswith(("src:", "dst:")):
        side, dataset_id = spec[:3].upper(), spec[4:]
        if not dataset_id:
            raise UsageError(t("err.bad_spec", spec=spec))
        with _client(env, side) as client:
            return snapshot_instance(client, dataset_id, spec)
    if not Path(spec).is_dir():
        raise UsageError(t("err.bad_spec", spec=spec))
    return snapshot_bundle(Path(spec))


def cmd_export(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    with _client(env, "SRC") as client:
        export_dataset(client, args.dataset, Path(args.out), force=args.force, progress=_progress)
    return 0


def cmd_import(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    with _client(env, "DST") as client:
        result = import_bundle(client, Path(args.bundle), name=args.name, into=args.into, on_conflict=args.on_conflict,
                               dry_run=args.dry_run, resume=args.resume, verify=not args.no_verify,
                               index_timeout=args.index_timeout, progress=_progress)
    if args.dry_run:
        return 0
    _progress("import.done", dataset=result.dataset_id)
    if result.report is None:
        return 0
    if result.report.total == 0 and result.skipped:
        # 모두 건너뛰었으면 "동일 0/0, 차이 없음"은 거짓 안심이다 — 검증한 게 없다고 말한다.
        print(t("import.nothing_verified"))
    else:
        print_report(result.report, as_json=False)
    if result.skipped_report is not None:
        differ = _documents_with_differences(result.skipped_report)
        if differ:
            print(t("import.kept_differ", count=len(differ), names=", ".join(differ)))
        else:
            print(t("import.kept_match", count=len(result.kept_existing), names=", ".join(result.kept_existing)))
    # 종료 코드는 이번에 가져온 문서만으로 정한다: 건너뛰기는 사용자가 고른 동작이다.
    return 0 if result.report.ok else 1


def _documents_with_differences(report: Report) -> list[str]:
    names = {row["document"] for row in report.changed + report.missing + report.added}
    names |= set(report.documents["missing"]) | set(report.documents["added"])
    names |= {c["document"] for c in report.documents["changed"]}
    return sorted(names)


def cmd_verify(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    left = snapshot_bundle(Path(args.bundle)) if Path(args.bundle).is_dir() else _raise_spec(args.bundle)
    with _client(env, "DST") as client:
        right = snapshot_instance(client, args.dataset, f"dataset {args.dataset}")
    report = compare(left, right)
    print_report(report, args.json)
    return 0 if report.ok else 1


def _raise_spec(spec: str) -> Snapshot:
    raise UsageError(t("err.bad_spec", spec=spec))


def cmd_diff(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    report = compare(_side(args.left, env), _side(args.right, env))
    print_report(report, args.json)
    return 0 if report.ok else 1


def cmd_demo(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return run_demo()


def cmd_mock_server(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    try:
        server = MockDify(port=args.port)
    except OSError as exc:  # port already in use or not allowed
        raise UsageError(t("err.port", port=args.port, detail=exc.strerror or str(exc))) from exc
    with server as mock:
        print(t("mock.listening", url=mock.url, api_key=DEFAULT_KEY), flush=True)
        if args.seed:
            # demo 와 같은 손편집 지식베이스를 HTTP 로 만들어 두면, 사용자는 출력된 id 로 바로
            # export -> import -> verify 를 연습할 수 있다(빈 서버는 404 로 끝난다).
            with DifyClient(mock.url, DEFAULT_KEY) as client:
                dataset_id = seed_source(client)
            print(t("mock.seeded", name=SEED_NAME, dataset=dataset_id), flush=True)
            print(t("mock.try", url=mock.url, api_key=DEFAULT_KEY, dataset=dataset_id), flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            print(t("mock.stopped"))
    return 0


def _positive_float(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if value <= 0:
        raise argparse.ArgumentTypeError(t("err.positive", value=raw))
    return value


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lang", metavar="{en,ko}", default=argparse.SUPPRESS, help=t("help.lang"))
    parser = argparse.ArgumentParser(prog="dify-kb-port", description=t("help.description"), epilog=t("help.epilog"),
                                     parents=[common])
    parser.add_argument("--version", action="version", version=f"dify-kb-port {__version__}")
    sub = parser.add_subparsers(dest="command")

    def add(name: str, helpkey: str, func) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=t(helpkey), description=t(helpkey), epilog=t("help.epilog"), parents=[common])
        p.set_defaults(func=func)
        return p

    p = add("export", "help.export", cmd_export)
    p.add_argument("--dataset", required=True, help=t("help.dataset"))
    p.add_argument("--out", required=True, help=t("help.out"))
    p.add_argument("--force", action="store_true", help=t("help.force"))

    p = add("import", "help.import", cmd_import)
    p.add_argument("bundle", help=t("help.bundle"))
    p.add_argument("--name", help=t("help.name"))
    p.add_argument("--into", help=t("help.into"))
    p.add_argument("--on-conflict", choices=("skip", "replace"), default="skip", help=t("help.on_conflict"))
    p.add_argument("--dry-run", action="store_true", help=t("help.dry_run"))
    p.add_argument("--resume", action="store_true", help=t("help.resume"))
    p.add_argument("--no-verify", action="store_true", help=t("help.no_verify"))
    p.add_argument("--index-timeout", type=_positive_float, default=600.0, help=t("help.index_timeout"))

    p = add("verify", "help.verify", cmd_verify)
    p.add_argument("bundle", help=t("help.bundle"))
    p.add_argument("--dataset", required=True, help=t("help.dataset"))
    p.add_argument("--json", action="store_true", help=t("help.json"))

    p = add("diff", "help.diff", cmd_diff)
    p.add_argument("left", help=t("help.left"))
    p.add_argument("right", help=t("help.right"))
    p.add_argument("--json", action="store_true", help=t("help.json"))

    add("demo", "help.demo", cmd_demo)
    p = add("mock-server", "help.mock_server", cmd_mock_server)
    p.add_argument("--port", type=int, default=5001, help=t("help.port"))
    p.add_argument("--seed", action="store_true", help=t("help.seed"))
    return parser


def _preparse_lang(argv: Sequence[str]) -> str | None:
    for i, arg in enumerate(argv):
        if arg == "--lang" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--lang="):
            return arg.split("=", 1)[1]
    return None


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    set_lang(resolve_lang(_preparse_lang(argv), env))
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return args.func(args, env)
    except AuthError as exc:
        print(t("err.prefix", message=t("err.auth", url=exc.url, status=exc.status, message=exc.message)), file=sys.stderr)
        return exc.exit_code
    except ConnectError as exc:
        # 상태 코드가 없는 실패(서버가 꺼짐·주소 오타)는 "HTTP 0" 대신 무엇을 확인할지 알려 준다.
        hint = t("err.resume_hint") if args.command == "import" else ""
        print(t("err.prefix", message=t("err.connect", url=exc.url, detail=exc.detail) + hint), file=sys.stderr)
        return exc.exit_code
    except (ApiError, IndexingError) as exc:
        hint = t("err.resume_hint") if args.command == "import" else ""
        print(t("err.prefix", message=t("err.api", message=str(exc)) + hint), file=sys.stderr)
        return exc.exit_code
    except PortError as exc:
        print(t("err.prefix", message=str(exc)), file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
