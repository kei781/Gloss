# Gloss

Windows 11 x64용 로컬 번역 도구입니다. 화면의 텍스트를 캡처해 Intel NPU에서 실행되는 OVMS 모델로 번역하고, 결과를 오버레이에 표시합니다. 텍스트 파일과 URL 번역, 영역 감시, 데스크톱 리더 및 대시보드도 제공합니다.

## 대상 장비

- Intel Core Ultra **X7 358H** (Series 3, Panther Lake), Intel AI Boost NPU, Intel Arc B390 GPU
- 개발 실기: Windows 11 x64, RAM 32GB
- Intel 공식 사양: 16코어(4P+8E+4 LP-E), NPU 최대 50 INT8 TOPS, LPDDR5X 최대 9600 MT/s. 이 수치는 프로세서 사양이며 모델 처리 속도는 아닙니다. [Intel 제품 사양](https://www.intel.com/content/www/us/en/products/sku/245527/intel-core-ultra-x7-processor-358h-18m-cache-up-to-4-80-ghz/specifications.html)
- 실기 검증: Qwen3-4B-Instruct-2507 INT4의 **텍스트 생성 37.23 tok/s**, NPU 직접 증거 확보. VLM의 vision encode는 아직 실기 검증 전입니다. [검증 노트](phase0/verification-notes/2026-06-20-intel358h.md)

## 시작

PowerShell에서 Python 3.11+ x64로 설치합니다.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[capture,pdf,web,gui]'
.\.venv\Scripts\python.exe -m playwright install chromium
```

OVMS와 Intel NPU 드라이버, OpenVINO IR 모델은 별도로 준비해야 합니다. 모델 프로파일과 포트는 `phase0/model-profiles.json`에 있습니다. Raw Hugging Face 모델에서 INT4 IR을 만들려면 Python/Optimum export 기능을 포함한 OVMS 빌드가 필요합니다.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\phase0\run_model_profile.ps1 -Profile qwen3-4b -Action pull -PrintOnly
powershell -ExecutionPolicy Bypass -File .\scripts\phase0\run_model_profile.ps1 -Profile qwen3-4b -Action pull
powershell -ExecutionPolicy Bypass -File .\scripts\phase0\run_model_profile.ps1 -Profile qwen3-4b -Action serve
```

`pull`은 INT4 OpenVINO 모델 준비 명령입니다. 사용 중인 OVMS 빌드에서 이 기능이 지원되지 않으면 [Phase 0 검증 노트](phase0/verification-notes/2026-06-20-intel358h.md)의 `optimum-cli export openvino` 명령으로 준비하세요. 모델 다운로드와 변환은 시간이 걸릴 수 있으며, 실제 NPU 적재 여부는 OVMS 로그와 NPU counter로 확인해야 합니다.

`qwen3-vl-4b`는 [OpenVINO의 사전 변환 INT4 IR](https://huggingface.co/OpenVINO/Qwen3-VL-4B-Instruct-int4-ov)을 pull하도록 설정했습니다. 약 3GB 모델 다운로드와 NPU 적재·vision encode 검증은 아직 수행되지 않았습니다.
NPU 프로파일은 OVMS의 비배칭 `LM`/`VLM` 파이프라인을 명시합니다. [OVMS 문제 해결 문서](https://docs.openvino.ai/2026/model-server/ovms_docs_troubleshooting.html)에 따르면 NPU는 연속 배칭 파이프라인을 사용할 수 없습니다.

다른 PowerShell에서:

```powershell
.\.venv\Scripts\gloss-text.exe --profile qwen3-4b --text "The moonlight fell softly over the old town."
.\.venv\Scripts\gloss-hover.exe --profile qwen3-4b --input-mode ocr --ocr-language en-US
.\.venv\Scripts\gloss-dashboard.exe --once
.\.venv\Scripts\gloss-dashboard.exe --npu-luid 0x11b60
.\.venv\Scripts\gloss-reader.exe --profile qwen3-4b
.\.venv\Scripts\gloss-dashboard-gui.exe --npu-luid 0x11b60
```

텍스트 PDF는 페이지별로 본문을 추출하고, 글자가 없는 스캔 페이지는 PDFium 렌더링 후 Windows OCR로 읽습니다. 스캔 문서의 언어를 지정할 수 있습니다.

```powershell
.\.venv\Scripts\gloss-text.exe --profile qwen3-4b --file .\paper.pdf --pdf-ocr-language en-US
.\.venv\Scripts\gloss-text.exe --profile qwen3-4b --url https://example.com/story --next-pages 2
.\.venv\Scripts\gloss-text.exe --profile qwen3-4b --url https://example.com/story --render-js
```

호버 단축키는 `Ctrl+Alt+Z` 번역, `Ctrl+Alt+L` 오버레이 잠금, `Ctrl+Alt+Q` 종료입니다. Visual 기본 경로는 Windows OCR + 텍스트 모델입니다. VLM 이미지 요청은 `gloss-visual --image-file` 또는 `gloss-hover --input-mode vlm`로 연결되어 있지만, Qwen3-VL-4B의 Intel NPU vision encode 검증 전에는 실사용 성능을 보장할 수 없습니다. `--profile qwen3-vl-4b`를 명시하고, 서버에 해당 모델이 적재됐는지 확인하세요.

```powershell
.\.venv\Scripts\gloss-visual.exe --profile qwen3-vl-4b --image-file .\dialog.png
.\.venv\Scripts\gloss-hover.exe --profile qwen3-vl-4b --input-mode vlm
```

캡처 기본값은 WGC → DXGI → GDI `CopyFromScreen` 순서로 시도합니다. `--capture-backend wgc`나 `--capture-backend dxgi`로 각 경로를 따로 확인할 수 있습니다. 전체화면 DirectX 게임의 실제 캡처 성공 여부는 사용자 데스크톱에서 검증해야 합니다.

세부 사용법: [Text](docs/phase1-text-engine.md), [Visual](docs/phase2-visual-ondemand.md), [영역 감시](docs/phase3-region-watch.md), [대시보드](docs/phase4-dashboard.md).
