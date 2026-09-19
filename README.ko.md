# Multi-Camera Video Pipeline & AI Editing Suite

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

---

> [!IMPORTANT]
> **Google Antigravity 네이티브 플러그인 및 워크플로 스위트**  
> 본 툴킷은 **Google Antigravity**(**Vertex AI Gemini 3.8 Flash** 기반) 및 전문 NLE 소프트웨어(**DaVinci Resolve**, **Adobe Premiere Pro**, **Final Cut Pro**)를 위한 4단계 멀티카메라 전처리 및 AI 러프컷 스위트입니다.

---

**Multi-Camera Video Pipeline & AI Editing Suite**는 2~6대 카메라 음향 동기화, 방송 표준 라우드니스 정규화, AI 러프컷 타임라인 생성 및 YouTube 자막 교정을 수행합니다.

---

## 설치 및 Google Cloud 설정 (`setup.sh`)

본 프로젝트는 [Agent Plugins 1.0](https://agent-plugins.org/) 표준을 준수하며 **Google Cloud Vertex AI (ADC)** 및 **Cloud Storage (GCS)** 기반으로 동작합니다.

```bash
# 1. 글로벌 Antigravity Plugin으로 클론
git clone https://github.com/sylphlin/multicam-video-preprocessing.git ~/.gemini/config/plugins/multicam-video-preprocessing

# 2. 의존성 설치 및 ADC 인증
brew install ffmpeg
pip install numpy google-genai google-cloud-storage mlx-whisper requests
gcloud auth application-default login

# 3. setup.sh 실행하여 GCS 버킷, 2단계 수명 주기(raw: 2일, 결과물: 15일), IAM 및 .env 설정
chmod +x setup.sh
./setup.sh --project YOUR_GCP_PROJECT_ID
```

---

## 4단계 핵심 워크플로 및 CLI 명령

### Stage 1: 멀티카메라 동기화 및 전처리 (`multicam_pipeline.py`)
1. **MFCC 음향 정렬 및 서브프레임 미세 조정 (`<0.125 ms`)**: 3단계 스캔과 `--strict-sync` 신뢰도 게이트를 지원합니다.
2. **EBU R128 (`-14 LUFS`) 2패스 선형 라우드니스 정규화**: `-14.0 LUFS`로 고정합니다.
3. **프레임 정확도 동기화 마스터 출력 (`CAM*_synced.mp4`)**: 하드웨어 인코딩을 통해 키프레임 오차를 제거합니다.
4. **무분할 전체 그리드 합성 (`multicam_merged_full.mp4`)**: 2~6대 카메라를 단일 캔버스($\le 1920 \times 1080$)로 합성합니다.

```bash
python3 scripts/multicam_pipeline.py \
  --ref CAM1.mp4 --targets CAM2.mp4 CAM3.mp4 \
  --normalize --merge -o output/
```

### Stage 2: Gemini 3.8 Flash Agentic Video 러프컷 (`generate_edl.py`)
**Vertex AI Gemini 3.8 Flash**(`processing="agentic"`)로 `edl_full.csv`를 생성하고 8개 항목의 결정론적 EDL 검증(`6 ERROR + 2 WARN`)을 수행합니다:

```bash
python3 scripts/generate_edl.py -v output/multicam_merged_full.mp4 --strict-edl --lang ko
```

### Stage 3A: FCP7 XML 타임라인 내보내기 (`export_fcp7_xml.py`)
```bash
python3 scripts/export_fcp7_xml.py -e output/edl_full.csv -o output/final_cut_full.xml --fps 29.97 --drop-frame
```

### Stage 3B: 단일 패스 비디오 렌더링 (`edl_to_video.py`)
```bash
python3 scripts/edl_to_video.py --edl output/edl_full.csv -o output/final_cut_full.mp4 --strict-edl
```

### Stage 4: 3단계 골든 자막 파이프라인 (`generate_subtitles.py`)
**Vertex AI 1M 용어집**, **Whisper 단어 타임스탬프**, **Gemini 멀티모달 오디오 교정 + 8차원 스트리밍 품질 감사**를 수행합니다:

```bash
python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4 --language ko
```

---

## Google Drive 연동 및 GCS 2단계 수명 주기 정책

| GCS 경로 접두사 (`matchesPrefix`) | 저장 객체 | 보관 기간 (`age`) | 정리 방식 |
| :--- | :--- | :--- | :--- |
| **`raw/audio_chunks/`** | Stage 4.3 오디오 청크 | **추론 직후 즉시 삭제** | 각 청크 완료 후 Python `finally` 블록에서 즉시 삭제합니다. |
| **`raw/`** | 스테이징된 그리드 비디오 및 오디오 | **2일 (`age: 2`)** | SHA-256 캐시 재사용을 위해 2일간 보관 후 자동 삭제합니다. |
| **`output/`**, **`deliverables/`**, **`multicam_assets/`** | XML/CSV 타임라인, SRT/VTT 자막 및 리포트 | **15일 (`age: 15`)** | 팀 검토를 위해 15일간 보관한 후 자동 삭제합니다. |

---

## 라이선스 (License)

이 프로젝트는 [MIT License](LICENSE)에 따라 라이선스가 부여됩니다.
