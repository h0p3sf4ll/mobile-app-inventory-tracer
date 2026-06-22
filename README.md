# AppSec Inventory Service

AppSec Inventory Service builds an application inventory from Azure DevOps and GitHub Enterprise without cloning
repositories. It discovers mobile apps, web applications, API services, microservices, serverless workloads,
containerized services, and middleware-oriented workers from structured source evidence, then streams reports and
scanner target manifests as the scan runs.

The project is published on PyPI as `appsec-scan-router` for package continuity. The primary commands are now
`appsec-inventory-service` and `appsec-inventory-service-ui`; older command names remain available as compatibility
aliases.

## Capabilities

- Scans Azure DevOps or GitHub Enterprise organizations
- Scans the entire organization when Azure DevOps `--project` or GitHub `--repo` is omitted
- Scans one resolved branch per repository: default branch first, then deployment or production-like fallback branches
- Detects Android, iOS, Flutter, React Native, Expo, Ionic, Capacitor, Cordova, Xamarin, and .NET MAUI
- Detects web frontends, web backends, API services, microservices, middleware workers, serverless apps, containers, and deployment descriptors
- Extracts inventory name, version, language, categories, mobile bundle/package identifiers, contributors, and last activity
- Splits Excel output into active and older worksheets based on the configured branch age window
- Optionally validates public Apple App Store and Google Play listings from detected mobile identifiers
- Emits CSV, JSON, XLSX, Semgrep target lists, SonarQube project manifests, and generic scanner target manifests
- Runs as a CLI, browser UI, Docker image, SDK, or importable library

## Install

```bash
python -m pip install appsec-scan-router
```

```bash
appsec-inventory-service --help
appsec-inventory-service-ui --help
```

For local development:

```bash
git clone https://github.com/h0p3sf4ll/mobile-app-inventory-tracer.git
cd mobile-app-inventory-tracer
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e .
```

## Azure DevOps

Set a read-only PAT:

```bash
export ADO_PAT="your-token"
```

Scan every project in an organization:

```bash
appsec-inventory-service \
  --provider azure-devops \
  --org FabrikamCloud \
  --out-dir reports
```

Scan one project:

```bash
appsec-inventory-service \
  --provider azure-devops \
  --org FabrikamCloud \
  --project "Go_To_Market" \
  --out-dir reports
```

`azure-devops` is the default provider, so `--provider azure-devops` can be omitted.

## GitHub Enterprise

Set a read-only token:

```bash
export GITHUB_TOKEN="your-token"
```

Scan every repository owned by an organization or user:

```bash
appsec-inventory-service \
  --provider github-enterprise \
  --base-url https://github.fabrikam.example/api/v3 \
  --org FabrikamCloud \
  --out-dir reports
```

Scan one repository:

```bash
appsec-inventory-service \
  --provider github-enterprise \
  --base-url https://github.fabrikam.example/api/v3 \
  --org FabrikamCloud \
  --repo payments-api \
  --out-dir reports
```

`--project` is accepted as a GitHub repository alias for teams that use one shared automation template.

## Docker

Build the image:

```bash
docker build -t appsec-inventory-service .
```

Run the browser UI on port `48731`:

```bash
mkdir -p reports
docker run --rm \
  -p 48731:48731 \
  -e ADO_PAT="$ADO_PAT" \
  -e GITHUB_TOKEN="$GITHUB_TOKEN" \
  -v "$PWD/reports:/reports" \
  appsec-inventory-service \
  ui \
  --host 0.0.0.0 \
  --port 48731 \
  --reports-dir /reports
```

Open `http://localhost:48731`.

Run a CLI scan in the container:

```bash
mkdir -p reports
docker run --rm \
  -e ADO_PAT="$ADO_PAT" \
  -v "$PWD/reports:/reports" \
  appsec-inventory-service \
  --provider azure-devops \
  --org FabrikamCloud \
  --out-dir /reports
```

GitHub Enterprise CLI scan:

```bash
mkdir -p reports
docker run --rm \
  -e GITHUB_TOKEN="$GITHUB_TOKEN" \
  -v "$PWD/reports:/reports" \
  appsec-inventory-service \
  --provider github-enterprise \
  --base-url https://github.fabrikam.example/api/v3 \
  --org FabrikamCloud \
  --out-dir /reports
```

The image runs as a non-root user and writes reports to `/reports`.

## Browser UI

```bash
appsec-inventory-service-ui --host 127.0.0.1 --port 48731 --reports-dir reports
```

The UI includes provider selection, token entry, whole-organization scans, confidence controls, activity mode,
branch age cutoff, worker tuning, store lookup, live logs, stop control, and report downloads. Required and optional
fields are labeled, scan defaults are shown inline, and the active scan panel shows live progress with an ETA once
enough progress data exists.

Preferred environment variables:

