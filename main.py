
import aiofiles
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
import json
import logging
from pydantic import BaseModel

from elevenlabs.client import ElevenLabs
from google.api_core.exceptions import GoogleAPIError
from google.cloud import texttospeech
import openai

config = {}
store = {}

preload_event = asyncio.Event()
play_event = asyncio.Event()  

class PreloadRequest(BaseModel):
    messages: str
    tools: str

class MessagesRequest(BaseModel):
    messages: list

class PreloadTextRequest(BaseModel):
    text: str

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(asctime)s: %(message)s",
    datefmt="%H:%M:%S"  # Format for the timestamp
)
logger = logging.getLogger()

app = FastAPI()

def store_get(client_id: str):
  global store
  return store[client_id] if client_id in store else {}

def store_put(client_id: str, data: dict):
  global store
  store[client_id] = data
  return store[client_id]

def config_get():
  global config
  return config

def load_config():
    """Loads config"""
    def merge_defaults(cfg: dict, defaults_cfg: dict):
        """Merges config with the defaults"""
        def merge(cfg, defaults_cfg):
            for key, value in defaults_cfg.items():
                if key not in cfg:
                    cfg[key] = value
                elif isinstance(value, dict):
                    cfg[key] = merge(cfg.get(key, {}), value)
            return cfg

        for key, value in defaults_cfg.items():
            if key in cfg:
                cfg[key] = merge(cfg[key], value)
            else:
                cfg[key] = value

        return cfg
  
    def validate_credentials(cfg:dict):
        try:
          if cfg["main"]["openai_api_key"]:
            openai.api_key = cfg["main"]["openai_api_key"]
        except KeyError as e:
          raise Exception("You need to provide an OpenAI API key in your configuration.json") from e
        try:
            if cfg["main"]["tts_engine"]=="google_cloud" and not cfg["google_cloud"]["credentials_path"]:
                raise Exception("You need to provide a Google Cloud credentials path in your configuration.json")
        except KeyError as e:
            raise Exception("You need to provide a Google Cloud credentials path in your configuration.json") from e
        try:
            if cfg["main"]["tts_engine"]=="elevenlabs" and not cfg["elevenlabs"]["api_key"]:
                raise Exception("You need to provide an ElevenLabs API key in your configuration.json")
        except KeyError as e:
            raise Exception("You need to provide an ElevenLabs API key in your configuration.json") from e
        return cfg
        
    # Load defaults and configuration from JSON file
    with open('defaults.json', 'r') as f:
      defaults = json.load(f)
    with open('configuration.json', 'r') as f:
      config = json.load(f)

    if config["main"]["tts_engine"] not in config:
      config[config["main"]["tts_engine"]] = {}

    return merge_defaults(validate_credentials(config), defaults)
            
def sentence_generator(text: str):
    """Yields sentences from text as they are detected."""
    sentence = ""
    for char in text:
        sentence += char
        if char in {'.', '!', '?'}:
            yield sentence.strip()
            sentence = ""
    if sentence.strip():
        yield sentence.strip()

