```request
curl -X POST "http://api.net/whisper-large-v3/v1/audio/transcriptions" \ 
-H "x-dep-ticket: credential:" \
-H "user-id: AD_ID" \
-H "content_type: application/json" \
-F "file=@audio.mp3" \
-F "model=openai/whisper-large-v3" \
-F "language=en" \
-F "timestamp_granularities=word" \
-F "response_format=diarized_json" 
```
```response
{  
  "text": "전체 텍스트 내용 (Full transcript)",  
  "task": "작업 유형 (예: 'transcribe')",  
  "language": "언어 코드 (예: 'en')",  
  "duration": 전체 재생 시간 (초 단위),  
  "segments": [  
    {  
      "id": 세그먼트 고유 ID,  
      "start": 시작 시간 (초 단위),  
      "end": 종료 시간 (초 단위),  
      "text": "세그먼트별 텍스트",  
      "words": [  
        {  
          "word": "단어",  
          "start": 단어 시작 시간,  
          "end": 단어 종료 시간  
        }  
      ]  
    }  
  ],  
  "words": null,  
  "speakers": [  
    {  
      "speaker": "화자 식별자 (예: 'SPEAKER_00')",  
      "start": 발화 시작 시간,  
      "end": 발화 종료 시간,  
      "text": "화자 텍스트"  
    }  
  ]  
}  
```
위 형식으로 동작하는 whisper-large-v3 모델을 사용하여 영상을 업로드하면 자막이 입혀진 영상을 자동으로 출력해주는 웹서비스를 만들고 싶어 아래 내용 준수해서 만들어줘

1. fastapi 기반
2. 다량의 요청 시 queue관리
3. 업로드 된 영상 별 task 관리 및 삭제 기능
4. ffmpeg 기반 영상에서 음성 추출
5. 사용자 친화 UI 구성
6. 웹상에서 자막이 올라간 영상을 바로 보고 쉽게 편집 후 반영
