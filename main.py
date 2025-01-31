
import aiofiles
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import json
import httpx
import logging

from elevenlabs import stream
from elevenlabs.client import ElevenLabs
from google.api_core.exceptions import GoogleAPIError
from google.cloud import texttospeech
import openai

config = {}

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:     %(message)s"  # Removes module name
)
logger = logging.getLogger()

app = FastAPI()

def load_config():
    # Add missing keys from defaults to config
    def merge_defaults(config, defaults):
        for key, value in defaults[config["main"]["tts_engine"]].items():
            if key not in config:
                config[config["main"]["tts_engine"]][key] = value
            elif isinstance(value, dict):
                merge_defaults(config[key], value)
  
    def validate_credentials(config):
        try:
          if config["main"]["openai_api_key"]:
            openai.api_key = config["main"]["openai_api_key"]
        except KeyError as e:
          raise Exception("You need to provide an OpenAI API key in your configuration.json") from e
        try:
            if config["main"]["tts_engine"]=="google_cloud" and not config["google_cloud"]["credentials_path"]:
                raise Exception("You need to provide a Google Cloud credentials path in your configuration.json")
        except KeyError as e:
            raise Exception("You need to provide a Google Cloud credentials path in your configuration.json") from e
        try:
            if config["main"]["tts_engine"]=="elevenlabs" and not config["elevenlabs"]["api_key"]:
                raise Exception("You need to provide an ElevenLabs API key in your configuration.json")
        except KeyError as e:
            raise Exception("You need to provide an ElevenLabs API key in your configuration.json") from e
        
    # Load defaults and configuration from JSON file
    with open('defaults.json', 'r') as f:
      defaults = json.load(f)
    with open('configuration.json', 'r') as f:
      config = json.load(f)

    if config["main"]["tts_engine"] not in config:
      config[config["main"]["tts_engine"]] = {}
      
    validate_credentials(config)
    merge_defaults(config, defaults)
    return config
            
def sentence_generator(text):
    """Yields sentences from text as they are detected."""
    sentence = ""
    for char in text:
        sentence += char
        if char in {'.', '!', '?'}:
            yield sentence.strip()
            sentence = ""
    if sentence.strip():
        yield sentence.strip()

async def gpt4_stream(prompt):
    """Streams response from OpenAI's GPT-4."""
    async with httpx.AsyncClient() as client:
        async with client.stream("POST", "https://api.openai.com/v1/chat/completions", 
                                headers={"Authorization": f"Bearer {openai.api_key}", "Content-Type": "application/json"},
                                json={
                                    "model": config["main"]["llm_model"],
                                    "messages": [{"role": "system", "content": config["main"]["llm_system_prompt"]}, {"role": "user", "content": prompt}],
                                    "stream": True
                                }) as response:
            sentence = ""
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    try:
                        data = json.loads(line[5:].strip())
                        if "choices" in data and data["choices"]:
                            new_content = data["choices"][0]["delta"].get("content", "")
                            if new_content:
                                sentence += new_content
                                if sentence.strip().endswith(('.', '!', '?')):
                                    yield sentence.strip()
                                    sentence = ""  # Reset sentence after yielding
                    except json.JSONDecodeError:
                        pass

async def tts_stream_google(sentence, credentials_path, name, language_code, gender):
    """Calls Google Cloud TTS and streams back audio."""
    try:
        client = texttospeech.TextToSpeechClient.from_service_account_json(credentials_path)
        input_text = texttospeech.SynthesisInput(text=sentence)
        voice = texttospeech.VoiceSelectionParams(
            name=name,
            language_code=language_code,
            ssml_gender = texttospeech.SsmlVoiceGender.FEMALE if gender == "FEMALE" else texttospeech.SsmlVoiceGender.MALE
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3
        )
        response = client.synthesize_speech(
            input=input_text, voice=voice, audio_config=audio_config
        )
        yield response.audio_content
    except GoogleAPIError as e:
        logger.error(f"Google Cloud TTS API error: {e}")
        yield b""  # Yield an empty byte string to indicate an error
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        yield b""  # Yield an empty byte string to indicate an error

