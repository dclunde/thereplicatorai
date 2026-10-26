/**
 * Web Audio API chime player for model-ready notifications.
 * Plays a pleasant, subtle two-tone chime (F#5 -> A5) without requiring any external audio files.
 */

let sharedAudioContext = null;
let lastPlayedAt = 0;
const SOUND_COOLDOWN_MS = 2500;

export function resetSoundThrottleForTesting() {
  lastPlayedAt = 0;
  sharedAudioContext = null;
}

function getAudioContext() {
  const target = typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : null);
  if (!target) return null;
  const AudioCtx = target.AudioContext || target.webkitAudioContext;
  if (!AudioCtx) return null;

  if (!sharedAudioContext || sharedAudioContext.state === 'closed') {
    try {
      sharedAudioContext = new AudioCtx();
    } catch {
      return null;
    }
  }
  return sharedAudioContext;
}

/**
 * Plays a soft, pleasant notification sound indicating a 3D model is ready.
 * Throttled so multiple concurrent or rapid triggers only play once.
 */
export function playModelReadySound(options = {}) {
  const force = typeof options === 'boolean' ? options : Boolean(options?.force);
  const now = Date.now();
  if (!force && now - lastPlayedAt < SOUND_COOLDOWN_MS) {
    return;
  }
  lastPlayedAt = now;

  try {
    const ctx = getAudioContext();
    if (!ctx) return;

    if (ctx.state === 'suspended') {
      ctx.resume().catch(() => {});
    }

    const nowCtx = ctx.currentTime;

    // Helper to play a smooth sine tone with attack and exponential decay
    const playTone = (freq, startTime, duration, peakGain = 0.12) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();

      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, startTime);

      // Smooth envelope: quick ramp-up to prevent click, exponential decay to zero
      gain.gain.setValueAtTime(0.0001, startTime);
      gain.gain.exponentialRampToValueAtTime(peakGain, startTime + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, startTime + duration);

      osc.connect(gain);
      gain.connect(ctx.destination);

      osc.start(startTime);
      osc.stop(startTime + duration + 0.05);
    };

    // Note 1: 740 Hz (F#5)
    playTone(739.99, nowCtx, 0.14, 0.10);
    // Note 2: 880 Hz (A5) slightly higher & brighter
    playTone(880.00, nowCtx + 0.10, 0.24, 0.12);
  } catch (err) {
    // Fail silently so audio issues never interrupt UI flow
    console.debug('Could not play model-ready sound:', err);
  }
}
