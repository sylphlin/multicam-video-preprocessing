# 멀티카메라 영상 지능형 전처리 및 AI 편집 스위트 (Antigravity Native)

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

---

> [!IMPORTANT]
> **🚀 Google Antigravity 네이티브 스킬 & 워크플로우**  
> 본 툴킷은 **Google Antigravity Agent 프레임워크(Gemini 3.7 Flash 1M 멀티모달 컨텍스트)** 및 전문 NLE 편집 소프트웨어(DaVinci Resolve, Adobe Premiere Pro, Final Cut Pro)를 위해 설계된 멀티카메라(2~6대) 지능형 전처리 파이프라인입니다.

---

## 📦 Antigravity 설치 및 구조

```bash
git clone https://github.com/sylphlin/multicam-video-preprocessing.git ~/.gemini/config/skills/multicam-video-preprocessing
```

### 📁 디렉토리 구조
```text
multicam-video-preprocessing/
├── GEMINI.md                          # Antigravity 루트 워크스페이스 상주 규칙
├── .agent/
│   ├── rules/
│   │   └── multicam_rules.md          # 상주 정책 및 제약 조건
│   └── workflows/
│       └── multicam_workflow.md       # 공식 4단계 실행 워크플로우 (Stage-Gated Runbook)
├── skills/
│   └── multicam-video-preprocessing/
│       └── SKILL.md                   # Antigravity 스킬 기능 정의서
├── assets/                            # 프롬프트 템플릿 자산
├── scripts/                           # 핵심 실행 도구 라이브러리
│   ├── multicam_pipeline.py           # 1단계: 멀티캠 동기화, 음량 표준화, 챕터 분할, 그리드 합성
│   ├── generate_edl.py                # 2단계: Gemini 3.7 Flash 멀티모달 가편집 결정
│   ├── export_fcp7_xml.py             # 3A단계: FCP7 XML 타임라인 내보내기 (추천)
│   ├── edl_to_video.py                # 3B단계: 하드웨어 가속 비디오 렌더링
│   ├── concat_videos.py               # 3B단계: 전체 챕터 무손실 병합
│   ├── generate_subtitles.py          # 4단계: YouTube 자막 생성 (Whisper + Gemini)
│   └── modules/                       # 음향 및 영상 핵심 알고리즘 모듈
└── README.ko.md
```

---

## 🚀 빠른 시작 가이드

### 플랜 A: 전문 NLE 타임라인 워크플로우 (XML 내보내기 ⭐ 추천)

```bash
# 1. 멀티카메라 전처리 (동기화 + EBU R128 음량 표준화 + 30-40분 챕터 분할 + 마스터 출력 + 그리드 합성)
python3 scripts/multicam_pipeline.py \
  --ref CAM1.mp4 --targets CAM2.mp4 \
  --auto-split --split-min-dur 30 --split-max-dur 40 \
  --normalize --merge \
  -o ./output/

# 2. Gemini 3.7 Flash AI 멀티모달 가편집 결정 (각 Part마다 실행)
python3 scripts/generate_edl.py -v ./output/multicam_merged_part1.mp4
python3 scripts/generate_edl.py -v ./output/multicam_merged_part2.mp4

# 3A. FCP7 XML 타임라인 내보내기
python3 scripts/export_fcp7_xml.py -d ./output/ -o ./output/final_cut_full.xml
```

#### 🎬 DaVinci Resolve 타임라인 가져오기:
1. DaVinci Resolve를 열고 새 프로젝트를 생성합니다.
2. `./output/CAM1_synced.mp4` 및 `./output/CAM2_synced.mp4`를 **미디어 풀(Media Pool)** 로 드래그합니다.
3. **파일 $\\rightarrow$ 가져오기 $\\rightarrow$ 타임라인...** 을 클릭하고 `final_cut_full.xml`을 선택합니다.
4. 모든 컷 편집점, 메인 오디오 트랙 및 컬러 마커가 즉시 로드됩니다!
