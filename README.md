# ClipForge

A local-first short-form video pipeline. It does two jobs, and both of them run on your own
machine for nothing.

**Clipping.** Point it at video you own or are licensed to use. It transcribes, finds the
strongest self-contained moments, cuts them vertical with burned-in captions, checks the
result, and queues them to post.

**Your Twin.** Type what a video should be about. It writes a spoken script, narrates it in a
clone of your voice, puts it over your own footage or a screen recording, times the captions
to the audio, and produces a finished 1080x1920 video. Measured end to end on a laptop RTX
5070 Ti: 73 seconds.

There is no per-video cost and no account to sign up for. The whole thing works offline.

## How it fits together

```
long video ──> transcribe ──> score moments ──> vertical cut + captions ──┐
                                                                          ├──> queue ──> post
brief ──> script ──> cloned voice ──> footage ──> captions ──> render ────┘
```

Both paths end in the same posting queue, so the platforms' rate limits count them together.

## What does the work

| Job | What runs it | Cost |
|---|---|---|
| Script writing | a local model over Ollama | free |
| Voice cloning | [Chatterbox](https://github.com/resemble-ai/chatterbox), MIT weights | free |
| Transcription and caption timing | faster-whisper | free |
| Lip-sync (optional) | [LatentSync](https://github.com/bytedance/LatentSync) | free |
| Screen recording | ffmpeg, encoded on the GPU | free |
| Editing and render | ffmpeg | free |

ElevenLabs is wired in as an alternative voice provider. It is never selected automatically,
even when an API key is present, because switching to a metered service without being asked
is a bill nobody agreed to.

## The provider contract

Nothing above `twin/contract.py` knows that any particular vendor exists. Voice and face
providers declare what they cost, where they run, and what they are missing when they cannot
run. Choosing a different one is a row in a config file.

```python
class VoiceProvider(Provider):
    def clone(self, name: str, reference_audio: str) -> str: ...
    def speak(self, handle: str, text: str, out_path: str) -> str: ...

class FaceProvider(Provider):
    def render(self, plate: str, audio: str, out_path: str) -> str: ...
```

A provider that reports itself unavailable must say what is missing. `Availability` raises if
you try to construct one that claims otherwise, because a component that can fail silently is
the failure this codebase keeps finding.

## Design notes worth the space

**Jobs ask for a role, never a model.** `fleet.py` resolves `score` or `write` against what
Ollama actually has installed right now. An earlier version hardcoded a model name, that model
was later removed from the machine, the error was swallowed, and the clip finder reported "no
clips found" indefinitely instead of naming what was missing.

**Captions are timed by transcribing the pipeline's own narration.** The words are already
known, so the transcript is only ever asked for timings. Because it is listening to the exact
audio that ends up in the video, the captions cannot drift. Guessing timings from word counts
drifts within about fifteen seconds.

**Screen capture records to fragmented MP4.** A normal MP4 only becomes readable when its
index is written at the very end, so a capture that is killed leaves a file with a plausible
size that no player will open. A fragmented one is complete at every instant.

**Every stage writes its artifact path before moving on.** A render that fails does not
re-generate the narration, which already cost GPU time and is still good.

## Running it

```
pip install -r requirements.txt
```

Then double-click, or run directly:

```
START_CLIPFORGE.bat     the dashboard on 127.0.0.1:8000
RUN_PIPELINE.bat        does everything queued, on its own
CHECK_EVERYTHING.bat    runs all four test suites
```

Voice cloning needs PyTorch built for your GPU. Install `chatterbox-tts` first, then re-pin
torch afterwards, because its dependency pin will otherwise replace a CUDA build with a
processor-only one and nothing will report an error:

```
pip install --force-reinstall torch torchaudio --index-url https://download.pytorch.org/whl/cu128
python -c "import torch; print(torch.cuda.is_available())"
```

## Rights

Only add source video you own or are licensed to repurpose. Every source requires a
permission record before it enters the pipeline. No posting credentials are included, and
posting is mock by default until you complete each platform's OAuth grant yourself.

MIT licensed.
