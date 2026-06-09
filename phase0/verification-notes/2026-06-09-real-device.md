# Phase 0 검증 노트 — 2026-06-09 실기

## 요약

| 항목 | 값 |
|---|---|
| 검증일 | 2026-06-09 |
| 검증자 | Codex / kei78 실기 |
| 장비 | Snapdragon X Plus / X1P-42-100 / 32GB |
| OS 빌드 | Windows 11 Home 10.0.26200 / ARM 64-bit Processor |
| 드라이버/런타임 | Qualcomm Adreno X1-45 GPU driver 31.0.98.1, onnxruntime-qnn 2.2.0, libGenie 1.18.0 |
| 최종 판정 | Partial |

Partial 이유:

- `phi-3.5-mini` 텍스트 모델은 `npurun` + Genie/QNN/HTP 경로로 실행되고 `> 5 tok/s` 기준을 통과했다.
- VLM/vision encode 경로는 아직 검증하지 못했다. `npurun v0.1.0-rc.2` 내장 registry에는 VLM 모델이 없고, NexaAI SDK 설치는 S3 `nexasdk-bridge.zip` 403으로 실패했다.

## 백엔드 후보

| 순위 | 백엔드 | 버전 | 모델 | 결과 | 비고 |
|---|---|---|---|---|---|
| 1 | npurun(Genie) | 0.1.0-rc.2 | phi-3.5-mini | Partial pass | Text 모델 NPU 실행 성공 |
| 2 | NexaSDK | nexaai 1.0.44 / 1.0.43 시도 | 없음 | Fail | `nexasdk-bridge.zip` 다운로드 403 |
| 3 | ONNX Runtime + QNN EP | onnxruntime 1.24.4 / onnxruntime-qnn 2.2.0 | 없음 | Support only | `Genie.dll`, `QnnHtp.dll` 공급원으로 사용 |

## 환경 수집

| 항목 | 파일/값 |
|---|---|
| 환경 JSON | `phase0/runs/real-device/environment.json` |
| counter 후보 JSON | `phase0/runs/real-device/performance-counter-candidates.json` |
| counter 샘플 JSON | `phase0/runs/real-device/counter-sample.json` |
| 작업 관리자 스크린샷 | 미수집 |
| 백엔드 로그 | `phase0/runs/real-device/npurun/run-phi-3.5-mini.txt`, `bench-phi-3.5-mini.txt`, `serve-stderr.txt` |
| ETW/perf trace | 미수집 |

장치 요약:

- OS: Microsoft Windows 11 Home, build 26200, ARM 64-bit Processor
- CPU: Snapdragon(R) X Plus - X1P42100 - Qualcomm(R) Oryon(TM) CPU, 8 cores / 8 logical processors
- GPU: Qualcomm(R) Adreno(TM) X1-45 GPU, driver 31.0.98.1
- NPU: `setup-qnn.ps1`에서 `Snapdragon(R) X Plus - X1P42100 - Qualcomm(R) Hexagon(TM) NPU [OK]` 확인

## 설치/런타임 경로

ARM64 Python:

```text
C:\agent\Gloss\.tools\Python313-arm64\python.exe
platform.machine() = ARM64
```

NexaAI:

```text
nexaai 1.0.44: Failed to download binaries: HTTP Error 403
nexaai 1.0.43: Failed to download binaries: HTTP Error 403
blocked URL: https://nexa-model-hub-bucket.s3.us-west-1.amazonaws.com/public/nexasdk/v*/windows_arm64/nexasdk-bridge.zip
```

npurun:

```text
npurun 0.1.0-rc.2 aarch64 Windows ZIP
QNN_SDK_ROOT/PATH = C:\agent\Gloss\.venv-arm64\Lib\site-packages\onnxruntime_qnn
```

`onnxruntime-qnn` 패키지에서 확인한 런타임 DLL:

```text
Genie.dll
QnnHtp.dll
QnnSystem.dll
QnnCpu.dll
onnxruntime_providers_qnn.dll
```

`npurun version`:

```text
npurun       0.1.0-rc.2
libGenie     1.18.0
QAIRT SDK    (sdk.yaml not parseable; root: C:\agent\Gloss\.venv-arm64\Lib\site-packages\onnxruntime_qnn)
```

## 텍스트 모델 측정

| 항목 | 값 |
|---|---|
| 모델 | phi-3.5-mini |
| 양자화 | W4A16 |
| 실행 명령 | `npurun run phi-3.5-mini ...`, `npurun bench phi-3.5-mini`, `npurun serve --model phi-3.5-mini` |
| 결과 JSONL | `phase0/runs/real-device/npurun/openai-phi-3.5-mini.jsonl` |
| 평균 TTFT | OpenAI 측정 평균 약 0.08s, bench warm 평균 102.40ms |
| 평균 tok/s | bench warm aggregate post-TTFT 15.6 tok/s |
| token count source | bench approx tokens, OpenAI 측정은 usage 미제공으로 char estimate |
| CPU/RAM 관찰 | 별도 정량 미수집 |
| NPU 직접 증거 | Genie/QNN/HTP backend config 및 npurun backend log |
| 판정 | Pass |

