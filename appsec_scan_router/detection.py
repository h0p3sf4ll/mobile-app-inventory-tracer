from __future__ import annotations

import re
from typing import Iterable

from .metadata import (
    collect_metadata_properties,
    parse_android_manifest,
    parse_capacitor_json,
    parse_capacitor_ts,
    parse_cordova_config,
    parse_csproj,
    parse_expo_config,
    parse_gradle_metadata,
    parse_info_plist,
    parse_info_plist_strings,
    parse_xcode_settings_metadata,
)
from .models import DetectionEvidence
from .utils import (
    load_json_object,
    merged_package_dependencies,
    normalize_path,
    regex_value,
    xml_text,
    yaml_has_flutter_dependency,
)


def detect_mobile_repo(
    paths: Iterable[str],
    file_contents: dict[str, str],
) -> tuple[str, list[DetectionEvidence], int]:
    evidence = collect_detection_evidence(paths, file_contents)
    score = sum(item.weight for item in evidence)
    has_strong_app_evidence = any(item.weight >= 3 for item in evidence)
    has_structural_app_evidence = any(item.category != "pipeline_mobile" for item in evidence)

    if score >= 7 and has_strong_app_evidence:
        confidence = "high"
    elif score >= 4 and has_structural_app_evidence:
        confidence = "medium"
    elif score >= 2 and has_structural_app_evidence:
        confidence = "low"
    else:
        confidence = "none"

    return confidence, evidence, score


def collect_detection_evidence(
    paths: Iterable[str],
    file_contents: dict[str, str],
) -> list[DetectionEvidence]:
    evidence: list[DetectionEvidence] = []
    path_set = {normalize_path(path).lower() for path in paths}
    properties = collect_metadata_properties(file_contents)
    evidence.extend(collect_path_evidence(path_set))

    for path, content in file_contents.items():
        lower_path = normalize_path(path).lower()
        if lower_path.endswith("androidmanifest.xml"):
            evidence.extend(detect_android_manifest_evidence(path, content))
        elif lower_path.endswith("build.gradle") or lower_path.endswith("build.gradle.kts"):
            evidence.extend(detect_gradle_evidence(path, content, properties))
        elif lower_path.endswith("info.plist"):
            evidence.extend(detect_info_plist_evidence(path, content, properties))
        elif lower_path.endswith("infoplist.strings"):
            evidence.extend(detect_info_plist_strings_evidence(path, content))
        elif lower_path.endswith("project.pbxproj") or lower_path.endswith(".xcconfig"):
            evidence.extend(detect_xcode_settings_evidence(path, content))
        elif lower_path.endswith("pubspec.yaml"):
            evidence.extend(detect_pubspec_evidence(path, content, path_set))
        elif lower_path.endswith("package.json"):
            evidence.extend(detect_package_json_evidence(path, content))
        elif lower_path.endswith("app.json") or lower_path.endswith("expo.json"):
            evidence.extend(detect_expo_evidence(path, content))
        elif lower_path.endswith("app.config.js") or lower_path.endswith("app.config.ts"):
            evidence.extend(detect_expo_dynamic_config_evidence(path, content))
        elif lower_path.endswith("capacitor.config.json"):
            evidence.extend(detect_capacitor_json_evidence(path, content))
        elif lower_path.endswith("capacitor.config.ts"):
            evidence.extend(detect_capacitor_ts_evidence(path, content))
        elif lower_path.endswith("config.xml"):
            evidence.extend(detect_cordova_evidence(path, content))
        elif lower_path.endswith(".csproj"):
            evidence.extend(detect_csproj_evidence(path, content))
        elif lower_path.endswith("azure-pipelines.yml") or lower_path.endswith("azure-pipeline.yml"):
            evidence.extend(detect_pipeline_evidence(path, content))

    return dedupe_evidence(evidence)


