# Phase 0 NPU 검증 가이드

Phase 0의 목적은 Gloss 본 구현 전에 Intel Core Ultra 358H(Intel AI Boost NPU) 장비에서 후보 백엔드와 모델이 실제로 NPU(device=NPU)에 적재되고 실행되는지 확인하는 것이다. 이 단계는 기능 구현이 아니라 **게이트 검증**이다.

> 기기 변경 이력: 이전 Snapdragon X Plus(Hexagon NPU, npurun/Genie/QNN) 경로는 deprecated다(ADR-001 → ADR-016). Hexagon 시절 검증 기록은 `phase0/verification-notes/2026-06-09-real-device.md`에 보존한다.

## 산출물

- `phase0/verification-note-template.md`: 검증 결과를 남기는 노트 템플릿
- `phase0/directory-structure.md`: Phase 0 현재 구조와 이후 구현 확장 구조
- `phase0/evidence/<date>/`: 검증 노트가 인용하는 작은 텍스트 증거
- `phase0/.env.example`: 로컬 env 예시. 실제 `phase0/.env`는 git에 올리지 않음
- `phase0/config.example.json`: 측정 스크립트 입력 예시
- `phase0/model-profiles.json`: 교체 가능한 모델 프로파일 목록
- `scripts/phase0/common.ps1`: PowerShell용 `log()`와 env loader
- `scripts/phase0/phase0_common.py`: Python용 `log()`와 env loader
- `scripts/phase0/collect_windows_env.ps1`: Windows/장치(Intel NPU)/NPU counter 후보 수집
- `scripts/phase0/run_model_profile.ps1`: 선택한 모델 프로파일로 OVMS(OpenVINO Model Server, device=NPU) 실행
- `scripts/phase0/run_model_profile.npurun.ps1`: (DEPRECATED) Snapdragon/Hexagon 시절 npurun 드라이버, 재현/참고용
- `scripts/phase0/measure_openai_backend.py`: OpenAI 호환 백엔드의 TTFT/tok/s 측정

## 로그와 env 계약

Phase 0 스크립트의 모든 콘솔 출력은 `log()` 함수를 통과한다.

- Python: `scripts/phase0/phase0_common.py`의 `log()`
- PowerShell: `scripts/phase0/common.ps1`의 `log()`

나중에 로그를 파일, JSONL, 앱 대시보드, IPC 등으로 받게 되면 각 공통 파일의 `log()`만 바꾼다. 스크립트 본문에서 `print()`나 `Write-Host`를 직접 추가하지 않는다.

로컬 경로와 비밀값은 env에서 관리한다. `phase0/.env.example`을 `phase0/.env`로 복사한 뒤 장비별 값을 수정한다. 실제 `.env`는 git에 올라가지 않는다. 공유 기본값(`run_id`, active profile, `runs`, `max_tokens`, prompt, output 규칙)은 `phase0/config.example.json`과 `phase0/model-profiles.json`을 단일 source로 둔다.

주요 env key:

- `GLOSS_PHASE0_API_KEY`: 로컬 backend API key
- `GLOSS_PHASE0_OVMS_PATH`: OVMS(OpenVINO Model Server) 실행 파일
- `GLOSS_PHASE0_TARGET_DEVICE`: OpenVINO 타겟 디바이스(기본 `NPU`)
- `GLOSS_PHASE0_MODELS_DIR`: OpenVINO IR 모델 저장소(`.models/ovms`)

필요할 때만 쓰는 override:

- `GLOSS_PHASE0_ACTIVE_MODEL_PROFILE`: 현재 모델 프로파일 override
- `GLOSS_PHASE0_MODEL`: 런타임 모델명 직접 override. 비워두면 profile의 `runtime_model` 사용
- `GLOSS_PHASE0_BASE_URL`: OpenAI 호환 endpoint override
- `GLOSS_PHASE0_OUTPUT_DIR`: 측정 산출물 저장 위치 override

## 모델 교체 구조

모델명은 실행 명령이나 측정 코드에 직접 박지 않는다.

- `phase0/model-profiles.json`: 후보 모델의 런타임 이름, 백엔드, 산출 파일명, 상태를 정의한다.
- `phase0/config.example.json`의 `active_model_profile`: 현재 기본 모델을 고른다.
- CLI의 `--profile` 또는 PowerShell의 `-Profile`: 임시로 다른 모델을 고른다.

기본 프로파일은 `qwen3-4b`이며, 런타임 모델명은 `qwen3-4b-instruct-2507`이다. Intel NPU에서 쓰려면 OpenVINO IR(.xml/.bin)로 export해야 한다(예: `optimum-cli export openvino --model Qwen/Qwen3-4B-Instruct-2507 --weight-format int4`). `phi-3.5-mini`는 텍스트 fallback 프로파일이며 Intel NPU(OVMS) 경로 재검증이 필요하다. Snapdragon/Hexagon 시절 검증본은 deprecated 프로파일 `phi-3.5-mini-hexagon`으로 보존한다.

