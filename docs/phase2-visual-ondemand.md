# Phase 2 Visual On-Demand

Phase 2는 화면 영역을 캡처하고, 보이는 텍스트를 한국어 번역으로 만들어 고정 오버레이에 표시하는 경로다.

현재 실기에서는 VLM bundle과 WGC Python binding이 아직 확보되지 않았기 때문에 첫 구현은 다음 범위를 제공한다.

- rect 기반 화면 캡처 helper
- OCR/VLM이 붙을 수 있는 Visual 엔진 인터페이스
- OCR 텍스트 fallback 번역 경로
- 클릭 통과 overlay 프로토타입
- Phase 2 전용 metrics JSONL

## 설치

`pyproject.toml`에 `gloss-visual` 콘솔 스크립트가 추가되었으므로 editable install을 다시 실행한다.

```powershell
.\.venv-arm64\Scripts\python.exe -m pip install -e .
```

## 캡처 Dry Run

현재 캡처 helper는 개발용 `gdi-copy-from-screen` backend다. 전체화면 DirectX 검증용 WGC backend는 다음 보강 대상이다.
Codex 샌드박스나 비대화형 세션에서는 `CopyFromScreen`이 `The handle is invalid`로 실패할 수 있으므로,
캡처 검증은 실제 사용자 데스크톱 PowerShell에서 실행한다.
이 helper는 stdout JSON을 UTF-8로 고정하고, 배율 디스플레이의 좌표 어긋남을 줄이기 위해
per-monitor DPI awareness를 시도한다.

```powershell
.\.venv-arm64\Scripts\gloss-visual.exe `
  --dry-run `
  --capture-rect "100,100,800,260" `
  --output .\runs\phase2\capture-dry-run.md
```

캡처 이미지는 기본적으로 `runs/phase2/captures`에 저장된다.

## OCR 텍스트 Fallback 번역

VLM/OCR이 아직 연결되지 않은 상태에서는 보이는 텍스트를 `--ocr-text`나 `--ocr-file`로 넣어 visual 번역/메트릭/오버레이 경로를 검증한다.

```powershell
.\.venv-arm64\Scripts\gloss-visual.exe `
  --profile phi-3.5-mini `
  --ocr-text "星間国家の悪徳領主として、俺は領民から搾取するつもりだった。" `
  --output .\runs\phase2\visual-ocr-text-live.md
```

## Overlay 확인

```powershell
.\.venv-arm64\Scripts\gloss-visual.exe `
  --dry-run `
  --ocr-text "The old town slept under moonlight." `
  --overlay `
  --overlay-rect "80,720,1000,180" `
  --overlay-duration 6
```

확인할 점:

- overlay가 지정 위치에 뜨는지
- 항상 위에 보이는지
- 마우스 클릭이 overlay 아래 창으로 통과하는지
- 텍스트가 박스 안에서 읽을 수 있게 줄바꿈되는지

## Phase 2 이후 실기 체크

- VN/RPG 대사창 샘플 10개를 준비한다.
- `--capture-rect`가 대사창을 과하게 넓거나 좁게 잡지 않는지 확인한다.
- 전체화면 DirectX 게임에서는 현재 GDI fallback이 검은 화면일 수 있다. 이 경우 WGC backend 구현 전까지 실패로 기록한다.
- `--ocr-text` fallback으로 번역/오버레이가 8초 안에 나오는지 먼저 본다.
- VLM bundle이 확보되면 image input backend를 같은 VisualEngine 계약에 연결한다.
- metrics는 `runs/phase2/visual-metrics.jsonl`에서 확인한다.
