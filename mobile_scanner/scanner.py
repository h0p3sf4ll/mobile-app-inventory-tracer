from __future__ import annotations

import json
import logging
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, as_completed, wait
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .activity import extract_repo_activity, parse_ado_datetime
from .azure import AzureDevOpsClient
from .constants import KNOWN_CATEGORIES, active_sheet_name, older_sheet_name
from .detection import detect_mobile_repo
from .metadata import extract_mobile_metadata
from .models import (
    AzureDevOpsError,
    DetectionEvidence,
    MobileAppMetadata,
    RepoActivityMetadata,
    RepoScanTarget,
    ScanConfig,
)
from .reports import StreamingReportWriter
from .store_lookup import StoreLookupClient, store_columns
from .utils import confidence_rank, should_fetch_content


LOGGER = logging.getLogger("ado_mobile_scanner")
UNAVAILABLE_REPO_REASON = "This usually means the repo is empty, disabled, moved, or not readable by this PAT."


def scan_to_reports(config: ScanConfig) -> tuple[list[dict[str, Any]], Path, Path, Path]:
    with StreamingReportWriter(config.out_dir, config.out_prefix, config.branch_age_days) as writer:
        LOGGER.info("Streaming CSV report to %s", writer.csv_path)
        LOGGER.info("Streaming JSON report to %s", writer.json_path)
        LOGGER.info("Streaming Excel report to %s", writer.xlsx_path)
        results = scan(config, on_result=writer.write_result)
        return results, writer.csv_path, writer.json_path, writer.xlsx_path


