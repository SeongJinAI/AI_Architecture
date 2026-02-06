#!/usr/bin/env python3
"""
커밋 전 문서 체크리스트 검증 훅
- Java 소스 변경 시 관련 문서 존재 여부를 확인
- 빌드 성공 여부를 확인
- 문서 누락 시 커밋을 차단하고 Claude에게 피드백
"""

import json
import sys
import subprocess
import os
import re
from pathlib import Path


def get_input():
    """stdin에서 훅 입력 데이터를 읽는다."""
    try:
        return json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(1)


def is_git_commit(command: str) -> bool:
    """git commit 명령인지 확인한다."""
    # git commit, git commit -m, git commit -am 등 매칭
    return bool(re.search(r'\bgit\s+commit\b', command))


def get_staged_files(project_dir: str) -> list[str]:
    """staged된 파일 목록을 반환한다."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=project_dir,
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return []
    return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]


def extract_feature_names(java_files: list[str]) -> set[str]:
    """
    변경된 Java 파일 경로에서 기능명을 추론한다.
    예: src/main/java/com/erp/attendance/AttendanceController.java
        → "attendance"
    """
    feature_names = set()
    for f in java_files:
        parts = f.split("/")
        # controller, service, repository 등의 상위 패키지명을 기능명으로 추정
        for i, part in enumerate(parts):
            if part in ("controller", "service", "repository", "dto", "entity", "mapper"):
                if i > 0:
                    feature_names.add(parts[i - 1].lower())
                break
        # 패키지 구조가 다른 경우 — 파일명에서 추출
        else:
            filename = Path(f).stem  # AttendanceController → AttendanceController
            # CamelCase에서 첫 단어 추출
            match = re.match(r'^([A-Z][a-z]+)', filename)
            if match:
                feature_names.add(match.group(1).lower())
    
    return feature_names


def check_build(project_dir: str) -> str | None:
    """빌드를 확인한다. 실패 시 에러 메시지를 반환한다."""
    # Gradle 프로젝트
    gradlew = os.path.join(project_dir, "gradlew")
    if os.path.exists(gradlew):
        result = subprocess.run(
            ["./gradlew", "compileJava", "-q"],
            cwd=project_dir,
            capture_output=True, text=True,
            timeout=90
        )
        if result.returncode != 0:
            return f"Gradle 빌드 실패:\n{result.stderr[:300]}"
        return None

    # Maven 프로젝트
    pom = os.path.join(project_dir, "pom.xml")
    if os.path.exists(pom):
        result = subprocess.run(
            ["mvn", "compile", "-q"],
            cwd=project_dir,
            capture_output=True, text=True,
            timeout=90
        )
        if result.returncode != 0:
            return f"Maven 빌드 실패:\n{result.stderr[:300]}"
        return None

    return None  # 빌드 도구를 못 찾으면 스킵


def check_docs(project_dir: str, feature_names: set[str], staged_files: list[str]) -> list[str]:
    """
    기능별 필수 문서 존재 여부를 확인한다.
    반환: 누락된 문서 에러 메시지 리스트
    """
    docs_dir = os.path.join(project_dir, "src", "docs")
    errors = []

    # ── 전역 문서 체크 ──
    error_messages_path = os.path.join(docs_dir, "ERROR_MESSAGES.md")
    if not os.path.exists(error_messages_path):
        errors.append("📄 ERROR_MESSAGES.md가 존재하지 않습니다 → src/docs/ERROR_MESSAGES.md 생성 필요")

    # ERROR_MESSAGES.md가 존재하는데 staged에 없으면 경고 (업데이트 안 했을 수 있음)
    elif not any("ERROR_MESSAGES.md" in f for f in staged_files):
        errors.append("⚠️ ERROR_MESSAGES.md가 이번 커밋에 포함되지 않았습니다. 새 에러 코드 추가가 필요하지 않은지 확인하세요")

    # ── 기능별 문서 체크 ──
    for feature in feature_names:
        missing = []

        # 기능명세서: {feature}_기능명세서.md 또는 {Feature}_기능명세서.md
        spec_patterns = [
            os.path.join(docs_dir, f"{feature}_기능명세서.md"),
            os.path.join(docs_dir, f"{feature.capitalize()}_기능명세서.md"),
        ]
        if not any(os.path.exists(p) for p in spec_patterns):
            missing.append(f"  - 기능명세서: src/docs/{feature}_기능명세서.md")

        # 아키텍처 설명서
        arch_path = os.path.join(docs_dir, "architecture", f"{feature}.md")
        if not os.path.exists(arch_path):
            missing.append(f"  - 아키텍처 설명서: src/docs/architecture/{feature}.md")

        # 사용자매뉴얼
        guide_path = os.path.join(docs_dir, "user-guide", f"{feature}.md")
        if not os.path.exists(guide_path):
            missing.append(f"  - 사용자매뉴얼: src/docs/user-guide/{feature}.md")

        if missing:
            errors.append(f"📁 기능 '{feature}' 관련 문서 누락:\n" + "\n".join(missing))

    return errors


def check_staged_docs(staged_files: list[str]) -> str | None:
    """staged에 문서 파일이 하나도 없으면 경고."""
    doc_files = [f for f in staged_files if f.startswith("src/docs/")]
    if not doc_files:
        return (
            "⚠️ 이번 커밋에 src/docs/ 하위 문서가 하나도 포함되지 않았습니다.\n"
            "   기능 코드를 변경했다면 관련 문서도 함께 커밋하세요."
        )
    return None


def deny(reason: str):
    """커밋을 차단하고 Claude에게 피드백."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason
        }
    }))
    sys.exit(0)


def main():
    input_data = get_input()
    command = input_data.get("tool_input", {}).get("command", "")

    # git commit이 아니면 무조건 통과
    if not is_git_commit(command):
        sys.exit(0)

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    staged_files = get_staged_files(project_dir)

    if not staged_files:
        sys.exit(0)  # staged 파일 없으면 패스

    # Java 소스 변경 확인 (테스트 코드 제외)
    java_changes = [
        f for f in staged_files
        if f.endswith(".java")
        and "src/main" in f
        and "test" not in f.lower()
    ]

    # Java 소스 변경이 없으면 문서 체크 스킵 (문서만 수정, 설정 변경 등)
    if not java_changes:
        sys.exit(0)

    # ── 검증 시작 ──
    all_errors = []

    # 1. 빌드 확인
    build_error = check_build(project_dir)
    if build_error:
        all_errors.append(f"❌ {build_error}")

    # 2. 기능명 추출 & 문서 존재 확인
    feature_names = extract_feature_names(java_changes)
    if feature_names:
        doc_errors = check_docs(project_dir, feature_names, staged_files)
        all_errors.extend(doc_errors)

    # 3. staged에 문서 포함 여부
    staged_warning = check_staged_docs(staged_files)
    if staged_warning:
        all_errors.append(staged_warning)

    # ── 결과 ──
    if all_errors:
        header = (
            "🚫 커밋 전 문서 체크리스트 미완료\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"변경된 Java 파일: {len(java_changes)}개\n"
            f"감지된 기능: {', '.join(feature_names) if feature_names else '추출 실패'}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        )
        body = "\n\n".join(all_errors)
        footer = (
            "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📋 문서를 작성/업데이트한 후 다시 커밋하세요.\n"
            "   참고: CLAUDE.md의 Feature Development Completion Checklist"
        )
        deny(header + body + footer)

    # 모두 통과
    sys.exit(0)


if __name__ == "__main__":
    main()
