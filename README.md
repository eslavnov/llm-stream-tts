# Make HAVPE respond quickly on long LLM outputs 

## What is the problem?

Home Assistant Voice Preview Edition (HAVPE) devices are amazing, but they do not handle long TTS responses that well, resulting in them silently failing on long audio responses. 

Let's say you are using a LLM in your pipeline and you try to announce via a TTS system a response to a prompt like "Tell me a story about home assistant". Suppose it would take 30 seconds for the TTS system to generate the audio, because the story is going to be a few minutes long. 

**Here is what would happen now with HAVPE devices:**

*You send a request to the LLM => it takes 2 seconds to generate the output text => the text is sent to the TTS system => you will NOT get any response because there is a **5 second timeout** in HAVPE devices; in the HAVPE logs you will see `“HTTP_CLIENT: Connection timed out before data was ready!”`.*

**Here is how to make it slightly better:**

You could follow [these instructions](https://community.home-assistant.io/t/http-timeout-for-voice-assistant-pe-even-though-the-response-is-recieved/834200/4?u=gyrga) to increase the HAVPE device's timeout. This will ensure you always get a response back, but here is how the flow would look like:

*You send a request to the LLM => it takes 2 seconds to generate the output text => the text is sent to the TTS system => it takes 30 seconds to generate the audio => the audio stream starts **32 seconds** after your request*

It's an improvement, but not ideal. We can do better than this.

## Better than this

This script streams the response of your LLM directly into your TTS engine of choice, allowing it to reply quickly even for long responses (around 3 seconds for Google Cloud Journey models; your milage might vary on the TTS engine/model). So now if you ask your LLM to tell you a long story, you don't have to wait 30 seconds to get a response. The flow would look like:

*You send a request to the LLM => the response is read token by token in real time until we hit an end of a sentence => the sentence is sent to your TTS system => we immediately stream the audio => the audio stream starts **3 seconds** after your request => as more sentences are processed, they are added in real-time to the audio stream*

The provided automation examples allow you to expose this script to your HAVPE devices: when you start a sentence with the words you define, it will switch to this streaming pipeline, which is perfect for stories, audiobooks, summaries, etc.

## Supported LLM/TTS engines

**LLMs:**
1. OpenAI

**TTS engines:**
1. OpenAI
2. Google Cloud
3. ElevenLabs

## Installation

1. Clone the repo.
2. Run the setup script with `./setup.sh` for Unix-based systems (make sure to run `chmod +x setup.sh` first) or `start.bat` for Windows. It will create a virtual environment and install the required dependencies.
3. Edit `configuration.json` and add your OpenAI API key to the `main` section ([get it here](https://platform.openai.com/settings/organization/api-keys)). This is the only required parameter, but there are additional optional settings you can further configure - see [Configuration](https://github.com/eslavnov/llm-stream-tts#configuration).

**(Optional) Run as a UNIX service:**
```
[Unit]
Description=LLM Stream TTS
After=syslog.target network.target

[Service]
User=homeassistant
Group=homeassistant
Type=simple

WorkingDirectory=/home/homeassistant/helper_scripts/llm-stream-tts/
ExecStart=bash ./start.sh
TimeoutStopSec=20
KillMode=process
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
## Configuration
**General settings**

General settings go under `"main"` in the `configuration.json`. All of them need to be provided, but the default config has already all of them prefilled, except for the `"openai_api_key"` - this is the only field in the whole configuration that you need to provide to get started!
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

**TTS settings - OpenAI**

This engine is enabled by default. If you have already provided your OpenAI API key in the `"main"` section, you are good to go!
```
{
    "main": {
      "tts_engine": "openai",
      ...
    },
    "openai": {
      <optional parameters>
    }
}
```
You can pass additional parameters in your `configuration.json`, see `configuration_examples/configuration_openai.json` for all supported options.

**TTS settings - Google Cloud**

1. First you need to obtain a service account file from Google Cloud. Follow [these instructions](https://www.home-assistant.io/integrations/google_cloud/#obtaining-service-account-file), you need only text-to-speech.
2. Change `tts_engine` to `google_cloud` in your `configuration.json`.
3. Add Google Cloud settings to the `configuration.json`. Only the `"credentials_path"` is required, the rest have default values:
```
{
    "main": {
      "tts_engine": "google_cloud",
      ...
    },
    "google_cloud": {
      "credentials_path": "creds.json"
    }
}
```
You can pass additional parameters in your `configuration.json`, see `configuration_examples/configuration_google_cloud.json` for all supported options.

**TTS settings - Elevenlabs**
1. First you need to obtain an API key from ElevenLabs. Get it [here](https://elevenlabs.io/app/settings/api-keys).
2. Change `tts_engine` to `elevenlabs` in your `configuration.json`.
3. Add ElevenLabs settings to the `configuration.json`. Only the `"api_key"` is required, the rest have default values:
```
{
    "main": {
      "tts_engine": "elevenlabs",
      ...
    },
    "elevenlabs": {
      "api_key": "<your-elevenlabs-api-key>"
    }
}
```
You can pass additional parameters in your `configuration.json`, see `configuration_examples/configuration_elevenlabs.json` for all supported options.


## Usage
Run the main script with `./start.sh` for Unix-based systems (make sure to run `chmod +x start.sh` first) or `start.bat` for Windows. It will start a small API server (at http://0.0.0.0:8888 using the default settings) with the following endpoints:

1. `/play` (GET) - Accepts `prompt` as a parameter. This will call your LLM with the supplied prompt and will return a stream to the audio response generated by your TTS engine. Example usage: 
```
curl --location '127.0.0.1:8888/play?prompt=tell+me+a+story+about+home+assistant'
```
2. `/tts` (POST) - Accepts any text and generates TTS audio directly without running the LLM. Good for audiobooks and other cases where you already have some (long) text. Example usage: 
```
curl --location '127.0.0.1:8888/tts' \
--header 'Content-Type: application/json' \
--data '{"text": "I really, REALLY hope it works this time"}'
```

You are almost there - the last step is to create some Home Assistant automations to use this service with HAVPE devices!

## Exposing the script to HAVPE devices
You need to create two Home Assistant automations:
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
4. This script is not aware of your other interactions with your voice assistant(s), what's happening with your Home Assistant instance, your history with this script or anything else. It operates purely based on your `system_prompt` and the request prompt, nothing else.

## Change log

### v0.0.3
**Added**
- `/tts` endpoint to generate audio from the TTS engine based on the provided text (skipping the LLM step).
- Better logs.

### v0.0.2
**Added**
- ElevenLabs TTS engine

### v0.0.1
- Initial commit

