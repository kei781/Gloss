# Gemma 4 한국어 번역·Intel NPU 평가 (2026-09-24)

## 선택

- **품질 우선 후보: Gemma 4 E4B-it.** Google 모델 카드 기준 4.5B effective, 임베딩 포함 총 8B 파라미터다. NPU 메모리와 지연 시간을 Qwen3-4B와 별도로 측정한다.
- **속도 후보: Gemma 4 E2B-it.** 2.3B effective, 임베딩 포함 총 5.1B 파라미터다.
- **현재 동작 기준선: Qwen3-4B-Instruct-2507.** 이 장비의 Intel NPU에서 INT4 텍스트 디코드 37.23 tok/s를 실측했다. Gemma 4가 한국어 번역에서 더 나은지는 같은 입력의 비교 평가가 필요하다.

Gemma 4 E2B/E4B는 텍스트·이미지·오디오 입력을 지원한다. Gloss의 현재 API 경로는 텍스트와 이미지 입력만 연결되어 있다. Google은 다국어 지원을 명시하지만, 모델 카드에 **Qwen3 대비 한국어 번역 우위**를 입증하는 비교는 없다. 사용자 선호에 따라 Gemma 4를 우선 평가하되, 품질 판정은 실측한다.

## NPU 호환성 근거와 제한

1. OpenVINO 2026.4 릴리스 노트는 NPU에서 Gemma4-E2B/E4B의 긴 컨텍스트 TTFT 개선을 명시한다. 이는 **OpenVINO NPU 플러그인**의 지원 근거이지, OVMS의 Gemma 4 VLM 전체 파이프라인이 X7 358H에서 동작했다는 증거는 아니다.
2. OpenVINO 검증 표에는 E2B의 INT8-CW와 FP4-NORMALIZED가 NPU 통과로 표시되어 있다. INT4-MIXED의 NPU 칸은 비어 있다. 변환 방식별 검증 결과를 혼동하지 않는다.
3. 공식 사전 변환 [E2B INT4](https://huggingface.co/OpenVINO/gemma-4-E2B-it-int4-ov)와 [E4B INT4](https://huggingface.co/OpenVINO/gemma-4-E4B-it-int4-ov)는 **INT4_ASYM/group 128**이다. 반면 OpenVINO NPU GenAI 안내는 **대칭 INT4**와 모델 크기에 따른 group 128 또는 channel-wise를 권장한다. E4B 사전 변환 모델은 OpenVINO 측에서도 실험적이라고 표시한다. 따라서 이 두 IR을 NPU용으로 바로 채택하지 않는다.
4. 위 IR은 OpenVINO **2026.4.0 이상**, Optimum Intel **2.2.0 이상**을 요구한다. 기존 Phase 0 실측 환경은 OVMS/OpenVINO **2026.2.1**이었다. OVMS 2026.3 릴리스 노트에는 Gemma 4의 CPU 전용 제한이 명시되어 있었고, 2026.4의 OVMS 릴리스 노트만으로는 Gemma 4 NPU VLM 경로의 성공을 확정할 수 없다.
5. Gemma 4의 128K는 모델의 최대 컨텍스트다. NPU에서 그 길이를 사용할 수 있다는 뜻은 아니다. 평가 프로파일은 메모리 부담을 줄이기 위해 `max_prompt_len=2048`로 시작한다.

## X7 358H에서 시험하는 순서

1. Windows용 OVMS **2026.4 이상, Python/Optimum export 지원 빌드**와 Intel NPU 드라이버를 준비한다. OVMS에 포함된 Optimum Intel이 Gemma 4 변환을 지원하는지 확인한다.
2. `gemma-4-e4b`를 먼저 시도한다. 저장 공간이나 적재 시간·번역 지연이 부담되면 `gemma-4-e2b`를 시도한다. 아래 `pull`은 Google 원본에서 **대칭 INT4/group 128** IR 내보내기를 요청한다. 이 변환 경로 자체도 아직 실기 검증 전이며, 모델 다운로드와 변환이 필요하다.

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\phase0\run_model_profile.ps1 -Profile gemma-4-e4b -Action pull -PrintOnly
   powershell -ExecutionPolicy Bypass -File .\scripts\phase0\run_model_profile.ps1 -Profile gemma-4-e4b -Action pull
   powershell -ExecutionPolicy Bypass -File .\scripts\phase0\run_model_profile.ps1 -Profile gemma-4-e4b -Action serve
   ```

3. 서버 로그의 **NPU plugin 적재 및 실제 추론**, NPU 사용률 또는 ETW/VTune trace를 남긴다. `target_device=NPU` 명령 문자열이나 CPU 유휴만으로 통과시키지 않는다. OVMS가 실패하면 실패 로그와 런타임 버전을 기록하고 standalone OpenVINO GenAI NPU 경로를 평가한다.
4. 텍스트 입력부터 측정한다. 같은 장비와 입력으로 `qwen3-4b`, `gemma-4-e4b`, `gemma-4-e2b`를 비교한다.

   ```powershell
   .\.venv\Scripts\python.exe .\scripts\phase0\measure_openai_backend.py --config .\phase0\config.example.json --profile gemma-4-e4b
   ```

5. 영어·일본어·중국어에서 한국어로 옮기는 게임 대사와 소설/논문 문장 각 10개 이상을 준비해 같은 원문·출력 길이·temperature로 비교한다. 의미 보존, 자연스러움, 고유명사, 누락/첨가, 지시문 준수와 TTFT·디코드 tok/s·종단 지연을 함께 기록한다. 품질이 높아도 NPU 적재나 실제 사용 지연이 기준에 못 미치면 기본 모델을 바꾸지 않는다.
6. 텍스트 게이트를 통과한 뒤에만 이미지 입력의 OCR+번역 정확도와 **vision encoder의 NPU 실행**을 별도로 검증한다.

## 공식 자료

- [Google Gemma 4 모델 카드](https://ai.google.dev/gemma/docs/core/model_card_4)
- [OpenVINO 2026.4 릴리스 노트](https://docs.openvino.ai/2026/about-openvino/release-notes-openvino.html)
- [OpenVINO 검증 모델 표](https://docs.openvino.ai/2026/documentation/compatibility-and-support/supported-models.html)
- [OpenVINO GenAI NPU 변환 안내](https://docs.openvino.ai/2026/openvino-workflow-generative/inference-with-genai/inference-with-genai-on-npu.html)
- [OVMS 2026.3/2026.4 릴리스](https://github.com/openvinotoolkit/model_server/releases)
- [OVMS pull 양자화 옵션](https://github.com/openvinotoolkit/model_server/blob/main/docs/parameters.md)
