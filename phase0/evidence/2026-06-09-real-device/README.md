# Phase 0 Evidence - 2026-06-09 Real Device

이 디렉토리는 `phase0/verification-notes/2026-06-09-real-device.md`가 인용하는 작은 텍스트 증거만 선별해 커밋한다. 대용량 `environment.json`과 perf counter raw sample은 `phase0/runs/`에 로컬 보관하고, 노트 본문에는 장치 요약만 남긴다.

## Files

- `openai-phi-3.5-mini.jsonl`: npurun OpenAI-compatible server 측정 결과. usage가 없어 char/token 추정 기반이므로 Phase 0 게이트 판정에는 보조 수치로만 사용한다.
- `bench-phi-3.5-mini-summary.txt`: npurun bench의 warm summary와 핵심 tok/s.
- `backend-log-excerpt.txt`: Genie/QNN/HTP 적재와 backend config 직접 증거 발췌.

## Verdict Use

Phase 0 text gate의 속도 근거는 `bench-phi-3.5-mini-summary.txt`의 warm `aggregate tok/s (post ttft): 15.6`이다. `openai-phi-3.5-mini.jsonl`의 `token_count_source=estimated_chars_per_token` 값은 OpenAI-compatible 경로 smoke evidence이며 합격 기준의 단독 근거로 쓰지 않는다.
