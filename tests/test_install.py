import install


class FakePip:
    def __init__(self, fail=()):
        self.calls = []
        self.fail = fail

    def __call__(self, *args):
        call = " ".join(args)
        self.calls.append(call)
        return not any(part in call for part in self.fail)


def test_full_install(tmp_path):
    marker = tmp_path / ".installed"
    pip = FakePip()
    assert install.main([], run=pip, marker=marker, stop=lambda: False) == 0
    assert marker.read_text(encoding="utf-8") == install.SETUP_VERSION
    assert any("requirements.txt" in call for call in pip.calls)
    assert any("requirements-voice.txt" in call for call in pip.calls)
    assert not any("--no-deps" in call for call in pip.calls)


def test_voice_without_onnxruntime(tmp_path, capsys):
    pip = FakePip(fail=("requirements-voice.txt",))
    assert install.main([], run=pip, marker=tmp_path / ".installed", stop=lambda: False) == 0
    assert any("requirements-voice-lite.txt" in call for call in pip.calls)
    assert "install --no-deps faster-whisper>=1.1" in pip.calls
    assert "ohne die optionale Stille-Erkennung" in capsys.readouterr().out


def test_voice_failure_is_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "_pause", lambda: None)
    marker = tmp_path / ".installed"
    assert install.main([], run=FakePip(fail=("requirements-voice",)), marker=marker, stop=lambda: False) == 0
    assert marker.exists()


def test_core_failure_stops(tmp_path):
    marker = tmp_path / ".installed"
    assert install.main([], run=FakePip(fail=("requirements.txt",)), marker=marker, stop=lambda: False) == 1
    assert not marker.exists()


def test_if_needed(tmp_path):
    marker = tmp_path / ".installed"
    marker.write_text(install.SETUP_VERSION, encoding="utf-8")
    pip = FakePip()
    assert install.main(["--if-needed"], run=pip, marker=marker, stop=lambda: False) == 0 and pip.calls == []
    marker.write_text("2", encoding="utf-8")
    assert install.main(["--if-needed"], run=pip, marker=marker, stop=lambda: False) == 0
    assert pip.calls and marker.read_text(encoding="utf-8") == install.SETUP_VERSION


def test_running_connor_is_stopped_before_installing(tmp_path, capsys):
    stopped = []
    marker = tmp_path / ".installed"
    assert install.main([], run=FakePip(), marker=marker, stop=lambda: stopped.append(1) or True) == 0
    assert stopped == [1] and "für das Update beendet" in capsys.readouterr().out
    assert install.main(["--if-needed"], run=FakePip(), marker=marker, stop=lambda: stopped.append(2) or True) == 0
    assert stopped == [1], "nothing to install – a running Connor stays open"