async def tts_stream_openai(sentence, model, voice):
    """Calls OpenAI TTS, saves audio to a file, and streams it in chunks."""
    try:
        response = openai.audio.speech.create(
            model=model,
            voice=voice,
            input=sentence,
            response_format="mp3"
        )
        # Stream response in chunks
        for audio_chunk in response.iter_bytes(1024):
          yield audio_chunk

    except openai.OpenAIError as e:
        logger.error(f"OpenAI TTS API error: {e}")
        yield b""  # Return an empty byte string on error
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        yield b""  # Return an empty byte string on error
        
async def tts_stream_elevenlabs(sentence, model, voice, api_key):
    """Calls ElevenLabs TTS and streams back audio."""
    try:
        client = ElevenLabs(api_key=api_key)
        response = client.text_to_speech.convert_as_stream(
            text=sentence,
            voice_id=voice,
            model_id=model
        )
        # Stream response in chunks
        for audio_chunk in response:
            yield audio_chunk

    except Exception as e:
        logger.error(f"ElevenLabs TTS API error: {e}")
        yield b""  # Return an empty byte string on error
            
async def tts_stream(sentence,tts_engine):
    """Streams back audio from Google Cloud TTS or OpenAI TTS."""
    if tts_engine== "google_cloud":
        async for audio_chunk in tts_stream_google(sentence, credentials_path=config["google_cloud"]["credentials_path"], name=config["google_cloud"]["name"], language_code=config["google_cloud"]["language_code"], gender=config["google_cloud"]["gender"]):
            yield audio_chunk
    elif tts_engine == "openai":
        async for audio_chunk in tts_stream_openai(sentence, model=config["openai"]["model"], voice=config["openai"]["voice"]):
            yield audio_chunk
    elif tts_engine == "elevenlabs":
        async for audio_chunk in tts_stream_elevenlabs(sentence, model=config["elevenlabs"]["model"], voice=config["elevenlabs"]["voice"], api_key=config["elevenlabs"]["api_key"]):
            yield audio_chunk
            
async def prompt_audio_streamer(prompt, file_path):
    """Runs an LLM prompt, streams the response as TTS audio and saves to a file."""
    collected_text = ""
    async with aiofiles.open(file_path, 'wb') as f:
        async for chunk in gpt4_stream(prompt):
            collected_text += chunk
            for sentence in sentence_generator(collected_text):
                if sentence.strip() !=".":
                  logger.info(f"TTS {config['main']['tts_engine'].upper()}: {sentence}")
                  async for audio_chunk in tts_stream(sentence, config["main"]["tts_engine"]):
                      await f.write(audio_chunk)
                      yield audio_chunk
                  collected_text = ""  # Clear collected_text after processing each sentence
    
async def audio_streamer(text, file_path):
    """Streams the provided text as TTS audio and saves to a file."""
    async with aiofiles.open(file_path, 'wb') as f:
        for sentence in sentence_generator(text):
            if sentence.strip() != ".":
                logger.info(f"TTS {config['main']['tts_engine'].upper()}: {sentence}")
                async for audio_chunk in tts_stream(sentence, config["main"]["tts_engine"]):
                    await f.write(audio_chunk)
                    yield audio_chunk
    
@app.get("/play")
async def play(request: Request):
    """Runs an LLM => TTS pipeline and returns an audio stream."""
    prompt = request.query_params.get('prompt', 'Say that you have recieved no prompt.')
    dummy_file_path = "/dev/null" # Dummy file path to discard audio

    async def dummy_audio_streamer():
        async for chunk in prompt_audio_streamer(prompt, dummy_file_path):
            yield chunk

    return StreamingResponse(dummy_audio_streamer(), media_type="audio/mp3")

@app.post("/tts")
async def tts(request: Request):
    """Processes a long text through TTS and returns an audio stream."""
    dummy_file_path = "/dev/null"  # Dummy file path to discard audio
    
    try:
        data = await request.json()
        text = data.get("text", "")
        if not text:
            raise HTTPException(status_code=400, detail="Text is required")        
        return StreamingResponse(audio_streamer(text, dummy_file_path), media_type="audio/mp3")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

if __name__ == "__main__":
    import uvicorn
    config = load_config()
    uvicorn.run(app, host=config["main"]["host"], port=config["main"]["port"])
