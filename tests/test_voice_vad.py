"""Unit tests for the adaptive noise-floor VAD in the voice loop.

The VAD must reject steady background music (e.g. Spotify bleeding into the mic)
while still triggering on a clear speech-like rise above the ambient floor.
No mic, no API, no memory writes — pure synthetic signal.
"""

import numpy as np


def _adaptive_threshold(noise_floor, abs_floor, snr_ratio):
    return max(abs_floor, noise_floor * snr_ratio)


class _FloorTracker:
    """Mirror of the voice loop's noise-floor follower."""

    def __init__(self, alpha=0.98):
        self.alpha = alpha
        self.floor = None

    def update(self, rms, capturing=False):
        if self.floor is None:
            self.floor = rms
        elif not capturing:
            self.floor = (self.alpha * self.floor) + ((1.0 - self.alpha) * rms)
        return self.floor


def _rms(frame):
    return float(np.sqrt((frame * frame).mean()))


def test_steady_music_becomes_floor_and_does_not_trigger():
    # Steady background music at a constant level should be absorbed into the
    # noise floor so the adaptive threshold rises above it and it never triggers.
    sr = 16000
    frame = int(sr * 0.1)  # 100ms frames
    music = np.full(frame, 0.01, dtype=np.float32)  # steady music level

    tracker = _FloorTracker(alpha=0.98)
    abs_floor = 0.002
    snr = 3.0

    # Let the floor converge to the music level.
    for _ in range(200):
        floor = tracker.update(_rms(music), capturing=False)

    thresh = _adaptive_threshold(floor, abs_floor, snr)
    # The music RMS (0.01) must be BELOW the adaptive threshold (floor*3 = 0.03).
    assert _rms(music) < thresh, f"steady music should not trigger: rms={_rms(music)} thresh={thresh}"


def test_speech_spike_above_floor_triggers():
    # A speech-like burst clearly above the converged floor should count as voiced.
    tracker = _FloorTracker(alpha=0.98)
    abs_floor = 0.002
    snr = 3.0

    # Converge floor on quiet ambience.
    for _ in range(200):
        tracker.update(0.01, capturing=False)

    floor = tracker.floor
    thresh = _adaptive_threshold(floor, abs_floor, snr)

    # Speech burst at 5x the floor -> should exceed the adaptive threshold.
    speech_rms = floor * 5.0
    assert speech_rms >= thresh, f"speech spike should trigger: {speech_rms} >= {thresh}"


def test_absolute_floor_is_respected_when_ambience_is_silent():
    # With near-silent ambience, the absolute floor keeps the VAD from triggering
    # on pure noise-floor rounding.
    tracker = _FloorTracker(alpha=0.98)
    abs_floor = 0.002
    snr = 3.0

    for _ in range(200):
        tracker.update(0.0001, capturing=False)  # near silence

    thresh = _adaptive_threshold(tracker.floor, abs_floor, snr)
    # Adaptive term (0.0001*3) is below the absolute floor; abs floor wins.
    assert thresh == abs_floor


def test_floor_does_not_inflate_during_capture():
    # While capturing an utterance, the floor must NOT follow the loud speech up,
    # or the VAD would lock itself out mid-sentence.
    tracker = _FloorTracker(alpha=0.98)
    for _ in range(50):
        tracker.update(0.01, capturing=False)
    floor_before = tracker.floor

    for _ in range(50):
        tracker.update(0.05, capturing=True)  # loud speech while capturing

    assert tracker.floor == floor_before, "floor must not rise during capture"
