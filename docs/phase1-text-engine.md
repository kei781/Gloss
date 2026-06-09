# Phase 1 Text Engine

Phase 1은 OCR 없이 접근 가능한 텍스트를 추출하고, Phase 0에서 검증한 OpenAI 호환 NPU 백엔드로 번역한다. GUI 리더는 아직 아니며, 첫 구현은 CLI 리더 출력과 메트릭 JSONL 기록이다.

## 실행 전 준비

NPU 백엔드 서버를 실행한다. 현재 실기에서 확인된 fallback은 `phi-3.5-mini`다.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\phase0\run_model_profile.ps1 `
  -Profile phi-3.5-mini `
  -Action serve
```

다른 터미널에서 `PYTHONPATH`를 지정하고 Text 엔진을 실행한다.

```powershell
$env:PYTHONPATH='src'
.\.venv-arm64\Scripts\python.exe -m gloss.text.cli `
  --profile phi-3.5-mini `
  --text "The moonlight fell softly over the old town." `
  --output .\runs\phase1\sample.md
Remove-Item Env:\PYTHONPATH
```

## 입력 모드

직접 텍스트:

```powershell
$env:PYTHONPATH='src'
.\.venv-arm64\Scripts\python.exe -m gloss.text.cli --profile phi-3.5-mini --text "Hello."
Remove-Item Env:\PYTHONPATH
```

UTF-8 텍스트/HTML 파일:

```powershell
$env:PYTHONPATH='src'
.\.venv-arm64\Scripts\python.exe -m gloss.text.cli --profile phi-3.5-mini --file .\samples\chapter.html
Remove-Item Env:\PYTHONPATH
```

URL:

```powershell
$env:PYTHONPATH='src'
.\.venv-arm64\Scripts\python.exe -m gloss.text.cli --profile phi-3.5-mini --url https://example.com/story
Remove-Item Env:\PYTHONPATH
```

## Dry Run

백엔드 없이 추출/분할/리더 출력만 확인한다.
실제 서버 smoke가 오래 걸리거나 멈춘 것처럼 보일 때는 먼저 이 경로로 Text 엔진 자체와 입력 추출을 분리 확인한다.

```powershell
$env:PYTHONPATH='src'
.\.venv-arm64\Scripts\python.exe -m gloss.text.cli `
  --dry-run `
  --text "This is a dry-run text block." `
  --show-source
Remove-Item Env:\PYTHONPATH
```

## 메트릭

기본 메트릭 경로는 `runs/phase1/text-metrics.jsonl`이다. 각 번역 block마다 다음 값을 기록한다.

- source kind/source/title
- model profile/model/backend base URL
- source/translated char count
- elapsed, TTFT, decode window
- completion/prompt tokens
- token count source
- decode tok/s, end-to-end tok/s
- usage raw payload

## 현재 한계

- URL 본문 추출은 표준 라이브러리 기반 휴리스틱이다. `readability/trafilatura`와 Playwright JS 렌더 경로는 다음 보강 대상이다.
- PDF 처리는 Phase 5 선택 기능으로 둔다.
- `qwen3-4b`는 기본 target profile이지만 bundle 확보 전까지 실기 실행은 `phi-3.5-mini` fallback을 사용한다.
