import pytest

from connor import voice

np = pytest.importorskip("numpy")


class Segment:
    def __init__(self, text, end):
        self.text = text
        self.end = end


class Info:
    duration = 4.0


class FakeModel:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []
        self.audio = []

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        self.audio.append(audio)
        if self.fail:
            raise RuntimeError("Library cublas64_12.dll is not found")
        return iter([Segment(" Hallo Connor,", 1.0), Segment("Untertitel im Auftrag des ZDF", 2.0),
                     Segment(" schreib weiter. ", 4.0)]), Info()


def test_without_onnxruntime_silence_is_trimmed(tmp_path, monkeypatch):
    transcriber = voice.Transcriber("small", "de", "cpu", root=tmp_path)
    monkeypatch.setattr(voice, "missing_packages", lambda need_mic=True: [])
    monkeypatch.setattr(voice, "vad_available", lambda: False)
    model = FakeModel()
    monkeypatch.setattr(transcriber, "_load", lambda device, compute: model)
    quiet = np.full(32000, 0.001, dtype=np.float32)
    speech = np.full(16000, 0.2, dtype=np.float32)
    assert transcriber.transcribe(np.concatenate([quiet, speech, quiet])) == "Hallo Connor, schreib weiter."
    assert model.calls[0]["vad_filter"] is False
    assert 16000 <= len(model.audio[0]) <= 16000 + 2 * 4800 + 480
    assert transcriber.transcribe(np.zeros(16000, dtype=np.float32)) == ""
    assert len(model.calls) == 1, "pure silence never reaches the model"


def test_broken_onnxruntime_falls_back(tmp_path, monkeypatch):
    transcriber = voice.Transcriber("small", "de", "cpu", root=tmp_path)
    monkeypatch.setattr(voice, "missing_packages", lambda need_mic=True: [])
    monkeypatch.setattr(voice, "vad_available", lambda: True)

    class BrokenVad(FakeModel):
        def transcribe(self, audio, **kwargs):
            if kwargs["vad_filter"]:
                raise RuntimeError("Applying the VAD filter requires the onnxruntime package")
            return super().transcribe(audio, **kwargs)

    model = BrokenVad()
    monkeypatch.setattr(transcriber, "_load", lambda device, compute: model)
    assert transcriber.transcribe(np.full(16000, 0.2, dtype=np.float32)) == "Hallo Connor, schreib weiter."
    assert [call["vad_filter"] for call in model.calls] == [False]
    assert transcriber._vad_failed


def test_transcribe_filters_hallucinations_and_reports_progress(tmp_path, monkeypatch):
    transcriber = voice.Transcriber("small", "de", "cpu", root=tmp_path)
    monkeypatch.setattr(voice, "missing_packages", lambda need_mic=True: [])
    monkeypatch.setattr(voice, "vad_available", lambda: True)
    model = FakeModel()
    monkeypatch.setattr(transcriber, "_load", lambda device, compute: model)
    progress = []
    text = transcriber.transcribe(np.zeros(16000, dtype=np.float32), progress=progress.append)
    assert text == "Hallo Connor, schreib weiter."
    assert progress == [0.25, 0.5, 1.0]
    assert model.calls[0]["language"] == "de" and model.calls[0]["vad_filter"] is True
    assert transcriber.is_ready()


def test_gpu_problems_fall_back_to_processor(tmp_path, monkeypatch):
    transcriber = voice.Transcriber("small", "auto", "auto", root=tmp_path)
    monkeypatch.setattr(voice, "missing_packages", lambda need_mic=True: [])
    monkeypatch.setattr(transcriber, "_devices", lambda: [("cuda", "float16"), ("cpu", "int8")]
                        if not transcriber._cuda_failed else [("cpu", "int8")])
    loaded = []

    def load(device, compute):
        loaded.append(device)
        return FakeModel(fail=device == "cuda")

    monkeypatch.setattr(transcriber, "_load", load)
    assert transcriber.transcribe(np.zeros(16000, dtype=np.float32)) == "Hallo Connor, schreib weiter."
    assert loaded == ["cuda", "cpu"]
    transcriber.transcribe(np.zeros(16000, dtype=np.float32))
    assert loaded == ["cuda", "cpu"], "model stays loaded"


def test_missing_model_download_is_explained(tmp_path, monkeypatch):
    transcriber = voice.Transcriber("small", "de", "cpu", root=tmp_path)
    monkeypatch.setattr(voice, "missing_packages", lambda need_mic=True: [])

    def offline(device, compute):
        raise OSError("connection refused")

    monkeypatch.setattr(transcriber, "_load", offline)
    with pytest.raises(voice.VoiceError, match="bist du online"):
        transcriber.transcribe(np.zeros(10, dtype=np.float32))


def test_not_installed(monkeypatch):
    monkeypatch.setattr(voice, "missing_packages", lambda need_mic=True: ["faster_whisper"])
    with pytest.raises(voice.VoiceError, match="start.bat"):
        voice.Transcriber().transcribe(np.zeros(10, dtype=np.float32))


def test_recorder_resamples_to_16k():
    recorder = voice.Recorder()
    recorder._np = np
    recorder._rate = 48000
    recorder._chunks = [np.ones(24000, dtype=np.float32), np.ones(24000, dtype=np.float32)]
    assert recorder.seconds == 1.0
    audio = recorder.stop()
    assert audio.dtype == np.float32 and len(audio) == 16000


def test_configure_ignores_unknown_values():
    transcriber = voice.Transcriber("riesig", "", "quantencomputer")
    assert (transcriber.model_name, transcriber.language, transcriber.device) == ("small", "de", "auto")


def test_decode_audio_resamples_files(tmp_path):
    pytest.importorskip("av")
    import wave

    path = tmp_path / "stereo.wav"
    seconds, rate = 1.5, 44100
    t = np.arange(int(seconds * rate)) / rate
    tone = (np.sin(2 * np.pi * 440 * t) * 12000).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(np.column_stack([tone, tone]).tobytes())
    audio = voice.decode_audio(str(path))
    assert audio.dtype == np.float32
    assert abs(len(audio) - seconds * 16000) < 400
    assert 0.3 < float(np.abs(audio).max()) < 0.4

    empty = tmp_path / "kaputt.mp3"
    empty.write_bytes(b"\x00" * 100)
    transcriber = voice.Transcriber(root=tmp_path)
    with pytest.raises(voice.VoiceError, match="nicht gelesen"):
        transcriber.file_transcriber()(empty, lambda _text: None)