| Variable | Purpose |
| --- | --- |
| `APPSEC_INVENTORY_SERVICE_UI_HOST` | Default UI host |
| `APPSEC_INVENTORY_SERVICE_UI_PORT` | Default UI port |
| `APPSEC_INVENTORY_SERVICE_REPORTS_DIR` | Default reports directory |

Legacy `APPSEC_SCAN_ROUTER_*` UI variables are still accepted.

## Branch Selection

Each repository contributes one branch to the inventory.

1. If the repository has a default branch, that branch is scanned.
2. If no default branch exists, Azure DevOps build definitions or GitHub deployment refs are inspected.
3. If those are unavailable, the scanner chooses the strongest production or mainline branch name, including
   `production`, `prod`, `preprod`, `release`, `staging`, `main`, `master`, `development`, `develop`, and `dev`.

There is no universal production branch field across source control and delivery platforms. Deployment fallback is
best-effort and depends on token permissions.

## Detection Model

Detection is evidence-based. The scanner fetches only allow-listed files that carry application or service signals,
including:

- Mobile manifests and project files: `AndroidManifest.xml`, `Info.plist`, `project.pbxproj`, `.xcconfig`, `.csproj`,
  `pubspec.yaml`, `capacitor.config.*`, `ionic.config.json`, and `config.xml`
- Build and dependency manifests: `package.json`, `pom.xml`, `build.gradle`, `build.gradle.kts`, `pyproject.toml`,
  `requirements.txt`, `Pipfile`, `go.mod`, `Cargo.toml`, `composer.json`, and `Gemfile`
- Runtime and deployment descriptors: `Dockerfile`, Compose files, Helm charts, Kustomize files, Serverless files,
  Spring application config, Terraform `main.tf`, and Azure pipeline YAML

Weak indicators are not enough on their own. A generic `.csproj`, a generic `config.xml`, or a standalone Dockerfile
will not be treated as a strong application match without supporting framework, manifest, or dependency evidence.

## Mobile Metadata And Store Validation

Mobile fields are populated when source manifests expose them:

| Field | Meaning |
| --- | --- |
| `mobile_name` | App display name |
| `mobile_version` | App version after placeholder filtering |
| `mobile_identifier` | Android package or Apple bundle identifier |
| `mobile_identifier_source` | Source family where the identifier was found |
| `mobile_identifier_status` | `found` or `missing_from_scanned_files` |

The scanner resolves common indirection patterns such as Gradle properties, Xcode build settings, MSBuild props, iOS
plist references, and Android string resources. It does not invent identifiers. Missing identifiers usually mean the
value is generated by CI/CD, stored in private variables, assembled by flavor-specific build logic, or absent from the
scanned branch.

Enable public store lookup:

```bash
appsec-inventory-service \
  --provider azure-devops \
  --org FabrikamCloud \
  --out-dir reports \
  --store-lookup \
  --store-country US
```

Store validation fields return `TRUE` when the requested public store listing is found and `FALSE` when lookup is
disabled, unavailable, missing, or not publicly visible.

## Outputs

Reports are created when the scan starts and updated as matching assets are detected.

Default output names:

- `appsec_inventory_service.csv`
- `appsec_inventory_service.json`
- `appsec_inventory_service.xlsx`
- `appsec_inventory_service_scanner_targets.csv`
- `appsec_inventory_service_scanner_targets.json`
- `appsec_inventory_service_semgrep_targets.txt`
- `appsec_inventory_service_sonarqube_projects.csv`

The workbook contains two worksheets by default:

- `Active 90d`
- `Older 90d`

Changing `--branch-age-days` changes these sheet names, for example `Active 60d` and `Older 60d`.

Core fields:

| Field | Meaning |
| --- | --- |
| `project` | Azure DevOps project or GitHub owner |
| `repo_name` | Repository name |
| `branch_name` | Branch scanned |
| `branch_last_updated` | Latest commit timestamp seen on that branch |
| `branch_age_bucket` | Active or older worksheet bucket |
| `web_url` | Repository browser URL |
| `source_url` | Clone/source URL when available |
| `inventory_name` | Best available application or service name |
| `inventory_version` | Best available application or service version |
| `inventory_types` | Semicolon-separated inventory types |
| `primary_language` | Best-effort primary language |
| `scanner_target` | Source target with branch metadata for downstream scanner orchestration |
| `semgrep_target` | Semgrep-oriented target reference |
| `sonarqube_project_key` | Stable SonarQube project key suggestion |
| `sonarqube_project_name` | SonarQube project display name suggestion |
| `contributing_developers` | Semicolon-separated commit authors |
| `last_updated` | Compatibility alias for `branch_last_updated` |
| `confidence` | Detection confidence |
| `score` | Weighted evidence score |
| `categories` | Semicolon-separated detection categories |
| `type_*` | Excel-filter-friendly inventory type flags |
| `category_*` | Excel-filter-friendly category flags |
| `detection_evidence` | JSON evidence details |