async def llm_stream(cfg: str, prompt: str, llm_config: dict, client_id: str):
    print("!!!!!", client_id)
    """Streams responses from OpenAI's GPT-4, handles tool calls, and re-calls the API if needed."""
    messages = None
    client_store = store_get(client_id)
    if "messages" in client_store:
      messages = client_store["messages"]
      
    if messages is None:
        # Check if messages were provided in the llm config
        messages = (
            json.loads(llm_config["messages"])
            if llm_config and "messages" in llm_config
            else [
                {"role": "system", "content": cfg["main"]["llm_system_prompt"]}, {"role": "user", "content": prompt},
            ]
        )
        client_store["messages"] = messages
        store_put(client_id, client_store)

    client = openai.OpenAI(api_key=cfg["main"]["openai_api_key"])

    while True:
        tool_calls = {}  # Dictionary to store tool calls by index
        sentence = ""
        full_response = ""
        client_store = store_get(client_id)
        messages = client_store["messages"]
        logger.info("CALLING LLM") #, json.dumps(messages[-2:]))
        
        # fail safe in case we did not get tool_call response and trying to issue a new command
        if "tool_calls" in messages[-2] and messages[-1]['role']=='user':
          logger.info("FAILSAFE TRIGGERED, IT'S OK")
          client_store["messages"].pop(-2)
          store_put(client_id, client_store)
  
        try:
            completion = client.chat.completions.create(
                model=cfg["main"]["llm_model"],
                messages=messages,
                tools=json.loads(llm_config["tools"]) if llm_config and "tools" in llm_config else None,
                stream=True,
            )
        except openai.OpenAIError as e:
            logger.error(f"OpenAI API error: {e}")
            return
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return

        # --- STREAM THE RESPONSE ---
        for chunk in completion:
            if chunk.choices:
                delta = chunk.choices[0].delta
                # print(delta)

                # Handle streaming text
                new_content = delta.content
                if new_content:
                    # print("Getting LLM response...")
                    logger.info("Getting LLM response...")
                    sentence += new_content
                    full_response += new_content
                    # Yield entire sentences based on punctuation
                    if sentence.strip().endswith((".", "!", "?")):
                        yield sentence.strip()
                        sentence = ""  # Reset sentence buffer

                # Handle tool calls
                if delta.tool_calls:
                    for tool_call in delta.tool_calls:
                        index = tool_call.index
                        if index not in tool_calls:
                            tool_calls[index] = {
                                "id": tool_call.id,
                                "name": tool_call.function.name,
                                "arguments": "",
                            }
                        tool_calls[index]["arguments"] += tool_call.function.arguments

        # If there was trailing text without a final punctuation, yield it
        if sentence.strip():
            yield sentence.strip()

        # Add the full response as a single message (if it has any content)
        if full_response.strip():
            messages.append({"role": "assistant", "content": full_response.strip()})
            client_store = store_get(client_id)
            client_store["messages"] = messages
            store_put(client_id, client_store)

        # If there are tool calls, add them to messages
        if tool_calls:
            for tcall in tool_calls.values():
                try:
                    # Transform to your desired structure
                    tcall["function"] = {
                        "name": tcall["name"],
                        "arguments": tcall["arguments"],
                    }
                    tcall["type"] = "function"
                    del tcall["arguments"]
                    del tcall["name"]
                except json.JSONDecodeError:
                    print(
                        f"Error parsing JSON for tool (index={tcall}): {tcall['arguments']}"
                    )

            final_tool_calls = list(tool_calls.values())
            messages.append({"role": "assistant", "tool_calls": final_tool_calls})
            client_store = store_get(client_id)
            client_store["messages"] = messages
            client_store["tool_commands"] = final_tool_calls
            store_put(client_id, client_store)
            
            # We've updated messages and want the external system to react
            preload_event.set()
            
            # Wait for the play_event to be set
            logger.info("GOT TOOLS IN THE RESPONSE, RUNNING A PROMPT TO GENERATE RESPONSE")
            await play_event.wait()
            play_event.clear()  # Clear the event for future use

            # At this point, 'messages' may have been modified externally,
            # so loop back and call the API again with updated 'messages'.
            # We'll continue in the `while True` loop.
        else:
            # No tool calls -> we can stop here
            break
          
async def tts_stream_google(sentence: str, credentials_path: str, name: str, language_code: str, gender: str):
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

async def tts_stream_openai(sentence: str, model: str, voice: str):
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
        
async def tts_stream_elevenlabs(sentence: str, model: str, voice: str, api_key: str):
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
            
async def tts_stream(sentence: str, cfg: dict):
    """
    Simple wrapper to route to whichever TTS engine is in config.
    Here we only show google_cloud for brevity.
    """
    if cfg["main"]["tts_engine"] == "google_cloud":
        async for audio_chunk in tts_stream_google(sentence, credentials_path=cfg["google_cloud"]["credentials_path"], name=cfg["google_cloud"]["name"], language_code=cfg["google_cloud"]["language_code"], gender=cfg["google_cloud"]["gender"]):
            yield audio_chunk
    elif cfg["main"]["tts_engine"]  == "openai":
        async for audio_chunk in tts_stream_openai(sentence, model=cfg["openai"]["model"], voice=cfg["openai"]["voice"]):
            yield audio_chunk
    elif cfg["main"]["tts_engine"]  == "elevenlabs":
        async for audio_chunk in tts_stream_elevenlabs(sentence, model=cfg["elevenlabs"]["model"], voice=cfg["elevenlabs"]["voice"], api_key=cfg["elevenlabs"]["api_key"]):
            yield audio_chunk
    else:
        print("FICK")
        yield b""
 
