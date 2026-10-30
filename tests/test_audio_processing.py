import numpy as np
import pytest
import soundfile as sf
from ai_analyzer import load_audio_mono_16k
from audio_engine import AudioEngine

# O filtro padrão de resample_poly deve preservar o seno de 1 kHz (RMS ~0,707).
# RMS <0,005 verifica ~43 dB de atenuação em 12 kHz na conversão 48 -> 16 kHz.
# É um limiar de regressão nesse ponto, não garantia universal de stopband ou fala.
@pytest.mark.parametrize('frequency,minimum,maximum', [(1000, .70, .72), (12000, 0, .005)])
def test_native_downsample_filters_aliases(tmp_path, monkeypatch, frequency, minimum, maximum):
    import ai_analyzer
    monkeypatch.setattr(ai_analyzer.subprocess, 'run', lambda *a, **k: pytest.fail('Native WAV must not use FFmpeg'))
    path = tmp_path / 'tone.wav'
    sf.write(path, np.sin(2*np.pi*frequency*np.arange(48000)/48000), 48000, subtype='FLOAT')
    data, duration = load_audio_mono_16k(str(path))
    assert duration == 1
    assert data.shape == (16000,)
    assert minimum <= np.sqrt(np.mean(data[100:-100]**2)) <= maximum

@pytest.mark.parametrize('channels', [1, 2, 4])
def test_native_stereo_resampling_and_cache(tmp_path, channels):
    path = tmp_path / 'channels.flac'
    sf.write(path, np.tile(np.arange(channels, dtype=float)*.1+.1, (4410, 1)), 44100)
    engine = AudioEngine(start_loopback=False)
    data, duration = engine.load_clip_stereo_48k(str(path))
    assert data.shape == (4800, 2)
    assert duration == pytest.approx(.1)
    assert np.mean(data[100:-100, 0]) == pytest.approx(.1, abs=.001)
    assert np.mean(data[100:-100, 1]) == pytest.approx(.1 if channels == 1 else .2, abs=.001)
    assert engine.load_clip_stereo_48k(str(path))[0] is data
    assert not data.flags.writeable

def test_shared_resampler_boundaries():
    import importlib.util
    assert importlib.util.find_spec('audio_processing'), 'Shared filtered resampler is missing'
    from audio_processing import resample_audio
    assert resample_audio(np.empty((0, 2)), 48000, 16000).shape == (0, 2)
    assert resample_audio(np.ones(10), 48000, 16000).shape == (4,)
    assert resample_audio(np.ones((3, 2)), 16000, 16000).dtype == np.float32
    for rate in [0, -1, 1.5, float('nan')]:
        with pytest.raises(ValueError):
            resample_audio(np.ones(4), rate, 16000)
    for data in [np.ones((2, 2, 2)), np.array([np.nan]), np.ones((2, 0))]:
        with pytest.raises(ValueError):
            resample_audio(data, 16000, 48000)
