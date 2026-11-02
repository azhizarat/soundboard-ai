"""A abertura da interface não carrega filtros que só serão usados no áudio."""
import os
from pathlib import Path
import subprocess
import sys


def test_importing_application_and_identity_resampling_do_not_load_scipy(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = """
import sys
import main
import numpy as np
from audio_processing import resample_audio
data = np.ones((8, 2), dtype=np.float32)
assert resample_audio(data, 48000, 48000) is data
assert resample_audio(data[:0], 44100, 48000).shape == (0, 2)
assert not any(name == 'scipy' or name.startswith('scipy.') for name in sys.modules), 'DSP carregado antes de ser necessário'
"""
    result = subprocess.run([sys.executable, '-c', script], cwd=root,
                            env={**os.environ, 'PYTHONPATH': str(root),
                                 'PYTHONDONTWRITEBYTECODE': '1',
                                 'SOUNDBOARD_MODELS_DIR': str(tmp_path / 'models'),
                                 'SOUNDBOARD_SEPARADOS_ROOT': str(tmp_path / 'separados')},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