def collect_path_evidence(path_set: set[str]) -> list[DetectionEvidence]:
    evidence: list[DetectionEvidence] = []
    has_android_dir = any(path.startswith("/android/") or "/android/" in path for path in path_set)
    has_ios_dir = any(path.startswith("/ios/") or "/ios/" in path for path in path_set)
    has_pubspec = any(path.endswith("/pubspec.yaml") for path in path_set)

    if any(path.endswith(".xcodeproj") or path.endswith(".xcworkspace") for path in path_set):
        evidence.append(
            DetectionEvidence("ios", "xcode_project", "Xcode project/workspace file present", 2)
        )
    if any(path.endswith("/androidmanifest.xml") for path in path_set):
        evidence.append(
            DetectionEvidence("android", "android_manifest_path", "Android manifest file present", 1)
        )
    if has_pubspec and has_android_dir and has_ios_dir:
        evidence.append(
            DetectionEvidence(
                "flutter",
                "flutter_project_layout",
                "pubspec.yaml plus android and ios project folders",
                2,
            )
        )
    return evidence


def detect_android_manifest_evidence(path: str, content: str) -> list[DetectionEvidence]:
    metadata = parse_android_manifest(content)
    if metadata.identifier:
        return [DetectionEvidence("android", path, f"Android package {metadata.identifier}", 4)]
    if metadata.version or metadata.name:
        return [DetectionEvidence("android", path, "Parsed Android application manifest", 2)]
    return []


def detect_gradle_evidence(
    path: str,
    content: str,
    properties: dict[str, str] | None = None,
) -> list[DetectionEvidence]:
    metadata = parse_gradle_metadata(content, properties or {})
    evidence: list[DetectionEvidence] = []
    if re.search(r"\bcom\.android\.application\b", content):
        evidence.append(DetectionEvidence("android", path, "Gradle Android application plugin", 4))
    elif re.search(r"\bcom\.android\.library\b", content):
        evidence.append(DetectionEvidence("android_library", path, "Gradle Android library plugin", 1))
    if metadata.identifier:
        evidence.append(DetectionEvidence("android", path, f"Gradle applicationId {metadata.identifier}", 3))
    return evidence


def detect_info_plist_evidence(
    path: str,
    content: str,
    properties: dict[str, str] | None = None,
) -> list[DetectionEvidence]:
    metadata = parse_info_plist(content, properties or {})
    if metadata.identifier:
        return [DetectionEvidence("ios", path, f"CFBundleIdentifier {metadata.identifier}", 4)]
    if metadata.name or metadata.version:
        return [DetectionEvidence("ios", path, "Parsed iOS Info.plist app metadata", 2)]
    return []


def detect_info_plist_strings_evidence(path: str, content: str) -> list[DetectionEvidence]:
    metadata = parse_info_plist_strings(content)
    if metadata.name:
        return [DetectionEvidence("ios", path, "Localized iOS display name", 1)]
    return []


def detect_xcode_settings_evidence(path: str, content: str) -> list[DetectionEvidence]:
    metadata = parse_xcode_settings_metadata(content)
    if metadata.identifier:
        return [DetectionEvidence("ios", path, f"PRODUCT_BUNDLE_IDENTIFIER {metadata.identifier}", 4)]
    if metadata.version:
        return [DetectionEvidence("ios", path, "Xcode marketing version configured", 1)]
    return []


def detect_pubspec_evidence(path: str, content: str, path_set: set[str]) -> list[DetectionEvidence]:
    evidence: list[DetectionEvidence] = []
    has_android_dir = any(candidate.startswith("/android/") or "/android/" in candidate for candidate in path_set)
    has_ios_dir = any(candidate.startswith("/ios/") or "/ios/" in candidate for candidate in path_set)
    if yaml_has_flutter_dependency(content):
        evidence.append(DetectionEvidence("flutter", path, "pubspec.yaml declares Flutter SDK dependency", 4))
    if has_android_dir and has_ios_dir:
        evidence.append(DetectionEvidence("flutter", path, "Flutter-style android and ios folders present", 1))
    return evidence


def detect_package_json_evidence(path: str, content: str) -> list[DetectionEvidence]:
    dependencies = merged_package_dependencies(load_json_object(content))
    evidence: list[DetectionEvidence] = []

    if "react-native" in dependencies:
        evidence.append(DetectionEvidence("react_native", path, "package.json dependency react-native", 4))
    if "expo" in dependencies:
        evidence.append(DetectionEvidence("react_native", path, "package.json dependency expo", 3))
    if "@capacitor/core" in dependencies:
        evidence.append(
            DetectionEvidence("ionic_capacitor_cordova", path, "package.json dependency @capacitor/core", 4)
        )
    if "@ionic/angular" in dependencies or "@ionic/react" in dependencies or "@ionic/vue" in dependencies:
        evidence.append(
            DetectionEvidence("ionic_capacitor_cordova", path, "package.json Ionic framework dependency", 3)
        )
    if "cordova" in dependencies or "cordova-android" in dependencies or "cordova-ios" in dependencies:
        evidence.append(DetectionEvidence("ionic_capacitor_cordova", path, "package.json Cordova dependency", 3))
    return evidence


