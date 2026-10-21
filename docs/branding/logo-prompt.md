# JetAI 로고 프롬프트

이미지 생성 AI(예: ChatGPT 이미지, Midjourney, Ideogram, Flux)에 그대로 붙여 넣으세요. 이미지 생성은 이 저장소 밖에서 하며, 결과 파일은 직접 저장합니다.

## 한 줄 설명
JetAI는 무료·로컬 AI 모델을 한곳에 연결해 개발 토큰을 아끼는 데스크톱 AI 채팅·작업 도구입니다. 키워드: 빠름(jet), 절약, 연결, 깔끔함, 믿음직함.

## 메인 프롬프트 (영문, 심볼 로고)
```
Minimal modern app icon logo for "JetAI", a fast and efficient AI assistant app.
A simple geometric symbol that combines a stylized paper-jet / swept wing shape with a small spark or node,
suggesting speed and intelligence. Flat vector style, bold clean shapes, rounded geometry,
two-colour palette: teal-green (#19c3a4) on deep charcoal (#212121), with a white version variant.
Centered on a plain background, generous padding, no gradients, no 3D, no photo, no mockup,
no text inside the symbol, crisp edges, scalable down to 16px, app icon in a rounded square.
```

## 워드마크 프롬프트 (글자 포함)
```
Logo wordmark "JetAI" in a clean geometric sans-serif typeface, medium-bold weight,
"Jet" in white and "AI" in teal-green (#19c3a4), tight letter spacing,
a small swept-wing accent integrated into the "J". Flat vector, solid dark background (#212121),
horizontal layout, no effects, no shadows, high contrast, sharp.
```

## 변형 요청 (마음에 드는 결과가 나온 뒤)
- "같은 심볼을 흰 배경·검은 배경 두 버전으로, 단색(검정) 버전도 만들어 줘"
- "16x16, 32x32에서도 알아볼 수 있게 더 단순하게"
- "윈도우 바탕화면 아이콘용으로 둥근 사각형 안에 꽉 차게"

## 피할 것 (negative prompt)
```
photo, realistic, 3D render, gradient mesh, glossy, drop shadow, busy details, thin lines,
many colours, cartoon mascot, robot head clichés, brain clichés, circuit board patterns,
text artefacts, watermark, any trademark or existing brand logo (OpenAI, Anthropic, Google, Nvidia)
```

## 참고
- 이름의 "AI"가 들어간 흔한 로고(뇌, 로봇, 회로)는 피해서 구별되게 합니다.
- 기존 서비스 로고와 닮지 않았는지 확인한 뒤 사용하세요.
- 결과 파일을 정하시면 앱 아이콘(`favicon`)과 바로가기 아이콘에 적용하는 작업은 제가 하겠습니다(`.ico`·`.svg`로 변환 필요).

## 확정된 로고 (2026-10-21)
이미지 생성 AI로 만든 시안(둥근 사각형 안의 종이비행기+별)을 바탕으로 **SVG로 다시 그려** 앱에 적용했습니다. 래스터 이미지는 라이트/다크 테마에 맞게 바꿀 수 없고 크기에 약해서입니다.
- `workshop/static/jetai-icon.svg`: 어두운 둥근 사각형 + 청록 심볼. 앱 아이콘·파비콘용(어느 배경에서나 같은 모양).
- `workshop/static/jetai-mark.svg`: 심볼만(청록 `#19c3a4`, 배경 투명).
- `workshop/static/jetai.ico`: 16~256px 7개 크기, 바탕화면 바로가기와 `/favicon.ico`용.
- 앱 사이드바의 심볼은 인라인 SVG라서 현재 테마의 강조색(라이트 `#0f9d85`, 다크 `#19c3a4`)을 따릅니다.
- 시안과 같은 형태를 눈으로 맞춘 근사이므로, 더 정확한 곡선이 필요하면 원본 이미지를 벡터 편집기에서 트레이스해 교체하세요.
