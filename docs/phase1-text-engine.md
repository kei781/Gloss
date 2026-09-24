# Phase 1 Text Engine

Phase 1은 OCR 없이 접근 가능한 텍스트를 추출하고, Phase 0에서 검증한 OpenAI 호환 NPU 백엔드(Intel NPU: OVMS/OpenVINO, device=NPU)로 번역한다. CLI 출력과 PyQt6 데스크톱 리더가 같은 추출·번역 엔진과 메트릭 JSONL을 사용한다.

## 실행 전 준비

NPU 백엔드 서버(OVMS, device=NPU)를 실행한다. 검증된 텍스트 프로파일은 `qwen3-4b`다. 로컬 환경에 INT4 OpenVINO IR 모델을 준비한 뒤 OVMS를 실행한다.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\phase0\run_model_profile.ps1 `
  -Profile qwen3-4b `
  -Action serve
```

다른 터미널에서 Text 엔진을 실행한다. 개발 체크는 `PYTHONPATH=src`로 할 수 있고,
패키지 설치 후에는 `gloss-text` 콘솔 명령도 사용할 수 있다.

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m gloss.text.cli `
  --profile qwen3-4b `
  --text "The moonlight fell softly over the old town." `
  --output .\runs\phase1\sample.md
Remove-Item Env:\PYTHONPATH
```

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
gloss-text --profile qwen3-4b --text "Hello."
```

## 입력 모드

GUI 리더에서는 왼쪽에 텍스트를 붙여 넣거나 URL/PDF/HTML 파일을 불러오고, 오른쪽에서 번역문을 읽거나 저장할 수 있다. URL의 JavaScript 렌더링과 추가 페이지 수를 선택할 수 있다. `.[gui]` 설치 후 실행한다.

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[gui]'
.\.venv\Scripts\gloss-reader.exe --profile qwen3-4b
.\.venv\Scripts\gloss-reader.exe --profile qwen3-4b --url https://example.com/story
```

PDF 파일은 `pypdf`로 텍스트를 페이지별 추출한다. 텍스트가 없는 스캔 페이지는 `pypdfium2`로 렌더링한 뒤 Windows OCR을 사용한다. `.[pdf]` extra를 설치하고, 필요하면 `--pdf-ocr-language`로 언어를 지정한다. 스캔 페이지의 OCR은 CPU 보조 작업이다.

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[pdf]'
.\.venv\Scripts\gloss-text.exe --profile qwen3-4b --file .\paper.pdf --pdf-ocr-language en-US
```

직접 텍스트:

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m gloss.text.cli --profile qwen3-4b --text "Hello."
Remove-Item Env:\PYTHONPATH
```

텍스트/HTML 파일:
파일 입력은 UTF-8, UTF-8 BOM, CP949, EUC-KR 순서로 읽는다.

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m gloss.text.cli --profile qwen3-4b --file .\samples\chapter.html
Remove-Item Env:\PYTHONPATH
```

URL:

다음 회차 링크를 따라 최대 2페이지를 더 읽으려면 `--next-pages 2`를 붙인다. `rel=next` 또는 "Next/다음/次へ" 링크를 같은 호스트에서만 따라가며, 링크가 없거나 순환하면 멈춘다.

본문이 JavaScript로 만들어지는 사이트는 `--render-js`를 쓴다. `.[web]` extra와 Chromium을 설치해야 하며, 기본적으로 DOMContentLoaded 이후 800ms 기다린다(`--js-wait-ms`로 변경 가능).

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[web]'
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\gloss-text.exe --profile qwen3-4b --url https://example.com/story --render-js --next-pages 2
```

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m gloss.text.cli --profile qwen3-4b --url https://example.com/story
Remove-Item Env:\PYTHONPATH
```

Windows/x64 Python 환경에서 사설 인증서 체인 때문에 HTTPS URL fetch가 실패하면
신뢰할 수 있는 테스트 URL에 한해 인증서 검증을 생략할 수 있다.

```powershell
.\.venv\Scripts\gloss-text.exe `
  --profile qwen3-4b `
  --url "https://ncode.syosetu.com/n1976ey/" `
  --url-insecure-skip-verify `
  --output .\runs\phase1\url-live.md
```

조직/프록시의 CA bundle을 알고 있다면 검증 생략 대신 `--url-ca-bundle`을 사용한다.

```powershell
.\.venv\Scripts\gloss-text.exe `
  --profile qwen3-4b `
  --url "https://example.com/story" `
  --url-ca-bundle .\certs\corp-root.pem
```

## Dry Run

백엔드 없이 추출/분할/리더 출력만 확인한다.
실제 서버 smoke가 오래 걸리거나 멈춘 것처럼 보일 때는 먼저 이 경로로 Text 엔진 자체와 입력 추출을 분리 확인한다.

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m gloss.text.cli `
  --dry-run `
  --text "This is a dry-run text block." `
  --show-source
Remove-Item Env:\PYTHONPATH
```

## 샘플 입력

실기 확인용 샘플은 `samples/phase1`에 둔다.

```powershell
.\.venv\Scripts\gloss-text.exe `
  --profile qwen3-4b `
  --file .\samples\phase1\long-text.txt `
  --output .\runs\phase1\long-text-live.md
```

`cp949-korean.txt`와 `euc-kr-korean.txt`는 파일 인코딩 확인용이고,
`sample-page.html`은 HTML 본문 추출 확인용이다. `invalid-encoding.txt`는
스택트레이스 없이 깔끔한 에러가 나는지 확인할 때만 사용한다.

## 메트릭

기본 메트릭 경로는 `runs/phase1/text-metrics.jsonl`이다. 각 번역 block마다 다음 값을 기록한다.

- source kind/source/title
- model profile/model/backend base URL
- source/translated char count
- elapsed, TTFT, decode window
- completion/prompt tokens
- token count source
- decode tok/s, end-to-end tok/s
- finish reason/truncated flag
- usage raw payload

Phase 1의 번역 토큰 기본값은 Phase 0 벤치마크용 `measurement.max_tokens`와
분리되어 있으며 기본 1024 tokens/block이다. 필요하면 `--max-tokens`,
`GLOSS_PHASE1_MAX_TOKENS`, 또는 config의 `phase1.text.max_tokens`로 조정한다.
원문은 Intel NPU의 기본 프롬프트 길이(1024토큰)를 고려해 블록당 추정 480토큰 이하로 나눈다. CJK 문자를 더 보수적으로 계산하며, `--max-estimated-tokens-per-block`으로 조정할 수 있다. 실제 토크나이저 값은 OVMS 응답의 `prompt_tokens` 메트릭으로 확인한다.
백엔드가 `finish_reason=length`를 반환하면 해당 block은 `truncated=true`로 기록되고
stderr 경고 로그도 남는다.

## 현재 한계

- URL 본문 추출은 표준 라이브러리 기반 휴리스틱이다. JS 페이지는 선택적 Playwright 렌더링을 쓸 수 있지만, 복잡한 사이트의 본문 선택 품질은 별도 확인이 필요하다.
- PDF 텍스트와 스캔 페이지 OCR 경로가 제공된다. 표/다단 편집 등 레이아웃 보존은 보장하지 않는다.
- `qwen3-4b`는 Intel X7 358H 실기에서 NPU 적재와 37.23 tok/s가 검증됐다. 새 환경에서는 IR 모델 설치와 NPU 직접 증거를 다시 확인한다.