def scan(
    config: ScanConfig,
    on_result: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    start = time.monotonic()
    client = AzureDevOpsClient(config.org, config.pat, config.timeout_seconds)
    store_client = create_store_client(config)
    try:
        targets = collect_targets(client, config.project)
        LOGGER.info("Scanning %s repositories", len(targets))

        results: list[dict[str, Any]] = []
        repo_workers = max(1, min(config.max_workers, len(targets) or 1))
        content_workers = max(1, config.content_workers)
        min_rank = confidence_rank(config.min_confidence)

        with (
            ThreadPoolExecutor(max_workers=repo_workers) as repo_executor,
            ThreadPoolExecutor(max_workers=content_workers) as content_executor,
        ):
            completed_scans = iter_completed_repo_scans(
                repo_executor=repo_executor,
                client=client,
                targets=targets,
                content_executor=content_executor,
                max_in_flight=max(repo_workers * 4, repo_workers),
                min_confidence_rank=min_rank,
                max_commits_per_repo=config.max_commits_per_repo,
                branch_age_days=config.branch_age_days,
                store_client=store_client,
            )

            for index, future in completed_scans:
                try:
                    branch_results = future.result()
                except Exception as exc:
                    LOGGER.warning("Failed to scan repo: %s", exc)
                    continue

                for result in branch_results:
                    results.append(result)
                    if on_result:
                        on_result(result)
                    log_detected_result(result)

                if index % 25 == 0:
                    LOGGER.info("Progress: %s/%s repositories scanned", index, len(targets))

        results.sort(key=row_sort_key)
        LOGGER.info("Finished in %.1fs; found %s app branches", time.monotonic() - start, len(results))
        return results
    finally:
        client.close()
        if store_client:
            store_client.close()


def scan_repo(
    client: AzureDevOpsClient,
    target: RepoScanTarget,
    content_executor: ThreadPoolExecutor,
    min_confidence_rank: int,
    max_commits_per_repo: int,
    branch_age_days: int,
    store_client: StoreLookupClient | None,
) -> list[dict[str, Any]]:
    repo = target.repo
    repo_id = repo.get("id", "")
    repo_name = repo.get("name", "")

    if not repo_id:
        LOGGER.warning("Skipping repo without id in project %s: %s", target.project_name, repo)
        return []
    if repo.get("isDisabled"):
        LOGGER.info("Skipping disabled repo: %s/%s", target.project_name, repo_name)
        return []

    try:
        refs = client.list_branches(target.project_name, repo_id)
    except AzureDevOpsError as exc:
        if exc.status_code == 404:
            LOGGER.info(
                "Skipping repo whose branches are unavailable: %s/%s. %s",
                target.project_name,
                repo_name,
                UNAVAILABLE_REPO_REASON,
            )
            return []
        raise

    rows: list[dict[str, Any]] = []
    for ref in refs:
        branch_name = branch_name_from_ref(ref.get("name", ""))
        if not branch_name:
            continue
        try:
            row = scan_branch(
                client=client,
                target=target,
                branch_name=branch_name,
                content_executor=content_executor,
                min_confidence_rank=min_confidence_rank,
                max_commits_per_repo=max_commits_per_repo,
                branch_age_days=branch_age_days,
                store_client=store_client,
            )
        except AzureDevOpsError as exc:
            LOGGER.info("Skipping branch %s/%s@%s: %s", target.project_name, repo_name, branch_name, exc)
            continue
        if row:
            rows.append(row)

    return rows


def scan_branch(
    client: AzureDevOpsClient,
    target: RepoScanTarget,
    branch_name: str,
    content_executor: ThreadPoolExecutor,
    min_confidence_rank: int,
    max_commits_per_repo: int,
    branch_age_days: int,
    store_client: StoreLookupClient | None,
) -> dict[str, Any] | None:
    repo = target.repo
    repo_id = repo.get("id", "")
    repo_name = repo.get("name", "")

    try:
        items = client.list_repo_items(target.project_name, repo_id, branch_name)
    except AzureDevOpsError as exc:
        if exc.status_code == 404:
            LOGGER.debug("Skipping unavailable branch contents: %s/%s@%s", target.project_name, repo_name, branch_name)
            return None
        raise

    paths = [item.get("path", "") for item in items if item.get("path")]
    if not paths:
        LOGGER.debug("Skipping empty branch: %s/%s@%s", target.project_name, repo_name, branch_name)
        return None

    content_paths = [path for path in paths if should_fetch_content(path)]
    contents = fetch_contents(client, target.project_name, repo_id, branch_name, content_paths, content_executor)
    confidence, evidence, score = detect_mobile_repo(paths, contents)
    metadata = extract_mobile_metadata(contents)

    if confidence == "none" or confidence_rank(confidence) < min_confidence_rank:
        LOGGER.debug("No match: %s/%s@%s", target.project_name, repo_name, branch_name)
        return None

    activity = fetch_repo_activity(client, target.project_name, repo_id, branch_name, max_commits_per_repo)
    categories = sorted({item.category for item in evidence})

    return build_scan_row(
        target=target,
        branch_name=branch_name,
        metadata=metadata,
        activity=activity,
        confidence=confidence,
        score=score,
        categories=categories,
        evidence=evidence,
        branch_age_days=branch_age_days,
        store_client=store_client,
    )


def build_scan_row(
    target: RepoScanTarget,
    branch_name: str,
    metadata: MobileAppMetadata,
    activity: RepoActivityMetadata,
    confidence: str,
    score: int,
    categories: list[str],
    evidence: list[DetectionEvidence],
    branch_age_days: int,
    store_client: StoreLookupClient | None,
) -> dict[str, Any]:
    repo = target.repo
    age_bucket = branch_age_bucket(activity.last_updated, branch_age_days)
    store_metadata = store_columns(metadata.identifier, categories, store_client)
    return {
        "project": target.project_name,
        "repo_name": repo.get("name", ""),
        "branch_name": branch_name,
        "branch_last_updated": activity.last_updated,
        "branch_age_bucket": age_bucket,
        "web_url": repo.get("webUrl", ""),
        "mobile_name": metadata.name,
        "mobile_version": metadata.version,
        "mobile_identifier": metadata.identifier,
        "mobile_identifier_source": metadata.identifier_source,
        "mobile_identifier_status": identifier_status(metadata.identifier),
        "contributing_developers": "; ".join(activity.contributing_developers),
        "last_updated": activity.last_updated,
        "confidence": confidence,
        "score": score,
        "categories": "; ".join(categories),
        **category_columns(categories),
        **store_metadata,
        "detection_evidence": json.dumps([item.as_dict() for item in evidence], sort_keys=True),
    }


def row_sort_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("project", "")).lower(),
        str(row.get("repo_name", "")).lower(),
        str(row.get("branch_name", "")).lower(),
    )


def log_detected_result(result: dict[str, Any]) -> None:
    LOGGER.info(
        "DETECTED app=%s version=%s id=%s confidence=%s repo=%s/%s branch=%s age=%s categories=%s",
        result["mobile_name"] or "(unknown)",
        result["mobile_version"] or "(unknown)",
        result["mobile_identifier"] or "(unknown)",
        result["confidence"],
        result["project"],
        result["repo_name"],
        result["branch_name"],
        result["branch_age_bucket"],
        result["categories"],
    )


