# Quick responses from TTS for long LLM outputs (works with Home Assistant!)

## What does it do?

This script streams the response of your LLM directly into your TTS engine of choice, allowing it to reply quickly (around 3 seconds) even for long responses. So now if you ask your LLM to tell you a long story, you don't have to wait 30 seconds to get a response.

The provided automation examples allow you to integrate this into Home Assistant: when you start a sentence with the words you define, it will switch to this streaming pipeline, which is perfect for stories, audiobooks, summaries, etc.

**Regular behaviour:**
1. Your prompt is sent to the LLM, where it is processed and response text is generated (2 seconds)
2. The resulting text is sent to the TTS, where it is processed fully and the resulting audio is generated (depends on the length of the text, let's say 30 seconds)
3. If you don't hit a timeout, the audio starts playing only after both of the steps are completed.

Time to play: 32 seconds.

**What this script does:**
1. Your prompt is sent to the LLM, and as soon as it starts to generate response, we read it in real time character-by-character to piece together individual sentences.
2. Individual sentences are sent to the TTS system, allowing it to start returning responses in seconds.
3. As LLM spits out more sententes, they are sent to the TTS system to ensure continuous playback of the stream.

Time to play: 3 seconds.

## Supported LLM/TTS engines

**LLMs:**
1. OpenAI

**TTS engines:**
1. OpenAI
2. Google Cloud
3. Elevenlabs

## Installation

1. Clone the repo, navigate to the folder with the code.
2. Run the setup script with `./setup.sh` for Unix-based systems (make sure to run `chmod +x setup.sh` first) or `start.bat` for Windows. It will create a virtual environment and install the required dependencies.
3. Edit `configuration.json` and add your OpenAI API key ([get it here](https://platform.openai.com/settings/organization/api-keys)). This is the only required parameter, but there are additional optional settings you can further configure - see below!

## Configuration

General settings go under `"main"` in the `configuration.json`. All of them need to be provided, but the default config has already all of them prefilled, except for the `"openai_api_key"`.
```
{
  "main":{
    "openai_api_key": <your-openai-api-key>,
    "llm_model": <model-to-use>, # https://platform.openai.com/docs/models
    "llm_system_prompt": <system-prompt>, # System prompt that is applied to all requests
    "tts_engine": <selected-tts-engine>, # Selected TTS engine
    "host": <service-host>, # Host to serve 
    "port": <service-port> # Port to serve
  }
}
```

**To use OpenAI TTS engine:**
This engine is enabled by default. You can pass additional parameters in your `configuration.json`, see `configuration_examples/configuration_openai.json` for all supported options.

**To use Google Cloud TTS engine:**
1. First you need to obtain service account file from Google Cloud. Follow [these instructions](https://www.home-assistant.io/integrations/google_cloud/#obtaining-service-account-file), you need only text-to-speech.
2. Change `tts_engine` to `google_cloud` in your `configuration.json`.
3. Add Google Cloud settings to the `configuration.json`. Only the `"credentials_path"` is required, the rest have default values:
```
"google_cloud": {
    "credentials_path": "creds.json"
}
```
You can pass additional parameters in your `configuration.json`, see `configuration_examples/configuration_google_cloud.json` for all supported options.

**To use Elevenlabs TTS engine:**
1. First you need to obtain an API key from ElevenLabs. Get it [here](https://elevenlabs.io/app/settings/api-keys).
2. Change `tts_engine` to `elevenlabs` in your `configuration.json`.
3. Add ElevenLabs settings to the `configuration.json`. Only the `"api_key"` is required, the rest have default values:
```
"elevenlabs": {
  "api_key": "<your-elevenlabs-api-key>"
}
```
You can pass additional parameters in your `configuration.json`, see `configuration_examples/configuration_elevenlabs.json` for all supported options.


## Usage
Run the main script with `./start.sh` for Unix-based systems (make sure to run `chmod +x start.sh` first) or `start.bat` for Windows. This will create an endpoint at `0.0.0.0:8888/play` that accepts your prompt as a parameter. When you access this endpoint, it will call your LLM model and stream the response directly into a TTS engine of your choice. You can test it by navigating in your browser to `http://<your-host-ip>:<your-port>/play?prompt=Tell+me+a+story+about+home+assistant`. You are almost there, the only thing that is left is to create some home assistant automations to start using this service with Home Assistant!

## Home Assistant integration
You need to create two automations:
1. One to trigger the new pipeline from your voice devices
2. Another one to stop the stream

Here is an example of the first automation:
```
alias: Tell a long story
description: ""
triggers:
  - trigger: conversation
    command:
      - "Tell me a story {details} "
conditions: []
actions:
  - set_conversation_response: OK!
  - action: media_player.play_media
    target:
      device_id: "{{ trigger.device_id }}"
    data_template:
      media_content_type: audio/mp3
      media_content_id: >
        http://<your-host-ip>:<your-port>/play?prompt={{ trigger.sentence |
        regex_replace(find=' ', replace='+', ignorecase=False) }}
mode: single
```
With this automation, any time you start a sentence with "Tell me a story", it will be passed to this script and your voice device will stream the resulting audio.

A second automation is required to stop the stream, because HAVPE does not stop on on "stop" command for me:
```
alias: Stop the story
description: ""
triggers:
  - trigger: conversation
    command:
      - Stop the story
conditions: []
actions:
  - set_conversation_response: OK!
  - action: media_player.media_stop
    metadata: {}
    data: {}
    target:
      device_id: "{{ trigger.device_id }}"
mode: single
```
With this automation, any time you say "stop the story", your voice device will stop playing back the stream.

## Known issues
1. HAVPE does not stop the stream on the stop wakeword, use the automation above as a workaround.
2. The text is sent to TTS systems sentence-by-sentence, so TTS has no awareness of the surrounding context. Usually it's not a problem, but sometimes it might affect the intonations.
3. For now, the logic for splitting the stream into sentences is very rudimental, so sentences like "What a nice day, Mr. Smith!" will be parsed as two sentences: ["What a nice day, Mr.", "Smith!"]. This might result in some weird pauses/strange tempo when this happens.

## Change log

### v0.0.2
**Added**
- Added ElevenLabs TTS engine

### v0.0.1
- Initial commit

