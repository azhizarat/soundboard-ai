"""Oráculos independentes da implementação: resposta analítica ITU e identidades de energia."""
import math
import re
import shutil
import subprocess
import numpy as np
import pytest
import loudness


def tone(frequency=1000, amplitude=.1, seconds=3, rate=48000):
    return (amplitude*np.sin(2*np.pi*frequency*np.arange(round(seconds*rate))/rate)).astype(np.float32)


def analytic_lufs(frequency, amplitude):
    # ITU-R BS.1770-5, tabelas 1/2 (48 kHz). Não lê constantes do módulo testado.
    z = np.exp(-2j*np.pi*frequency/48000)
    shelf = (1.53512485958697-2.69169618940638*z+1.19839281085285*z*z)/(1-1.69065929318241*z+.73248077421585*z*z)
    highpass = (1-2*z+z*z)/(1-1.99004745483398*z+.99007225036621*z*z)
    return -.691+10*math.log10(amplitude**2/2*abs(shelf*highpass)**2)


@pytest.mark.parametrize('frequency', [220, 1000, 5000])
def test_mono_tone_matches_independent_itu_frequency_response(frequency):
    measured, estimated = loudness.integrated_lufs(tone(frequency))
    assert not estimated
    assert measured == pytest.approx(analytic_lufs(frequency,.1), abs=.11)


@pytest.mark.parametrize('seconds', [.2, 3])
def test_channel_energy_does_not_cancel_antiphase_and_stereo_adds_three_lu(seconds):
    mono = tone(220, seconds=seconds)
    mono_lufs, estimated = loudness.integrated_lufs(mono)
    same = loudness.analyze_audio_loudness(np.column_stack([mono,mono]))
    inverse = loudness.analyze_audio_loudness(np.column_stack([mono,-mono]))
    assert same['lufs'] == inverse['lufs']
    assert same['lufs'] == pytest.approx(mono_lufs+10*math.log10(2),abs=.11)
    assert same['lufs_estimated'] == estimated


def test_gain_changes_loudness_by_expected_decibels():
    audio = np.column_stack([tone(220),-tone(220)])
    low = loudness.analyze_audio_loudness(audio)
    high = loudness.analyze_audio_loudness(audio*2)
    assert high['lufs']-low['lufs'] == pytest.approx(20*math.log10(2),abs=.11)


def test_sample_peak_ceiling_uses_loudest_channel_before_any_mixdown():
    left=tone(amplitude=.01);right=-left.copy();right[len(right)//2]=.95
    result=loudness.analyze_audio_loudness(np.column_stack([left,right]))
    assert 20*math.log10(.95)+result['loudness_gain_db'] <= -1+1e-5


@pytest.mark.parametrize('rate', [44100, 96000])
def test_resampling_preserves_reference_tone_loudness(rate):
    measured,_=loudness.integrated_lufs(tone(rate=rate),sr=rate)
    assert measured == pytest.approx(analytic_lufs(1000,.1),abs=.11)


def test_downsampling_filters_out_of_band_energy_before_metering():
    measured,_=loudness.integrated_lufs(tone(30000,rate=96000),sr=96000)
    assert measured is None or measured < -55


def test_unknown_channel_layout_is_rejected_instead_of_guessing_weights():
    with pytest.raises(ValueError,match='mono|estéreo'):
        loudness.integrated_lufs(np.zeros((48000,3),dtype=np.float32))


@pytest.mark.parametrize('channels', [1, 2])
def test_optional_ffmpeg_ebur128_oracle(channels):
    executable=shutil.which('ffmpeg')
    if not executable:
        pytest.skip('Oráculo opcional: FFmpeg não instalado; sem downloads')
    mono=tone(1000)
    data=mono if channels==1 else np.column_stack([mono,-mono])
    result=subprocess.run([executable,'-hide_banner','-nostats','-f','f32le','-ar','48000','-ac',str(channels),
        '-i','pipe:0','-af','ebur128=peak=sample','-f','null','-'],input=data.astype('<f4').tobytes(),capture_output=True,timeout=15)
    assert result.returncode==0,result.stderr.decode(errors='replace')
    references=re.findall(r'I:\s*(-?\d+(?:\.\d+)?) LUFS',result.stderr.decode(errors='replace'))
    assert references
    measured,_=loudness.integrated_lufs(data)
    assert measured==pytest.approx(float(references[-1]),abs=.1)
