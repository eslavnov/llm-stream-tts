import asyncio
from typing import Callable

async def create_persistent_flac_encoder():
    """
    Creates a persistent ffmpeg subprocess that reads MP3 from stdin, outputs FLAC on stdout.
    This is a format that HAVPE can natively paly.
    """
    process = await asyncio.create_subprocess_exec(
        'ffmpeg',
        '-hide_banner',
        '-loglevel', 'error',
        '-i', 'pipe:0',         # input is MP3 from stdin
        '-ar', '24000',         # sample rate
        '-ac', '2',             # stereo
        '-sample_fmt', 's16',  # force 16 bits-per-sample:
        '-f', 'flac',           # output format
        'pipe:1',               # send FLAC to stdout
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    return process

async def feed_encoder(encoder: asyncio.subprocess.Process, audio_source_function: Callable, cfg: dict, client_id: str, prompt =None, llm_config=None):
    """
    Feeds audio data from the provided audio source function to the encoder's stdin.
    This function streams audio data generated by the prompt_audio_streamer
    and writes it to the stdin of the encoder process. It ensures that the
    encoder receives the audio data in chunks and processes it accordingly.
    """
    
    async for audio_data in audio_source_function(cfg, client_id, prompt, llm_config):
        encoder.stdin.write(audio_data)
        await encoder.stdin.drain()
        
    encoder.stdin.close()

async def stream_flac_from_audio_source(audio_source_function: Callable, cfg: dict, client_id: str,  prompt=None, llm_config=None):
    """
    - Calls a function that generates mp3 stream
    - Launches ffmpeg (Audio -> FLAC).
    - Feeds each sentence's audio data from TTS -> ffmpeg stdin.
    - Streams ffmpeg's FLAC output to the caller.
    """
    encoder = await create_persistent_flac_encoder()
    
    feed_task = asyncio.create_task(feed_encoder(encoder, audio_source_function, cfg, client_id, prompt, llm_config))

    try:
      while True:
          flac_chunk = await encoder.stdout.read(4096)
          if not flac_chunk:
              break
          yield flac_chunk
    finally:
        await feed_task
        await encoder.wait()

        # Capture and log ffmpeg stderr output
        stderr_output = await encoder.stderr.read()