async def prompt_audio_streamer(prompt: str, cfg: dict, llm_config: dict, client_id: str, file_path: str = "/dev/null"):
    """Runs an LLM prompt, streams the response as TTS audio and saves to a file."""
    collected_text = ""
    async with aiofiles.open(file_path, 'wb') as f:
        async for chunk in llm_stream(cfg, prompt, llm_config, client_id):
            collected_text += chunk
            for sentence in sentence_generator(collected_text):
                if sentence.strip() !=".":
                  logger.info(f"TTS {config['main']['tts_engine'].upper()}: {sentence}")
                  async for audio_chunk in tts_stream(sentence, cfg):
                      await f.write(audio_chunk)
                      yield audio_chunk
                  collected_text = ""  # Clear collected_text after processing each sentence
    
async def audio_streamer(text: str, cfg: dict, file_path: str = "/dev/null"):
    """
    Takes the user text, splits into sentences, calls TTS for each one,
    and yields the raw MP3 data in chunks. Also saves to 'file_path' (if desired).
    """
    async with aiofiles.open(file_path, 'wb') as f:
        for sentence in sentence_generator(text):
            if sentence.strip():
                logger.info(f"TTS {cfg['main']['tts_engine'].upper()} => {sentence}")
                async for audio_chunk in tts_stream(sentence, cfg):
                    await f.write(audio_chunk)
                    yield audio_chunk
 