`npurun show phi-3.5-mini`:

```text
name: phi-3.5-mini
arch: phi3
quant: W4A16
context: 4096
qnn_sdk: 2.43.1
genie_config: .../genie_config.json
```

`npurun bench` warm summary:

```text
queries:                  3
avg total per query:      13.50s
avg time-to-first-token:  102.40ms
avg generation time:      13.40s
aggregate tok/s (incl ttft): 15.5
aggregate tok/s (post ttft): 15.6
```

OpenAI 호환 서버 측정:

```text
runs: 3
successful: 3
avg tokens_per_second: 10.09
avg ttft_s: 0.08
```

## VLM 측정

| 항목 | 값 |
|---|---|
| 모델 | 미검증 |
| 양자화 | 미검증 |
| 이미지 샘플 | 미사용 |
| 실행 명령 | 없음 |
| 결과 JSONL | 없음 |
| 평균 TTFT | 없음 |
| 평균 tok/s | 없음 |
| vision encode NPU 증거 | 없음 |
| CPU fallback 여부 | 알 수 없음 |
| 판정 | Fail / Not tested |

사유:

- `npurun v0.1.0-rc.2` built-in registry에는 `phi-3.5-mini`, `llama-v3-1-8b-instruct`, `qwen-2-5-7b`만 있다.
- Qwen3-VL-4B 또는 동급 VLM Genie bundle을 확보하지 못했다.
- NexaAI SDK는 Windows ARM64 bridge 바이너리 다운로드가 403으로 실패했다.

## 직접 증거

- [ ] 작업 관리자 NPU% > 0
- [ ] PDH NPU counter sample
- [x] Genie/QNN/HTP 로그
- [ ] 벤더 샘플 출력
- [ ] ETW/perf trace

증거 설명:

```text
npurun log:
loaded manifest name=phi-3.5-mini arch=phi3 quant=W4A16 qnn_sdk=2.43.1 backend=Genie
Genie dialog created (libGenie 1.18.0)
[INFO] "Using create From Binary"
[INFO] "Allocated total size = 816554496 across 7 buffers"

genie_config.json:
"backend": {
  "type": "QnnHtp",
  "QnnHtp": {
    "use-mmap": true,
    "poll": true,
    "cpu-mask": "0xe0"
  }
}

htp_backend_ext_config.json:
"dsp_arch": "v73"
"mem_type": "shared_buffer"
```

주의:

- PDH counter 후보에는 NPU 이름이 직접 노출되지 않았다. 일반 `GPU Engine(*)` counter의 `compute` 인스턴스는 별도 샘플 시점 0%였다.
- Task Manager NPU% 스크린샷은 아직 남기지 않았다.

## 결론

### 합격 여부

Partial

### 최종 모델/사이즈 결정

- Text: `phi-3.5-mini`는 Phase 0 속도 기준 통과. 다만 한국어 번역 품질은 Gloss 요구에는 부족할 수 있다.
- Visual: 미확정.
- Vision encode: 미검증.

### 다음 Phase에 넘길 결정

- Phase 1 Text 엔진에서 사용할 백엔드: `npurun serve --model phi-3.5-mini`로 OpenAI 호환 HTTP 경로는 사용 가능.
- Phase 2 Visual 온디맨드에서 사용할 경로: VLM bundle 확보 전까지 확정 불가.
- 경량 OCR + 소형 LLM fallback 필요 여부: 필요 가능성 높음. 현재 검증된 NPU 경로는 텍스트 LLM뿐이다.

### 실패/위험 기록

```text
1. NexaAI SDK 설치 실패
   - nexaai 1.0.44 / 1.0.43 모두 nexasdk-bridge.zip S3 403으로 실패.

2. Qwen3/Qwen3-VL registry 부재
   - npurun v0.1.0-rc.2 내장 registry에는 qwen3-4b/qwen3-vl-4b가 없음.

3. 공식 QAIRT SDK 미설치
   - onnxruntime-qnn 2.2.0 패키지의 DLL로 npurun 런타임 실행은 가능했지만,
     정식 SDK 헤더와 sdk.yaml은 없음.

4. 직접 NPU 사용률 스크린샷 미확보
   - Genie/QNN/HTP config/log는 확보했지만 작업 관리자 NPU% > 0 스크린샷은 미수집.
```
