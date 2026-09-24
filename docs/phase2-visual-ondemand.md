# Phase 2 Visual On-Demand

Visual 엔진은 지정 영역을 캡처한 뒤 한국어 번역을 출력하거나 오버레이에 표시한다. 입력은 두 가지다.

- **OCR + 텍스트 LLM**: Windows.Media.Ocr가 이미지를 읽고, 검증된 `qwen3-4b` OVMS 모델이 번역한다. 현재 실기 권장 경로다.
- **VLM 이미지 직접 입력**: PNG/JPEG를 OpenAI 호환 `image_url` data URL로 OVMS에 보낸다. 클라이언트 연결은 완료됐지만 Qwen3-VL-4B의 Intel NPU vision encode와 속도는 아직 검증되지 않았다.
  기본적으로 긴 변을 1024px로 제한한다(`--vlm-max-edge 256~2048`). 게임 대사창은 캡처 rect를 타이트하게 잡아 작은 글자가 축소로 뭉개지지 않도록 한다.

## 설치

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[capture]'
```

`capture` extra는 x64 DXcam의 WinRT(Windows.Graphics.Capture)·DXGI 백엔드와 Pillow를 설치한다. 기본 `--capture-backend auto`는 WGC → DXGI → GDI 순서로 시도한다. `--capture-backend wgc`와 `--capture-backend dxgi`는 해당 경로의 실패를 바로 표시한다. GPU 캡처는 현재 주 모니터 기준이며, 다른 모니터의 영역은 GDI fallback을 사용한다.

## OCR 경로

```powershell
.\.venv\Scripts\gloss-visual.exe `
  --capture-rect "100,600,900,200" `
  --ocr-backend windows --ocr-language en-US `
  --profile qwen3-4b --overlay
```

커서 주위 영역을 글로벌 핫키로 번역하려면:

```powershell
.\.venv\Scripts\gloss-hover.exe --profile qwen3-4b --input-mode ocr --region "640,260"
```

`Ctrl+Alt+Z` 번역, `Ctrl+Alt+L` 오버레이 잠금, `Ctrl+Alt+Q` 종료. 오버레이를 잠그면 클릭이 뒤 창으로 통과한다.

## VLM 경로

OVMS에 이미지 입력을 지원하는 모델이 적재된 상태에서 실행한다. `qwen3-vl-4b` 프로파일은 후보 설정이며, 먼저 Phase 0의 NPU vision encode 게이트를 통과해야 한다.

```powershell
.\.venv\Scripts\gloss-visual.exe --profile qwen3-vl-4b --image-file .\dialog.png
.\.venv\Scripts\gloss-visual.exe --profile qwen3-vl-4b --capture-rect "100,600,900,200" --vlm --overlay
.\.venv\Scripts\gloss-hover.exe --profile qwen3-vl-4b --input-mode vlm
```

이미지 bytes는 base64로 요청에만 담기며, metrics JSONL에는 이미지 경로와 크기만 기록된다. 로컬 서버 주소를 사용한다. `--dry-run`으로 서버 없이 요청 분기와 출력 형식을 확인할 수 있다.

## 확인할 점

- `--capture-backend wgc`로 게임 캡처 PNG에 대사창이 실제로 보이는지 확인한다. 캡처가 불가능한 전체화면 게임은 창/테두리 없는 창 모드로도 점검한다.
- VLM 실기 검증에서는 OVMS 로그의 `EXECUTION_DEVICES: NPU`와 Intel AI Boost NPU LUID counter를 확인한다. `target_device=NPU` 설정만으로 vision encode의 NPU 실행을 단정하지 않는다.
- 대사창 샘플 10개에서 8개 이상이 8초 이내, 최악 12초 이내이며 의미 보존·무부연 출력을 만족하는지 기록한다(PRD 성공 기준).

메트릭은 `runs/phase2/visual-metrics.jsonl`에 남는다.