Scanner sidecars:

- `_scanner_targets.csv` and `_scanner_targets.json` are provider-neutral manifests for orchestration jobs.
- `_semgrep_targets.txt` is a line-oriented target list.
- `_sonarqube_projects.csv` contains project key/name suggestions, branch, source URL, and context columns.

These files are intended for pipeline glue code that checks out each target and runs tools such as Semgrep,
SonarQube Scanner, SCA scanners, or custom security checks.

## CLI Reference

| Option | Default | Description |
| --- | --- | --- |
| `--provider` | `azure-devops` | `azure-devops` or `github-enterprise` |
| `--org` | required | Azure DevOps organization or GitHub owner |
| `--project` | all | Azure DevOps project or GitHub repository name |
| `--repo` | all | GitHub repository name; alias for `--project` |
| `--base-url` | env | GitHub Enterprise API URL |
| `--pat` | env | Provider token; prefer `ADO_PAT`, `GITHUB_TOKEN`, or `GHE_TOKEN` |
| `--out-dir` | current directory | Output directory |
| `--out-prefix` | `appsec_inventory_service` | Output filename prefix |
| `--max-workers` | `8` | Concurrent repository preparation tasks |
| `--branch-workers` | `16` | Concurrent branch scans |
| `--content-workers` | `16` | Concurrent selected-file fetches |
| `--max-commits-per-repo` | `0` | Commit limit per matched branch; `0` means all available history |
| `--timeout` | `30` | Provider HTTP timeout in seconds |
| `--min-confidence` | `low` | `low`, `medium`, or `high` |
| `--branch-age-days` | `90` | Active/older worksheet cutoff |
| `--activity-mode` | `contributors` | `contributors` or `latest` |
| `--store-lookup` | disabled | Enable public app store enrichment |
| `--store-country` | `US` | Two-letter store country code |
| `--store-timeout` | `15` | Store lookup timeout in seconds |
| `--verbose` | disabled | Debug logging |

## Performance Guidance

For first-pass inventory in a large organization:

```bash
appsec-inventory-service \
  --provider github-enterprise \
  --base-url https://github.fabrikam.example/api/v3 \
  --org FabrikamCloud \
  --out-dir reports \
  --min-confidence medium \
  --activity-mode latest \
  --max-workers 12 \
  --branch-workers 32 \
  --content-workers 32
```

Use `--activity-mode latest` when you only need last-update timestamps. Use `--activity-mode contributors` when the
developer column is required. Leave `--store-lookup` off for the fastest scan.

Increase workers only while the source provider is responding cleanly. Reduce concurrency if you see throttling,
timeouts, or repeated transient errors.

## SDK

```python
from pathlib import Path

from appsec_scan_router import ScanConfig, scan_to_reports

config = ScanConfig(
    provider="github-enterprise",
    base_url="https://github.fabrikam.example/api/v3",
    org="FabrikamCloud",
    pat="your-token",
    project=None,
    out_dir=Path("reports"),
    out_prefix="appsec_inventory_service",
    max_workers=8,
    branch_workers=16,
    content_workers=16,
    max_commits_per_repo=2000,
    timeout_seconds=30,
    min_confidence="medium",
    branch_age_days=90,
    activity_mode="contributors",
    store_lookup=True,
    store_country="US",
    store_timeout_seconds=15,
)

results, csv_path, json_path, xlsx_path = scan_to_reports(config)
```

Object-oriented usage:

```python
from appsec_scan_router import AppSecInventoryService

service = AppSecInventoryService(config)
results, csv_path, json_path, xlsx_path = service.scan_to_reports()
```

Stream rows into another process:

```python
from appsec_scan_router import scan

def handle_row(row):
    print(row["project"], row["repo_name"], row["branch_name"], row["inventory_types"])

rows = scan(config, on_result=handle_row)
```

## Compatibility

These commands remain available:

```bash
appsec-scan-router --help
appsec-scan-router-ui --help
mobile-app-inventory-tracer --org FabrikamCloud --out-dir reports
ado-mobile-scanner --org FabrikamCloud --out-dir reports
```

New integrations should import `appsec_scan_router` and prefer the `appsec-inventory-service` command.

## Test

```bash
python -m unittest discover -s tests
python -m compileall ado_mobile_scanner.py mobile_app_inventory_tracer.py appsec_scan_router mobile_scanner tests
```

## Security Notes

- Use read-only tokens
- Scope tokens to the smallest practical organization, project, or repository set
- Prefer environment variables over `--pat`
- Do not commit generated reports if they contain internal names, URLs, identifiers, or contributor emails
- The scanner does not clone repositories
- The scanner fetches only allow-listed source and configuration files
- Docker runs as a non-root user

## License

AppSec Inventory Service is released under the MIT License. See [LICENSE](LICENSE).
