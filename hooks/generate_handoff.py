#!/usr/bin/env python3
import json, sys, os
from datetime import datetime

input_data = json.load(sys.stdin)
transcript_path = input_data.get("transcript_path", "")
trigger = input_data.get("trigger", "unknown")  # "manual" or "auto"
project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
handoff_dir = os.path.join(project_dir, "docs", "handoffs")
os.makedirs(handoff_dir, exist_ok=True)

# 트랜스크립트 파싱
messages = []
if transcript_path and os.path.exists(transcript_path):
    with open(transcript_path, "r") as f:
        for line in f:
            try:
                msg = json.loads(line)
                messages.append(msg)
            except:
                pass

# 최근 메시지에서 주요 내용 추출
recent_messages = messages[-80:]  # 마지막 80개 메시지
assistant_msgs = [
    m for m in recent_messages 
    if m.get("role") == "assistant" and isinstance(m.get("content"), str)
]

# 파일 변경 추적 (Write/Edit 도구 사용 내역)
changed_files = set()
for m in recent_messages:
    if m.get("role") == "assistant":
        content = m.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_input = block.get("input", {})
                    file_path = tool_input.get("file_path", "")
                    if file_path:
                        changed_files.add(file_path)

now = datetime.now()
timestamp = now.strftime("%Y.%m.%d %H:%M")
filename = now.strftime("%Y%m%d_%H%M%S")

handoff = f"""# 인수인계 기록

## {timestamp} (trigger: {trigger})

### 세션 정보
- 세션 ID: {input_data.get('session_id', 'N/A')}
- 총 메시지 수: {len(messages)}
- 트리거: {'수동 /compact' if trigger == 'manual' else '자동 컴팩트'}

### 변경된 파일
{chr(10).join(f'- `{f}`' for f in sorted(changed_files)) if changed_files else '- 없음'}

### 최근 대화 요약
{chr(10).join(f'- {m.get("content", "")[:200]}' for m in assistant_msgs[-5:])}

### 다음 작업
- [ ] (수동 작성 필요)

### 주의사항
- (수동 작성 필요)
"""

# 최신 인수인계서 (항상 덮어쓰기)
with open(os.path.join(project_dir, "HANDOFF.md"), "w") as f:
    f.write(handoff)

# 아카이브 (히스토리 보존)
with open(os.path.join(handoff_dir, f"handoff_{filename}.md"), "w") as f:
    f.write(handoff)