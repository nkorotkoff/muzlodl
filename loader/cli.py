"""CLI entry point."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .config import Config
from .doctor import run_doctor, TEST_FUNCTIONS
from .loaders import load_input
from .pipeline import Pipeline
from .sources.registry import default_sources, default_enrichers
from .state import get_or_detect, print_report, reset as reset_state, load_state
from .storage import BACKENDS, CloudConfig, make_storage
from .webdav import WebDAVClient, WebDAVError
from .yandex_oauth import (
    build_implicit_url, build_pkce_url, extract_token_from_redirect,
    exchange_code_for_token, generate_pkce_pair, test_token, YandexToken,
)
from .credentials import (
    load_cloud, save_cloud, clear_cloud, cloud_path,
    load_yandex_token, save_yandex_token, clear_yandex_token,
)

log = logging.getLogger(__name__)


def _setup_logging(verbose: bool, quiet: bool):
    if quiet:
        level = logging.WARNING
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_download(args):
    _setup_logging(args.verbose, args.quiet)
    cfg = Config.from_env()
    cfg.merge(
        output_dir=args.output,
        parallel=args.parallel,
        retries=args.retries,
        skip_existing=not args.no_skip,
        min_match_score=args.min_score,
        quality=args.quality,
        max_path_len=args.max_path_len,
        enrich=not args.no_enrich,
        upload_after_download=args.upload_after_download,
        delete_after_upload=args.delete_after_upload,
        acoustid_verify=args.verify_acoustid,
        acoustid_api_key=args.acoustid_api_key or os.environ.get("ACOUSTID_API_KEY", ""),
        acoustid_min_score=args.acoustid_min_score,
    )

    # Resolve sources: explicit > cached auto-detect > fresh auto-detect
    if args.sources:
        cfg.enabled_sources = args.sources
        chain_source = "cli"
    elif args.reset_cache:
        reset_state(Path(args.output))
        cfg.enabled_sources, _, _ = get_or_detect(
            cfg, Path(args.output), force=True,
            include_previews=args.include_previews,
        )
        chain_source = "auto (reset)"
    else:
        cfg.enabled_sources, state, was_fresh = get_or_detect(
            cfg, Path(args.output), force=args.auto_detect,
            include_previews=args.include_previews,
        )
        chain_source = "auto (cached)" if was_fresh else "auto (detected)"

    if args.enrichers is not None:
        cfg.enabled_enrichers = args.enrichers

    log.info("source chain (%s): %s", chain_source, cfg.enabled_sources)

    # Streaming upload: load cloud storage up front so each track can
    # be pushed the moment it's downloaded. Requires --upload-after-download.
    cloud = None
    if args.upload_after_download:
        cloud = load_cloud()
        if not cloud:
            log.error("--upload-after-download requires cloud-setup first")
            return 1
        try:
            cloud = make_storage(cloud)
        except WebDAVError as e:
            log.error(f"cloud init failed: {e}")
            return 1
        log.info("streaming upload: ON (delete-after=%s)",
                 "yes" if args.delete_after_upload else "no")

    tracks = load_input(args.input)
    if not tracks:
        print("no tracks loaded", file=sys.stderr)
        return 1

    sources = default_sources(cfg)
    enrichers = default_enrichers(cfg)
    pipe = Pipeline(cfg, sources, enrichers, cloud=cloud)
    pipe.process(tracks)

    if args.upload and not args.upload_after_download:
        # Legacy mode: just upload everything at the end
        if cloud is None:
            cloud = load_cloud()
        if cloud:
            try:
                storage = make_storage(cloud)
                storage.upload_library(Path(args.output))
            except WebDAVError as e:
                log.error(f"cloud upload failed: {e}")
                return 1
        else:
            log.error("no cloud storage configured; run `music-loader cloud-setup`")
            return 1
    return 0




def cmd_cloud_setup(args):
    """Set up a cloud storage backend (Yandex.Disk or Mail.ru via WebDAV)."""
    _setup_logging(args.verbose, args.quiet)
    print()
    print("=" * 70)
    print("Cloud storage setup")
    print("=" * 70)
    print()
    print("Choose where to host your music library:")
    print()
    keys = list(BACKENDS.keys())
    for i, k in enumerate(keys, 1):
        b = BACKENDS[k]
        print(f"  [{i}] {b['name']}")
    print()
    try:
        choice = input(f"  choice [1-{len(keys)}, default=1]: ").strip() or "1"
        idx = int(choice) - 1
        backend = keys[idx]
    except (ValueError, IndexError, EOFError, KeyboardInterrupt):
        print("aborted")
        return 1

    b = BACKENDS[backend]
    print()
    print(f"Setting up {b['name']}.")
    print()
    print(b["howto"])
    print()

    # yandex_rest uses an OAuth token, not a password
    if backend == "yandex_rest":
        token = load_yandex_token()
        if not token:
            print("  No Yandex OAuth token saved yet.")
            print("  Run: music-loader yandex-oauth")
            return 1
        if not test_token(token):
            print("  ERROR: saved token doesn't work. Re-run: music-loader yandex-oauth")
            return 1
        config = CloudConfig(backend=backend, password=token, root=b["root"])
        save_cloud(config)
        print(f"  OK — token valid, will upload to /{b['root']}/")
        print(f"  saved to {cloud_path()}")
        return 0

    try:
        login = input("  login (email or username): ").strip()
        if not login:
            print("aborted")
            return 1
        import getpass
        password = getpass.getpass("  app password (input hidden): ").strip()
        if not password:
            print("aborted")
            return 1
    except (EOFError, KeyboardInterrupt):
        print("\naborted")
        return 1

    print()
    print("Testing connection...")
    try:
        client = WebDAVClient(b["endpoint"], login, password)
        if not client.exists("/"):
            print(f"  ERROR: cannot access {b['endpoint']}")
            print("  check your login and app password")
            return 1
        client.mkdir(b["root"])
        print(f"  OK — connected, root folder '{b['root']}' ready")
    except WebDAVError as e:
        print(f"  ERROR: {e}")
        return 1

    config = CloudConfig(
        backend=backend,
        login=login,
        password=password,
        root=b["root"],
    )
    save_cloud(config)
    print(f"  saved to {cloud_path()}")
    return 0


def cmd_cloud_status(args):
    _setup_logging(args.verbose, False)
    config = load_cloud()
    if not config:
        print("no cloud storage configured; run `music-loader cloud-setup`")
        return 1
    b = BACKENDS.get(config.backend, {})
    print(f"backend:  {b.get('name', config.backend)}")
    print(f"endpoint: {b.get('endpoint', '?')}")
    print(f"login:    {config.login or '(OAuth token)'}")
    print(f"root:     /{config.root}")
    print(f"file:     {cloud_path()}")

    # Test connection — different backends use different clients
    if config.backend == "yandex_rest":
        from .yandex_oauth import test_token
        if test_token(config.password):
            print("status:   reachable (token valid)")
        else:
            print("status:   NOT REACHABLE (token may have been revoked)")
        return 0

    try:
        client = WebDAVClient(b.get("endpoint"), config.login, config.password)
        if client.exists("/"):
            print("status:   reachable")
        else:
            print("status:   NOT REACHABLE (token may have been revoked)")
    except WebDAVError as e:
        print(f"status:   ERROR ({e})")
    return 0


def cmd_cloud_logout(args):
    _setup_logging(args.verbose, False)
    if clear_cloud():
        print("cloud credentials cleared")
    else:
        print("no cloud credentials to clear")
    return 0


def cmd_cloud_test(args):
    """Test upload a single file to the configured cloud."""
    _setup_logging(args.verbose, False)
    config = load_cloud()
    if not config:
        print("no cloud storage configured; run `music-loader cloud-setup`")
        return 1
    src = Path(args.file)
    if not src.exists():
        print(f"file not found: {src}")
        return 1
    storage = make_storage(config)
    remote = f"{config.root}/__test__/{_safe(src.name)}"
    try:
        storage.client.upload_streaming(src, remote)
        print(f"  uploaded to {remote}")
        print(f"  test: open {BACKENDS[config.backend]['name']} app and check /{config.root}/__test__/")
    except WebDAVError as e:
        print(f"  ERROR: {e}")
        return 1
    return 0


def _safe(name: str) -> str:
    import re
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    s = s.strip(" ._") or "_"
    return s[:200]


def cmd_upload(args):
    _setup_logging(args.verbose, False)
    cloud = load_cloud()
    if not cloud:
        log.error("no cloud storage configured; run `music-loader cloud-setup`")
        return 1
    try:
        storage = make_storage(cloud)
        storage.upload_library(Path(args.library))
    except WebDAVError as e:
        log.error(f"cloud upload failed: {e}")
        return 1
    return 0


def cmd_verify(args):
    """Verify library files against AcoustID/MusicBrainz.

    Fingerprints every audio file and looks it up in AcoustID to find
    tracks where the metadata matches but the actual sound is wrong
    (fan-uploads, covers, previews, etc.).
    """
    from .verifier import main as _verify_main
    import sys
    argv = ["verify"]
    if args.library and args.library != "./library":
        argv.append(str(args.library))
    if args.api_key:
        argv += ["--api-key", args.api_key]
    if args.min_score != 0.5:
        argv += ["--min-score", str(args.min_score)]
    if args.delete:
        argv.append("--delete")
    if args.delete_previews:
        argv.append("--delete-previews")
    if args.workers != 4:
        argv += ["--workers", str(args.workers)]
    if args.limit:
        argv += ["--limit", str(args.limit)]
    old_argv = sys.argv
    try:
        sys.argv = argv
        return _verify_main()
    finally:
        sys.argv = old_argv


def cmd_web(args):
    """Start the web UI."""
    _setup_logging(args.verbose, args.quiet)
    host = args.host or "127.0.0.1"
    port = args.port or 8080
    if args.debug and host != "127.0.0.1":
        # Werkzeug debugger is an RCE vector; never expose it beyond loopback.
        log.warning("debug mode with host=%s is dangerous — forcing 127.0.0.1", host)
        host = "127.0.0.1"
    log.info("starting web UI at http://%s:%d", host, port)
    from .web import create_app
    app = create_app()
    if host not in ("0.0.0.0", "::", "::0"):
        import webbrowser
        webbrowser.open(f"http://{host}:{port}")
    app.run(host=host, port=port, debug=args.debug)


def cmd_yandex_oauth(args):
    """Run the Yandex OAuth flow to get a Disk REST API token.

    Tries implicit flow first (simpler, no PKCE). If the app doesn't
    support implicit (some newer apps do not), falls back to PKCE.
    """
    _setup_logging(args.verbose, args.quiet)
    print()
    print("=" * 70)
    print("Yandex.Disk OAuth setup (REST API)")
    print("=" * 70)
    print()
    print("You'll need:")
    print("  1. A Yandex app registered at https://oauth.yandex.ru/")
    print("     - 'Web services' type")
    print("     - Redirect URI: https://oauth.yandex.com/verification_code")
    print("     - Scopes: cloud_api:disk.app, cloud_api:disk.read, cloud_api:disk.write")
    print("  2. The client_id from that app")
    print()

    if args.token:
        # Non-interactive: just save the token
        token = args.token.strip()
        if not token:
            print("--token is empty", file=sys.stderr)
            return 1
        if not test_token(token):
            print("ERROR: token doesn't work (check scopes)", file=sys.stderr)
            return 1
        save_yandex_token(token)
        print(f"OK — saved to {cloud_path()}")
        print("Now run: music-loader cloud-setup, choose [1] Yandex.Disk (WebDAV),")
        print("or set backend=yandex_rest manually. Or just run download with")
        print("--upload-after-download and streaming will use the REST API.")
        return 0

    try:
        client_id = input("  client_id: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\naborted")
        return 1
    if not client_id:
        print("aborted")
        return 1

    # Try implicit flow first
    url = build_implicit_url(client_id)
    print()
    print("STEP 1: open this URL in any browser, log in, click 'Allow'.")
    print("You'll be redirected to a page that says 'permission granted' or")
    print("shows the access_token. Copy the FULL URL from the address bar.")
    print()
    print(f"  {url}")
    print()
    try:
        redirect = input("  paste the redirect URL: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\naborted")
        return 1
    if not redirect:
        print("aborted")
        return 1

    token_obj = extract_token_from_redirect(redirect)
    if not token_obj:
        print()
        print("Implicit flow failed (token not in URL fragment).")
        print("Your app may require PKCE. Trying the authorization_code flow...")

        verifier, challenge = generate_pkce_pair()
        url2 = build_pkce_url(client_id, challenge)
        print()
        print("STEP 2 (PKCE): open this URL, click Allow, copy the full URL.")
        print()
        print(f"  {url2}")
        print()
        try:
            redirect2 = input("  paste the redirect URL: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\naborted")
            return 1
        if not redirect2:
            print("aborted")
            return 1

        # Extract authorization code from URL
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(redirect2)
        qs = parse_qs(parsed.query)
        code = qs.get("code", [None])[0]
        if not code:
            # Also try fragment
            qs2 = parse_qs(parsed.fragment)
            code = qs2.get("code", [None])[0]
        if not code:
            print(f"ERROR: no authorization code in {redirect2[:100]}")
            return 1

        # Need client_secret for PKCE token exchange
        try:
            import getpass
            client_secret = getpass.getpass("  client_secret (input hidden): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\naborted")
            return 1
        if not client_secret:
            print("aborted")
            return 1
        try:
            token_obj = exchange_code_for_token(
                client_id, client_secret, code, verifier
            )
        except Exception as e:
            print(f"  token exchange failed: {e}")
            return 1

    if not token_obj or not token_obj.access_token:
        print("ERROR: failed to obtain token")
        return 1

    # Test
    print()
    print("Testing the token...")
    if not test_token(token_obj.access_token):
        print("  ERROR: token doesn't work (check scopes in your Yandex app)")
        return 1
    print("  OK")

    # Save
    save_yandex_token(token_obj.access_token)
    print()
    print("=" * 70)
    print(f"  DONE! token saved to {cloud_path()}")
    print(f"  expires in: ~{token_obj.expires_in} sec ({token_obj.expires_in // 86400} days)")
    print()
    print("  Next: music-loader cloud-setup with backend=yandex_rest,")
    print("  OR just run download with --upload-after-download and it'll")
    print("  automatically use the REST API.")
    print("=" * 70)
    return 0


def cmd_sources(args):
    _setup_logging(False, False)
    cfg = Config.from_env()
    sources = default_sources(cfg)
    print(f"available sources ({len(sources)}):")
    for s in sources:
        print(f"  - {s.name}: {s.__class__.__name__}")
    return 0


def cmd_doctor(args):
    """Test reachability of all known sources."""
    _setup_logging(args.verbose, args.quiet)
    cfg = Config.from_env()
    if args.reset:
        reset_state(Path(args.output))
    log.info("testing sources (this takes ~30-60 sec)...")
    results = run_doctor(cfg, only=args.only)
    # Print as a table
    print()
    print("=" * 78)
    print(f"{'Source':<14} {'Status':<14} {'Search':<8} {'Download':<10} {'Latency':<10} Note")
    print("-" * 78)
    for name, h in sorted(results.items()):
        avail = "yes" if h.available else "no"
        dl = "yes" if h.can_download else "no"
        ms = f"{h.latency_ms}ms"
        note = h.reason or h.extras.get("note", "")
        print(f"{name:<14} {h.status:<14} {avail:<8} {dl:<10} {ms:<10} {note}")
    print("=" * 78)
    from .doctor import pick_default_chain
    chain = pick_default_chain(results)
    if not args.include_previews:
        chain = [n for n in chain if n != "itunes"]
    print(f"Recommended chain ({len(chain)}):")
    for n in chain:
        marker = "  ✓" if results[n].can_download else "  ◐"
        print(f"{marker} {n}")
    print()
    print(f"Run `music-loader download tracks.csv -o {args.output}` to use this chain.")
    print(f"State is cached in {args.output}/.loader-state.json (refreshes every 1h).")
    return 0


def cmd_reset(args):
    """Clear cached source health state."""
    _setup_logging(args.verbose, False)
    if reset_state(Path(args.output)):
        print(f"removed {args.output}/.loader-state.json")
    else:
        print("no cached state to remove")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="music-loader",
        description="Bulk music downloader with source fallback, then cloud upload.",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_dl = sub.add_parser("download", help="download tracks from a list")
    p_dl.add_argument("input", help="CSV/JSON/TXT file or Spotify URL")
    p_dl.add_argument("-o", "--output", default="./library",
                      help="output directory (default: ./library)")
    p_dl.add_argument("--sources", nargs="+",
                      help="explicit source list (skips auto-detect)")
    p_dl.add_argument("-p", "--parallel", type=int, default=1,
                      help="parallel downloads (default: 1)")
    p_dl.add_argument("--retries", type=int, default=1,
                      help="retries per source (default: 1)")
    p_dl.add_argument("--no-skip", action="store_true",
                      help="re-download existing files")
    p_dl.add_argument("--min-score", type=float, default=0.6,
                      help="min match score 0..1 (default: 0.6)")
    p_dl.add_argument("--quality", default="320", help="MP3 kbps (default: 320)")
    p_dl.add_argument("--max-path-len", type=int, default=0,
                      help="max relative path length (0=unlimited, e.g. 100 for Android)")
    p_dl.add_argument("--enrichers", nargs="+",
                      help="enricher chain, e.g. itunes musicbrainz")
    p_dl.add_argument("--no-enrich", action="store_true",
                      help="skip metadata enrichment")
    p_dl.add_argument("--upload", action="store_true",
                      help="after download, upload to cloud (Yandex.Disk / Mail.ru)")
    p_dl.add_argument("--auto-detect", action="store_true",
                      help="force re-run source detection (ignore cache)")
    p_dl.add_argument("--reset-cache", action="store_true",
                      help="clear cached source health, then re-detect")
    p_dl.add_argument("--include-previews", action="store_true",
                      help="include iTunes 30-90s preview as last-resort fallback")
    p_dl.add_argument("--verify-acoustid", action="store_true",
                      help="fingerprint each download and reject if not the expected track")
    p_dl.add_argument("--acoustid-api-key", default="",
                      help="AcoustID API key (or set ACOUSTID_API_KEY env)")
    p_dl.add_argument("--acoustid-min-score", type=float, default=0.5,
                      help="AcoustID match threshold (default 0.5)")
    p_dl.add_argument("--upload-after-download", action="store_true",
                      help="stream each track to the cloud as soon as it's ready")
    p_dl.add_argument("--delete-after-upload", action="store_true",
                      help="remove local file after successful cloud upload "
                           "(requires --upload-after-download)")
    p_dl.add_argument("-q", "--quiet", action="store_true")
    p_dl.set_defaults(func=cmd_download)

    p_up = sub.add_parser("upload",
                          help="upload library to configured cloud storage")
    p_up.add_argument("library", help="library directory")
    p_up.set_defaults(func=cmd_upload)

    p_ver = sub.add_parser("verify",
                           help="verify library against AcoustID (find wrong audio)")
    p_ver.add_argument("library", nargs="?", default="./library", help="library directory")
    p_ver.add_argument("--api-key", default=None,
                       help="AcoustID API key (or set ACOUSTID_API_KEY env)")
    p_ver.add_argument("--min-score", type=float, default=0.5,
                       help="match threshold (default 0.5)")
    p_ver.add_argument("--delete", action="store_true",
                       help="delete mismatched files (default: report only)")
    p_ver.add_argument("--delete-previews", action="store_true",
                       help="also delete <=35s files (likely 30s previews)")
    p_ver.add_argument("--workers", type=int, default=4)
    p_ver.add_argument("--limit", type=int, default=0, help="only verify first N files")
    p_ver.set_defaults(func=cmd_verify)

    p_src = sub.add_parser("sources", help="list available sources (no network test)")
    p_src.set_defaults(func=cmd_sources)

    p_doc = sub.add_parser("doctor", help="test all sources and show a health report")
    p_doc.add_argument("-o", "--output", default="./library",
                       help="output dir for state cache (default: ./library)")
    p_doc.add_argument("--only", nargs="+",
                       help="test only these sources (e.g. archiveorg openverse)")
    p_doc.add_argument("--reset", action="store_true",
                       help="also clear cached state")
    p_doc.add_argument("--include-previews", action="store_true",
                       help="include iTunes preview in the recommended chain")
    p_doc.add_argument("-q", "--quiet", action="store_true")
    p_doc.set_defaults(func=cmd_doctor)

    p_reset = sub.add_parser("reset", help="clear cached source health")
    p_reset.add_argument("-o", "--output", default="./library",
                         help="output dir (default: ./library)")
    p_reset.set_defaults(func=cmd_reset)

    p_cs = sub.add_parser("cloud-setup",
                          help="set up Yandex.Disk or Cloud.Mail.ru via WebDAV")
    p_cs.add_argument("-q", "--quiet", action="store_true")
    p_cs.set_defaults(func=cmd_cloud_setup)

    p_yo = sub.add_parser("yandex-oauth",
                          help="OAuth flow to get a Yandex.Disk REST API token")
    p_yo.add_argument("--token", help="non-interactive: provide a token directly")
    p_yo.add_argument("-q", "--quiet", action="store_true")
    p_yo.set_defaults(func=cmd_yandex_oauth)

    p_cst = sub.add_parser("cloud-status", help="show saved cloud credentials")
    p_cst.set_defaults(func=cmd_cloud_status)

    p_csx = sub.add_parser("cloud-logout", help="remove saved cloud credentials")
    p_csx.set_defaults(func=cmd_cloud_logout)

    p_cst2 = sub.add_parser("cloud-test", help="test upload a single file")
    p_cst2.add_argument("file", help="local file to upload")
    p_cst2.set_defaults(func=cmd_cloud_test)

    p_web = sub.add_parser("web", help="start browser-based UI")
    p_web.add_argument("--host", default="127.0.0.1", help="bind address")
    p_web.add_argument("--port", "-p", type=int, default=8080, help="port")
    p_web.add_argument("--debug", action="store_true", help="Flask debug mode")
    p_web.add_argument("-q", "--quiet", action="store_true")
    p_web.add_argument("-v", "--verbose", action="store_true")
    p_web.set_defaults(func=cmd_web)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