def detect_expo_evidence(path: str, content: str) -> list[DetectionEvidence]:
    metadata = parse_expo_config(content)
    if metadata.name or metadata.version or metadata.identifier:
        return [DetectionEvidence("react_native", path, "Parsed Expo app config", 4)]
    return []


def detect_expo_dynamic_config_evidence(path: str, content: str) -> list[DetectionEvidence]:
    if re.search(r"\bexpo\s*:", content) and (
        regex_value(content, r"\bbundleIdentifier\s*:\s*['\"]([^'\"]+)")
        or regex_value(content, r"\bpackage\s*:\s*['\"]([^'\"]+)")
    ):
        return [DetectionEvidence("react_native", path, "Parsed Expo dynamic app config", 4)]
    return []


def detect_capacitor_json_evidence(path: str, content: str) -> list[DetectionEvidence]:
    metadata = parse_capacitor_json(content)
    if metadata.identifier:
        return [DetectionEvidence("ionic_capacitor_cordova", path, f"Capacitor appId {metadata.identifier}", 4)]
    if metadata.name:
        return [DetectionEvidence("ionic_capacitor_cordova", path, "Parsed Capacitor app config", 2)]
    return []


def detect_capacitor_ts_evidence(path: str, content: str) -> list[DetectionEvidence]:
    metadata = parse_capacitor_ts(content)
    if metadata.identifier:
        return [DetectionEvidence("ionic_capacitor_cordova", path, f"Capacitor appId {metadata.identifier}", 4)]
    if metadata.name:
        return [DetectionEvidence("ionic_capacitor_cordova", path, "Parsed Capacitor app config", 2)]
    return []


def detect_cordova_evidence(path: str, content: str) -> list[DetectionEvidence]:
    metadata = parse_cordova_config(content)
    if metadata.identifier:
        return [DetectionEvidence("ionic_capacitor_cordova", path, f"Cordova widget id {metadata.identifier}", 4)]
    return []


def detect_csproj_evidence(path: str, content: str) -> list[DetectionEvidence]:
    target_frameworks = " ".join((xml_text(content, "TargetFramework"), xml_text(content, "TargetFrameworks"))).lower()
    uses_maui = xml_text(content, "UseMaui").lower() == "true"
    evidence: list[DetectionEvidence] = []

    if uses_maui:
        evidence.append(DetectionEvidence("xamarin_maui", path, "UseMaui=true", 4))
    if "-android" in target_frameworks or "-ios" in target_frameworks or "-maccatalyst" in target_frameworks:
        evidence.append(
            DetectionEvidence("xamarin_maui", path, f"Mobile target frameworks: {target_frameworks}", 4)
        )
    metadata = parse_csproj(content)
    if metadata.identifier and (uses_maui or target_frameworks):
        evidence.append(DetectionEvidence("xamarin_maui", path, f".NET mobile ApplicationId {metadata.identifier}", 3))
    return evidence


def detect_pipeline_evidence(path: str, content: str) -> list[DetectionEvidence]:
    task_patterns = (
        r"\bXcode@\d+\b",
        r"\bGradle@\d+\b",
        r"\bAndroidSigning@\d+\b",
        r"\bInstallAppleCertificate@\d+\b",
        r"\bInstallAppleProvisioningProfile@\d+\b",
        r"\bGooglePlayRelease@\d+\b",
        r"\bAppStoreRelease@\d+\b",
    )
    count = sum(1 for pattern in task_patterns if re.search(pattern, content))
    if count >= 2:
        return [DetectionEvidence("pipeline_mobile", path, f"{count} mobile pipeline tasks", 2)]
    return []


def dedupe_evidence(evidence: list[DetectionEvidence]) -> list[DetectionEvidence]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[DetectionEvidence] = []
    for item in evidence:
        key = (item.category, item.source, item.detail)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique
