"""Common interface every pluggable ASR engine implements.

Engines are heavy (multi-GB local models) and loaded lazily: is_installed()
must be cheap (import check only, no download), load() does the actual
download/load and may take a long time, unload() frees VRAM/RAM so another
engine can load without contention.
"""

from __future__ import annotations

from typing import Optional

import numpy as np


class ASREngine:
    key: str = "base"
    display_name: str = "Base"
    experimental: bool = False

    def is_installed(self) -> bool:
        """Cheap check: are this engine's Python packages importable?
        Does not guarantee the model weights are downloaded yet."""
        raise NotImplementedError

    def load(self) -> Optional[str]:
        """Load/download the model. Returns an error message on failure, else None."""
        raise NotImplementedError

    def unload(self) -> None:
        """Free the loaded model (VRAM/RAM) so another engine can load cleanly."""
        raise NotImplementedError

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000, language: Optional[str] = None) -> str:
        """audio: mono float32 PCM. Raises on failure -- callers should catch."""
        raise NotImplementedError