def create_store_client(config: ScanConfig) -> StoreLookupClient | None:
    if not config.store_lookup:
        return None
    return StoreLookupClient(config.store_country, config.store_timeout_seconds)


def branch_name_from_ref(ref_name: str) -> str:
    prefix = "refs/heads/"
    if ref_name.startswith(prefix):
        return ref_name[len(prefix):]
    return ref_name


def branch_age_bucket(
    last_updated: str,
    branch_age_days: int,
    now: datetime | None = None,
) -> str:
    updated_at = parse_ado_datetime(last_updated)
    if updated_at is None:
        return older_sheet_name(branch_age_days)
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=branch_age_days)
    if updated_at >= cutoff:
        return active_sheet_name(branch_age_days)
    return older_sheet_name(branch_age_days)


def identifier_status(identifier: str) -> str:
    if identifier:
        return "found"
    return "missing_from_scanned_files"


def category_columns(categories: Iterable[str]) -> dict[str, str]:
    category_set = set(categories)
    return {
        f"category_{category}": "TRUE" if category in category_set else "FALSE"
        for category in KNOWN_CATEGORIES
    }


def fetch_repo_activity(
    client: AzureDevOpsClient,
    project_name: str,
    repo_id: str,
    branch_name: str,
    max_commits: int,
) -> RepoActivityMetadata:
    try:
        commits = client.list_commits(
            project_name=project_name,
            repo_id=repo_id,
            max_commits=max_commits,
            branch_name=branch_name,
        )
    except AzureDevOpsError as exc:
        LOGGER.info("Could not fetch commit activity for %s/%s@%s: %s", project_name, repo_id, branch_name, exc)
        return RepoActivityMetadata()
    return extract_repo_activity(commits)


def fetch_contents(
    client: AzureDevOpsClient,
    project_name: str,
    repo_id: str,
    branch_name: str,
    paths: list[str],
    executor: ThreadPoolExecutor,
) -> dict[str, str]:
    if not paths:
        return {}

    contents: dict[str, str] = {}
    futures = {
        executor.submit(client.fetch_file_content, project_name, repo_id, path, branch_name): path
        for path in paths
    }
    for future in as_completed(futures):
        path = futures[future]
        content = future.result()
        if content:
            contents[path] = content
    return contents


def collect_targets(client: AzureDevOpsClient, project_name: str | None) -> list[RepoScanTarget]:
    projects = [{"name": project_name}] if project_name else client.list_projects()
    targets: list[RepoScanTarget] = []
    seen_repo_ids: set[str] = set()

    for project in projects:
        name = project.get("name")
        if not name:
            continue

        LOGGER.info("Listing repositories in project: %s", name)
        try:
            repos = client.list_repos(name)
        except Exception as exc:
            LOGGER.warning("Failed to list repos for %s: %s", name, exc)
            continue

        for repo in repos:
            repo_id = repo.get("id")
            if not repo_id or repo_id in seen_repo_ids:
                continue
            seen_repo_ids.add(repo_id)
            targets.append(RepoScanTarget(project_name=name, repo=repo))

    return targets


def iter_completed_repo_scans(
    repo_executor: ThreadPoolExecutor,
    client: AzureDevOpsClient,
    targets: list[RepoScanTarget],
    content_executor: ThreadPoolExecutor,
    max_in_flight: int,
    min_confidence_rank: int,
    max_commits_per_repo: int,
    branch_age_days: int,
    store_client: StoreLookupClient | None,
) -> Iterable[tuple[int, Future[list[dict[str, Any]]]]]:
    target_iter = iter(targets)
    pending: set[Future[list[dict[str, Any]]]] = set()
    submitted = 0
    completed = 0

    def submit_next() -> bool:
        nonlocal submitted
        try:
            target = next(target_iter)
        except StopIteration:
            return False
        pending.add(
            repo_executor.submit(
                scan_repo,
                client,
                target,
                content_executor,
                min_confidence_rank,
                max_commits_per_repo,
                branch_age_days,
                store_client,
            )
        )
        submitted += 1
        return True

    for _ in range(max(1, max_in_flight)):
        if not submit_next():
            break

    while pending:
        done, pending = wait(pending, return_when=FIRST_COMPLETED)
        for future in done:
            completed += 1
            yield completed, future

        while len(pending) < max_in_flight and submitted < len(targets):
            if not submit_next():
                break