async def create_persistent_flac_encoder():
    """
    Creates a persistent ffmpeg subprocess that reads MP3 from stdin, outputs FLAC on stdout.
    """
    process = await asyncio.create_subprocess_exec(
        'ffmpeg',
        '-hide_banner',
        '-loglevel', 'error',
        '-i', 'pipe:0',         # input is MP3 from stdin
        '-ar', '24000',         # sample rate
        '-ac', '1',             # mono
        # Force 16 bits-per-sample:
        '-sample_fmt', 's16',
        '-f', 'flac',           # output format
        'pipe:1',               # send FLAC to stdout
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    return process

# async def stream_flac_from_mp3_sentences(prompt: str, cfg):
#     """
#     - Launch ffmpeg (MP3 -> FLAC).
#     - Feed each sentence's MP3 data from TTS -> ffmpeg stdin.
#     - Stream ffmpeg's FLAC output to the caller.
#     """
#     encoder = await create_persistent_flac_encoder()

#     async def feed_encoder():
#         async for mp3_data in audio_streamer(prompt, cfg):
#             encoder.stdin.write(mp3_data)
#             await encoder.stdin.drain()
#         encoder.stdin.close()

#     feed_task = asyncio.create_task(feed_encoder())

#     try:
#         while True:
#             flac_chunk = await encoder.stdout.read(4096)
#             if not flac_chunk:
#                 break
#             yield flac_chunk
#     finally:
#         await feed_task
#         await encoder.wait()

async def stream_flac_from_mp3_sentences_with_prompt(prompt: str, cfg: dict, client_id: str, llm_config=False):
    """
    - Launch ffmpeg (MP3 -> FLAC).
    - Feed each sentence's MP3 data from TTS -> ffmpeg stdin.
    - Stream ffmpeg's FLAC output to the caller.
    """
    encoder = await create_persistent_flac_encoder()

    async def feed_encoder():
        async for mp3_data in prompt_audio_streamer(prompt, cfg, llm_config, client_id):
            encoder.stdin.write(mp3_data)
            await encoder.stdin.drain()
        encoder.stdin.close()

    feed_task = asyncio.create_task(feed_encoder())

    try:
        while True:
            flac_chunk = await encoder.stdout.read(4096)
            if not flac_chunk:
                break
            yield flac_chunk
    finally:
        await feed_task
        await encoder.wait()

@app.post("/preload-text/{client_id}")
async def preload_text(client_id: str, request_data: PreloadTextRequest):
    """
    Accepts JSON {"text": "..."} and stores it globally so that
    /tts_say can use this text.
    """
    client_store = store_get(client_id)
    client_store["preloaded_text"]= request_data.text
    store_put(client_id, client_store)
    logger.info(f"NEW PRELOADED TEXT: {client_store['preloaded_text']}")
    response_data = {
        "status": "ok",
        "msg": "Text preloaded successfully.",
    }
    return JSONResponse(content=response_data)
  
@app.post("/preload/{client_id}")
async def preload_llm_config(client_id: str, request_data: PreloadRequest):
    """
    Accepts JSON {"messages": "...", "tools": "..."} and stores it globally so that
    /play/<filename>.flac can use this text instead of ?prompt=.
    """
    messages =  json.loads(request_data.messages)
    logger.info(f"NEW MESSAGE: {messages[-1]['content']}")

    client_store = store_get(client_id)
    client_store["messages"] = messages
    client_store["preloaded_llm_config"] = {"messages": request_data.messages, "tools": request_data.tools }
    store_put(client_id, client_store)

    # Now we have our llm config with tools and messages ready. 
    # We wait for the /play endpoint to trigger LLM pipeline with this config.
    # It will call preload_event.set() when it has the full response.
    await preload_event.wait()  # Wait for the event to be set
    preload_event.clear()  # Clear the event for future use
    
    # Now that we have the full response, we return the updated messages history and the tool_calls to Home Assistant.
    # This will allow it to run the tools and append the results to the messages history.
    # Updated messages will come via the /write_history endpoint.
    client_store = store_get(client_id)
    tools_request = client_store["tool_commands"]
    client_store["tool_commands"] = None
    store_put(client_id, client_store)
    response_data = {
        "status": "ok",
        "msg": "Text preloaded successfully.",
        "tool_calls": tools_request,
        "messages": messages
    }
    return JSONResponse(content=response_data)

@app.get("/tts_say/{client_id}")
async def tts(request: Request):
    """Processes a long text through TTS and returns an audio stream."""
    config = config(get)
    dummy_file_path = "/dev/null"  # Dummy file path to discard audio
    client_store = store_get(client_id)
    preloaded_text = client_store["preloaded_text"] if "preloaded_text" in client_store else None
    try:
        if not preloaded_text:
            raise HTTPException(status_code=400, detail="Text is required")        
        return StreamingResponse(audio_streamer(preloaded_text, config, dummy_file_path), media_type="audio/mp3")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
      
@app.get("/play/{client_id}.flac")
async def play_flac(client_id: str, request: Request):
    """
    Endpoint for streaming the TTS audio in FLAC format.
    We use ?prompt= from the query string.
    Otherwise if llm config (tools + messages) was preloaded via /preload, we use that.
    """
    config = config_get()
    # if not config:
    #     config = load_config()
    # Use prompt query param, otherwise use provided llm config
    client_store = store_get(client_id)
    preloaded_llm_config = client_store["preloaded_llm_config"] if "preloaded_llm_config" in client_store else None
    prompt = request.query_params.get("prompt", None)
    if not preloaded_llm_config and not prompt:
        prompt ="Say you have received no prompt."
    llm_config =None if prompt else preloaded_llm_config
    
    flac_stream = stream_flac_from_mp3_sentences_with_prompt(prompt, config, client_id, llm_config)
    
    return StreamingResponse(
        flac_stream,
        media_type="audio/flac",
        headers={"Content-Disposition": f'inline; filename="{client_id}.flac"'}     # Content-Disposition so the browser sees it as a .flac file
    )
  
@app.get("/history/{client_id}")
async def get_history(client_id: str):
    """
    Returns the history of messages as a JSON response.
    """
    client_store = store_get(client_id)
    return JSONResponse(content={"messages": client_store["messages"]})

@app.post("/write_history/{client_id}")
async def write_history(client_id: str, request_data: MessagesRequest):
    """
    Writes the messages history.
    """
    client_store = store_get(client_id)
    messages = request_data.messages
    client_store["messages"] = messages
    store_put(client_id, client_store)
    play_event.set()
    response_data = {"status": "ok", "msg": "Messages history updated."}
    return JSONResponse(content=response_data)
  
if __name__ == "__main__":
    import uvicorn
    config = load_config()
    print(config)
    uvicorn.run(app, host=config["main"]["host"], port=config["main"]["port"])