모델을 바꾸는 방법:

```json
{
  "active_model_profile": "qwen3-4b"
}
```

또는 실행 시점에만 바꾼다.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\phase0\run_model_profile.ps1 `
  -Profile phi-3.5-mini `
  -Action serve

python .\scripts\phase0\measure_openai_backend.py `
  --config .\phase0\config.example.json `
  --profile phi-3.5-mini
```

## 검증 순서

1. 후보 백엔드를 실행한다.
   - 1순위: OVMS(OpenVINO GenAI, device=NPU)
   - 2순위: standalone OpenVINO GenAI
   - 3순위: ONNX Runtime + OpenVINO/DirectML EP
2. Windows 환경과 NPU counter 후보를 수집한다.
3. 텍스트 모델에 짧은 번역 요청을 3회 이상 보낸다.
4. VLM 모델에 이미지 입력 요청을 3회 이상 보내 vision encode 경로를 확인한다.
5. 작업 관리자, PDH counter, OVMS 서버 로그(target_device=NPU)·OpenVINO NPU plugin 로그, ETW/perf trace 중 하나 이상으로 NPU 직접 증거를 남긴다.
6. 결과를 `phase0/verification-note-template.md` 형식으로 정리한다.

## Windows 환경 수집

PowerShell에서 실행한다.
Counter 후보 탐색은 장비/권한 상태에 따라 몇 분 걸릴 수 있다.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\phase0\collect_windows_env.ps1 `
  -OutputDir .\phase0\runs\2026-06-09-xplus
```

생성되는 파일:

- `environment.json`: OS/CPU/메모리/비디오 컨트롤러/Intel NPU 관련 장치 정보
- `performance-counter-candidates.json`: NPU/Neural/AI/GPU 관련 counter set 후보
- `counter-sample.json`: 읽을 수 있는 후보 counter의 짧은 샘플

## 텍스트 모델 측정

백엔드는 프로파일에 맞춰 `OVMS`(OpenVINO Model Server, device=NPU)로 띄운다. 기본값은 `qwen3-4b`다.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\phase0\run_model_profile.ps1 `
  -Action serve
```

다른 터미널에서 OpenAI 호환 엔드포인트를 측정한다. `--config`만 넘기면 `active_model_profile`, `base_url`, `model`, 출력 파일명을 모두 설정에서 읽는다.

```powershell
python .\scripts\phase0\measure_openai_backend.py `
  --config .\phase0\config.example.json
```

## VLM vision encode 측정

이미지 파일을 함께 넘기면 OpenAI chat/completions의 `image_url` content payload로 요청한다. VLM 후보가 생기면 `phase0/model-profiles.json`에 새 profile을 추가하고 `active_model_profile` 또는 `--profile`로 선택한다.

```powershell
python .\scripts\phase0\measure_openai_backend.py `
  --config .\phase0\config.example.json `
  --profile qwen3-vl-4b `
  --image .\phase0\samples\dialog.png `
  --prompt "이미지에 보이는 외국어 텍스트만 한국어로 번역해줘." `
  --output .\phase0\runs\2026-06-09-xplus\vlm-benchmark.jsonl
```

## 합격 기준

- ~4B 모델이 `> 5 tok/s`를 달성한다.
- `> 5 tok/s` 게이트 판정은 `usage` 기반 token count가 있는 stream decode 측정 또는 벤치마크의 실토큰/명시 토큰 측정값을 우선 사용한다.
- `token_count_source != "usage"`인 OpenAI 측정값은 CJK 출력에서 부정확할 수 있으므로 보조 수치로만 기록한다.
- `--no-stream` 측정은 프리필 포함 end-to-end 처리량이며 decode tok/s가 아니므로 게이트 판정값으로 쓰지 않는다.
- NPU 사용 직접 증거가 최소 1개 있다.
- 텍스트 모델과 VLM 경로의 결과가 분리 기록되어 있다.
- vision encode가 CPU fallback이면 그 사실을 명시하고 Phase 2 경로 결정을 남긴다.

`tok/s + CPU 유휴`는 보조 증거다. 단독 합격 근거로 쓰지 않는다.

## 불합격 기준

- 응답은 나오지만 NPU 직접 증거가 없다.
- NPU%를 읽을 수 있는데 생성 중 계속 0%다.
- ~4B 모델이 5 tok/s 이하이며 모델 축소 외 개선 여지가 없다.
- 백엔드가 Intel NPU 드라이버/OpenVINO NPU plugin 적재, 모델 포맷(IR), 동적 shape 문제로 재현 불가능하게 실패한다.
