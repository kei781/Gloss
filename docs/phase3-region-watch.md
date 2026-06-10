# Phase 3 Region Watch

Phase 3은 사용자가 지정한 화면 영역을 주기적으로 캡처하고, **내용이 바뀔 때만** 재번역해 오버레이/파일/표준출력으로 내보내는 경로다 (FR-V3). 대상은 **느린 전환**(슬라이드, 정지 자막)이며 실시간 흐르는 자막은 비목표다 (ADR-014).

## 동작 구조

```
캡처(주기) → 프레임 해시 diff → (변화 시) Windows OCR → OCR 텍스트 dedupe → NPU 번역 → 출력
```

- **프레임 diff**: 캡처 PNG의 SHA-256. 픽셀 하나만 바뀌어도 변화로 간주하므로, 감시 rect는 애니메이션·커서·시계가 없는 영역으로 잡는다.
- **텍스트 dedupe**: 프레임이 바뀌어도 OCR 텍스트가 직전과 같으면 번역을 건너뛴다 (배경만 바뀐 경우).
- **OCR**: `Windows.Media.Ocr` (OS 내장, CPU helper — ADR-016). NFR-1의 "선택적 경량 OCR" 예외 경로이며, LLM 번역은 그대로 NPU 백엔드를 쓴다.
- **번역**: Phase 2와 동일한 `VisualEngine` / OpenAI 호환 백엔드.

## 설치

```powershell
.\.venv-arm64\Scripts\python.exe -m pip install -e .
```

`gloss-watch` 콘솔 스크립트가 추가된다. 스크립트 경로(`scripts/phase3/ocr_image_text.ps1`)는 저장소 루트 기준 상대 경로이므로 **저장소 루트에서 실행**한다.

## OCR 언어 준비

Windows OCR은 설치된 언어팩의 OCR 기능만 쓸 수 있다. 현재 실기에는 `en-US`, `ko`만 설치되어 있다.

사용 가능 언어 확인:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\phase3\ocr_image_text.ps1 -ListLanguages
```

일본어 게임/소설 대상이면 일본어 OCR 팩을 설치한다 (관리자 PowerShell):

```powershell
Add-WindowsCapability -Online -Name "Language.OCR~~~ja-JP~0.0.1.0"
```

`ja`/`zh` 계열은 Windows OCR이 단어 사이에 공백을 끼워 넣으므로, Gloss가 줄 단위로 공백을 제거해 번역에 넘긴다.

## Dry Run (백엔드 없이 캡처+OCR 루프 확인)

```powershell
.\.venv-arm64\Scripts\gloss-watch.exe `
  --watch-rect "100,600,900,200" `
  --dry-run `
  --max-iterations 3 `
  --ocr-language en-US
```

## 실행 (백엔드 + 오버레이)

백엔드를 먼저 띄운다:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\phase0\run_model_profile.ps1 `
  -Profile phi-3.5-mini -Action serve
```

감시 시작 (Ctrl+C로 종료):

```powershell
.\.venv-arm64\Scripts\gloss-watch.exe `
  --watch-rect "100,600,900,200" `
  --profile phi-3.5-mini `
  --ocr-language en-US `
  --overlay --overlay-rect "80,840,1000,160" `
  --output .\runs\phase3\watch-log.md
```

주요 옵션:

| 옵션 | 기본 | 설명 |
|---|---|---|
| `--interval` | 2.0 | 캡처 주기(초). 느린 전환용이므로 1~5초 권장 |
| `--stability` | 0 | 변화 감지 후 N프레임 연속 동일해야 처리 (전환 중간 프레임 방지). 3N+3회 재캡처 안에 안정화되지 않으면 WARN 후 마지막 프레임을 그대로 처리 |
| `--min-text-chars` | 2 | OCR 결과가 이보다 짧으면 건너뜀 |
| `--keep-captures` | off | 캡처 PNG 보존 (기본은 처리 후 삭제) |
| `--max-iterations` | 무제한 | N회 후 종료 (테스트용) |

## 온디맨드 경로에서도 OCR 사용 (FR-V2 보강)

`gloss-visual`에도 `--ocr-backend windows`가 추가되어, `--ocr-text` 수동 입력 없이 캡처→OCR→번역이 한 번에 된다:

```powershell
.\.venv-arm64\Scripts\gloss-visual.exe `
  --capture-rect "100,600,900,200" `
  --ocr-backend windows --ocr-language en-US `
  --profile phi-3.5-mini --overlay
```

## 메트릭

`runs/phase3/watch-metrics.jsonl`에 번역 1건당 1행. Phase 2 visual 스키마에 `"phase": 3`, `"inputMode": "watch_ocr"`, `watch`(iteration/interval/stability), `ocr`(backend/언어/소요시간) 필드가 추가된다.

## 한계

- 프레임 diff가 픽셀 단위라 노이즈(애니메이션 배경, 페이드 효과)가 있으면 매 주기 변화로 감지된다 → rect를 타이트하게 잡고 `--stability`로 완화 (단, 끝내 안정화되지 않으면 마지막 프레임을 처리하므로 완전한 억제는 아님 — OCR 텍스트 dedupe가 2차 방어선).
- Windows OCR은 스타일라이즈드 게임 폰트에서 약할 수 있다. 이 경로는 ADR-013의 "경량 OCR + 소형 LLM" 탈출구이며, VLM bundle 확보 시 같은 watch 계약에 VLM 백엔드를 연결한다.
- 빠른 흐름(실시간 자막)은 캡처-번역 레이턴시상 비대상. Windows Live Captions에 위임 (ADR-014).
