import { describe, it, expect, beforeEach } from 'vitest';
import {
  isVersionGenerating,
  getStatusMessage,
  getProgress,
  isVersionListItemGenerating,
  isInputDisabled,
  updateJobState,
  clearJobState,
  hasVersionCode,
  canRerender,
  INITIAL_PAGE_SIZE,
  LOAD_MORE_PAGE_SIZE,
  computeLoadMoreVisible,
  formatLoadMoreText,
  mergeSessions,
  computeRequiredGrams,
  formatSupportStatus,
  isPrinterStateBusy,
  ACTIVE_PRINTER_STATES,
} from '../sessionUtils';
import { playModelReadySound, resetSoundThrottleForTesting } from '../audioUtils';

describe('isVersionGenerating', () => {
  it('returns true for temporary sessions', () => {
    expect(isVersionGenerating({
      activeSession: { isTemp: true },
      selectedVersion: 1,
      currentJob: null,
      activeJob: null,
      selectedVersionObj: null,
    })).toBe(true);
  });

  it('returns true when editing and pendingVersion matches', () => {
    expect(isVersionGenerating({
      activeSession: { isEditing: true, pendingVersion: 2 },
      selectedVersion: 2,
      currentJob: null,
      activeJob: null,
      selectedVersionObj: null,
    })).toBe(true);
  });

  it('returns false when editing but pendingVersion differs', () => {
    expect(isVersionGenerating({
      activeSession: { isEditing: true, pendingVersion: 3 },
      selectedVersion: 2,
      currentJob: null,
      activeJob: null,
      selectedVersionObj: null,
    })).toBe(false);
  });

  it('returns true when activeJob targets selected version', () => {
    expect(isVersionGenerating({
      activeSession: { id: 'sess1' },
      selectedVersion: 2,
      currentJob: null,
      activeJob: { target_version: 2 },
      selectedVersionObj: null,
    })).toBe(true);
  });

  it('returns true when currentJob targets selected version', () => {
    expect(isVersionGenerating({
      activeSession: { id: 'sess1' },
      selectedVersion: 3,
      currentJob: { targetVersion: 3, statusMsg: 'Building...', progress: 50 },
      activeJob: null,
      selectedVersionObj: null,
    })).toBe(true);
  });

  it('returns true when selectedVersionObj.is_generating is set', () => {
    expect(isVersionGenerating({
      activeSession: { id: 'sess1' },
      selectedVersion: 1,
      currentJob: null,
      activeJob: null,
      selectedVersionObj: { version: 1, is_generating: true },
    })).toBe(true);
  });

  it('returns true when selectedVersionObj.isPending is set', () => {
    expect(isVersionGenerating({
      activeSession: { id: 'sess1' },
      selectedVersion: 1,
      currentJob: null,
      activeJob: null,
      selectedVersionObj: { version: 1, isPending: true },
    })).toBe(true);
  });

  it('returns false for a completed version with no active jobs', () => {
    expect(isVersionGenerating({
      activeSession: { id: 'sess1' },
      selectedVersion: 1,
      currentJob: null,
      activeJob: null,
      selectedVersionObj: { version: 1, has_stl: true },
    })).toBe(false);
  });

  it('returns false when activeJob targets a different version', () => {
    expect(isVersionGenerating({
      activeSession: { id: 'sess1' },
      selectedVersion: 1,
      currentJob: null,
      activeJob: { target_version: 2 },
      selectedVersionObj: { version: 1, has_stl: true },
    })).toBe(false);
  });
});

describe('getStatusMessage', () => {
  it('prioritizes currentJob.statusMsg', () => {
    expect(getStatusMessage({
      currentJob: { statusMsg: 'From frontend' },
      activeJob: { message: 'From server' },
      selectedVersionObj: { status_message: 'From version' },
      isTemp: false,
      selectedVersion: 2,
    })).toBe('From frontend');
  });

  it('falls back to activeJob.message', () => {
    expect(getStatusMessage({
      currentJob: null,
      activeJob: { message: 'From server' },
      selectedVersionObj: { status_message: 'From version' },
      isTemp: false,
      selectedVersion: 2,
    })).toBe('From server');
  });

  it('falls back to selectedVersionObj.status_message', () => {
    expect(getStatusMessage({
      currentJob: null,
      activeJob: null,
      selectedVersionObj: { status_message: 'From version' },
      isTemp: false,
      selectedVersion: 2,
    })).toBe('From version');
  });

  it('falls back to default message for temp session', () => {
    expect(getStatusMessage({
      currentJob: null,
      activeJob: null,
      selectedVersionObj: null,
      isTemp: true,
      selectedVersion: 1,
    })).toBe('Designing & Compiling...');
  });

  it('falls back to default message with version for non-temp session', () => {
    expect(getStatusMessage({
      currentJob: null,
      activeJob: null,
      selectedVersionObj: null,
      isTemp: false,
      selectedVersion: 3,
    })).toBe('Designing & Compiling v3...');
  });

  it('skips empty statusMsg from currentJob', () => {
    expect(getStatusMessage({
      currentJob: { statusMsg: '' },
      activeJob: { message: 'From server' },
      selectedVersionObj: null,
      isTemp: false,
      selectedVersion: 1,
    })).toBe('From server');
  });
});

describe('getProgress', () => {
  it('prioritizes currentJob.progress', () => {
    expect(getProgress({
      currentJob: { progress: 75 },
      activeJob: { progress: 50 },
      selectedVersionObj: { progress: 25 },
    })).toBe(75);
  });

  it('falls back to activeJob.progress', () => {
    expect(getProgress({
      currentJob: null,
      activeJob: { progress: 50 },
      selectedVersionObj: { progress: 25 },
    })).toBe(50);
  });

  it('falls back to selectedVersionObj.progress', () => {
    expect(getProgress({
      currentJob: null,
      activeJob: null,
      selectedVersionObj: { progress: 25 },
    })).toBe(25);
  });

  it('defaults to 0 when all sources are null', () => {
    expect(getProgress({
      currentJob: null,
      activeJob: null,
      selectedVersionObj: null,
    })).toBe(0);
  });

  it('uses currentJob.progress=0 instead of falling back (nullish coalescing)', () => {
    expect(getProgress({
      currentJob: { progress: 0 },
      activeJob: { progress: 60 },
      selectedVersionObj: null,
    })).toBe(0);
  });
});

describe('isVersionListItemGenerating', () => {
  it('returns true when version has is_generating flag', () => {
    expect(isVersionListItemGenerating(
      { version: 2, is_generating: true },
      null,
      null,
    )).toBe(true);
  });

  it('returns true when version has isPending flag', () => {
    expect(isVersionListItemGenerating(
      { version: 2, isPending: true },
      null,
      null,
    )).toBe(true);
  });

  it('returns true when activeJob targets this version', () => {
    expect(isVersionListItemGenerating(
      { version: 2, has_stl: false },
      { target_version: 2 },
      null,
    )).toBe(true);
  });

  it('returns true when currentJob targets this version', () => {
    expect(isVersionListItemGenerating(
      { version: 3 },
      null,
      { targetVersion: 3 },
    )).toBe(true);
  });

  it('returns false for a completed version', () => {
    expect(isVersionListItemGenerating(
      { version: 1, has_stl: true },
      null,
      null,
    )).toBe(false);
  });

  it('returns false when jobs target different versions', () => {
    expect(isVersionListItemGenerating(
      { version: 1 },
      { target_version: 2 },
      { targetVersion: 3 },
    )).toBe(false);
  });
});

describe('isInputDisabled', () => {
  it('returns true for temp sessions', () => {
    expect(isInputDisabled({ isTemp: true }, {})).toBe(true);
  });

  it('returns true when session is editing', () => {
    expect(isInputDisabled({ isEditing: true, id: 'sess1' }, {})).toBe(true);
  });

  it('returns true when session has active_job', () => {
    expect(isInputDisabled({ id: 'sess1', active_job: { target_version: 2 } }, {})).toBe(true);
  });

  it('returns true when generatingJobs has entry for session', () => {
    expect(isInputDisabled(
      { id: 'sess1' },
      { sess1: { statusMsg: 'Building...', progress: 50 } },
    )).toBe(true);
  });

  it('returns false for idle session with no jobs', () => {
    expect(isInputDisabled({ id: 'sess1' }, {})).toBe(false);
  });

  it('returns false when activeSession is null', () => {
    expect(isInputDisabled(null, {})).toBe(false);
  });

  it('returns false when generatingJobs has entries for other sessions', () => {
    expect(isInputDisabled(
      { id: 'sess1' },
      { sess2: { statusMsg: 'Building...', progress: 50 } },
    )).toBe(false);
  });
});

describe('updateJobState', () => {
  it('creates new job entry in empty state', () => {
    const result = updateJobState({}, 'sess1', 'Starting...', 0, 1);
    expect(result).toEqual({
      sess1: { statusMsg: 'Starting...', progress: 0, targetVersion: 1 },
    });
  });

  it('updates existing job preserving other sessions', () => {
    const prev = {
      sess1: { statusMsg: 'Old', progress: 10, targetVersion: 1 },
      sess2: { statusMsg: 'Other', progress: 50, targetVersion: 2 },
    };
    const result = updateJobState(prev, 'sess1', 'Compiling...', 75, 1);
    expect(result.sess1).toEqual({ statusMsg: 'Compiling...', progress: 75, targetVersion: 1 });
    expect(result.sess2).toEqual(prev.sess2);
  });

  it('preserves existing values when undefined is passed', () => {
    const prev = {
      sess1: { statusMsg: 'Building...', progress: 60, targetVersion: 2 },
    };
    const result = updateJobState(prev, 'sess1', undefined, undefined, undefined);
    expect(result.sess1).toEqual({ statusMsg: 'Building...', progress: 60, targetVersion: 2 });
  });

  it('does not mutate previous state', () => {
    const prev = { sess1: { statusMsg: 'Old', progress: 10, targetVersion: 1 } };
    const result = updateJobState(prev, 'sess1', 'New', 50, 1);
    expect(prev.sess1.statusMsg).toBe('Old');
    expect(result.sess1.statusMsg).toBe('New');
  });
});

describe('clearJobState', () => {
  it('removes the specified session', () => {
    const prev = {
      sess1: { statusMsg: 'A', progress: 10, targetVersion: 1 },
      sess2: { statusMsg: 'B', progress: 20, targetVersion: 2 },
    };
    const result = clearJobState(prev, 'sess1');
    expect(result).toEqual({ sess2: prev.sess2 });
    expect('sess1' in result).toBe(false);
  });

  it('returns same reference when session does not exist', () => {
    const prev = { sess1: { statusMsg: 'A', progress: 10, targetVersion: 1 } };
    const result = clearJobState(prev, 'nonexistent');
    expect(result).toBe(prev);
  });

  it('does not mutate previous state', () => {
    const prev = { sess1: { statusMsg: 'A', progress: 10, targetVersion: 1 } };
    const result = clearJobState(prev, 'sess1');
    expect('sess1' in prev).toBe(true);
    expect('sess1' in result).toBe(false);
  });
});

describe('playModelReadySound', () => {
  beforeEach(() => {
    resetSoundThrottleForTesting();
  });

  it('does not throw when AudioContext is undefined', () => {
    const target = typeof window !== 'undefined' ? window : globalThis;
    const originalAudioContext = target.AudioContext;
    const originalWebkit = target.webkitAudioContext;
    try {
      delete target.AudioContext;
      delete target.webkitAudioContext;
      expect(() => playModelReadySound()).not.toThrow();
    } finally {
      if (originalAudioContext !== undefined) target.AudioContext = originalAudioContext;
      if (originalWebkit !== undefined) target.webkitAudioContext = originalWebkit;
    }
  });

  it('synthesizes chime with oscillators and gains when AudioContext is present', () => {
    let resumed = false;
    const createdOscillators = [];
    const createdGains = [];

    class MockGainNode {
      constructor() {
        this.gain = {
          setValueAtTime: () => {},
          exponentialRampToValueAtTime: () => {},
        };
        createdGains.push(this);
      }
      connect() {}
    }

    class MockOscillatorNode {
      constructor() {
        this.type = 'sine';
        this.frequency = { setValueAtTime: () => {} };
        this.started = false;
        this.stopped = false;
        createdOscillators.push(this);
      }
      connect() {}
      start() { this.started = true; }
      stop() { this.stopped = true; }
    }

    class MockAudioContext {
      constructor() {
        this.state = 'suspended';
        this.currentTime = 0;
        this.destination = {};
      }
      resume() {
        resumed = true;
        this.state = 'running';
        return Promise.resolve();
      }
      createOscillator() { return new MockOscillatorNode(); }
      createGain() { return new MockGainNode(); }
    }

    const target = typeof window !== 'undefined' ? window : globalThis;
    const originalAudioContext = target.AudioContext;
    try {
      target.AudioContext = MockAudioContext;
      expect(() => playModelReadySound()).not.toThrow();
      expect(resumed).toBe(true);
      expect(createdOscillators.length).toBe(2);
      expect(createdGains.length).toBe(2);
      expect(createdOscillators[0].started).toBe(true);
      expect(createdOscillators[1].started).toBe(true);
    } finally {
      if (originalAudioContext !== undefined) {
        target.AudioContext = originalAudioContext;
      } else {
        delete target.AudioContext;
      }
    }
  });

  it('throttles rapid repeated calls so the chime is only played once', () => {
    const createdOscillators = [];

    class MockGainNode {
      constructor() {
        this.gain = {
          setValueAtTime: () => {},
          exponentialRampToValueAtTime: () => {},
        };
      }
      connect() {}
    }

    class MockOscillatorNode {
      constructor() {
        this.type = 'sine';
        this.frequency = { setValueAtTime: () => {} };
        createdOscillators.push(this);
      }
      connect() {}
      start() {}
      stop() {}
    }

    class MockAudioContext {
      constructor() {
        this.state = 'running';
        this.currentTime = 0;
        this.destination = {};
      }
      resume() { return Promise.resolve(); }
      createOscillator() { return new MockOscillatorNode(); }
      createGain() { return new MockGainNode(); }
    }

    const target = typeof window !== 'undefined' ? window : globalThis;
    const originalAudioContext = target.AudioContext;
    try {
      target.AudioContext = MockAudioContext;
      // First call should play 2 tones
      playModelReadySound();
      expect(createdOscillators.length).toBe(2);

      // Immediate second and third calls should be throttled (no additional tones created)
      playModelReadySound();
      playModelReadySound();
      expect(createdOscillators.length).toBe(2);

      // After throttle reset, playing sound works again
      resetSoundThrottleForTesting();
      playModelReadySound();
      expect(createdOscillators.length).toBe(4);

      // Force option bypasses throttle
      playModelReadySound({ force: true });
      expect(createdOscillators.length).toBe(6);
    } finally {
      if (originalAudioContext !== undefined) {
        target.AudioContext = originalAudioContext;
      } else {
        delete target.AudioContext;
      }
    }
  });
});

describe('hasVersionCode', () => {
  it('returns true when version has has_code: true', () => {
    expect(hasVersionCode({ version: 1, has_code: true })).toBe(true);
  });

  it('returns false when version has has_code: false', () => {
    expect(hasVersionCode({ version: 1, has_code: false })).toBe(false);
  });

  it('returns false when version object is null or undefined', () => {
    expect(hasVersionCode(null)).toBe(false);
    expect(hasVersionCode(undefined)).toBe(false);
  });
});

describe('canRerender', () => {
  it('returns true when version has code and is not generating', () => {
    expect(canRerender({ version: 1, has_code: true, has_stl: false }, false)).toBe(true);
  });

  it('returns false when version has no code', () => {
    expect(canRerender({ version: 1, has_code: false, has_stl: false }, false)).toBe(false);
  });

  it('returns false when version is currently generating', () => {
    expect(canRerender({ version: 1, has_code: true, has_stl: false }, true)).toBe(false);
  });

  it('returns false when version object is null or undefined', () => {
    expect(canRerender(null, false)).toBe(false);
  });

  it('defaults isGenerating to false when omitted', () => {
    expect(canRerender({ version: 1, has_code: true })).toBe(true);
  });
});

describe('playModelReadySound edge cases', () => {
  beforeEach(() => {
    resetSoundThrottleForTesting();
  });

  it('handles AudioContext constructor throwing and closed state recovery', () => {
    const target = typeof window !== 'undefined' ? window : globalThis;
    const originalAudioContext = target.AudioContext;
    try {
      target.AudioContext = class ThrowingAudioContext {
        constructor() {
          throw new Error('Audio blocked');
        }
      };
      expect(() => playModelReadySound(true)).not.toThrow();

      let instances = 0;
      target.AudioContext = class RecoverableAudioContext {
        constructor() {
          instances += 1;
          this.state = instances === 1 ? 'closed' : 'running';
          this.currentTime = 0;
          this.destination = {};
        }
        createOscillator() {
          return {
            frequency: { setValueAtTime: () => {} },
            connect: () => {},
            start: () => {},
            stop: () => {},
          };
        }
        createGain() {
          return {
            gain: {
              setValueAtTime: () => {},
              exponentialRampToValueAtTime: () => {},
            },
            connect: () => {},
          };
        }
      };
      playModelReadySound(true);
      playModelReadySound(true);
      expect(instances).toBe(2);
    } finally {
      if (originalAudioContext !== undefined) {
        target.AudioContext = originalAudioContext;
      } else {
        delete target.AudioContext;
      }
    }
  });

  it('catches errors thrown inside createOscillator without crashing', () => {
    const target = typeof window !== 'undefined' ? window : globalThis;
    const originalAudioContext = target.AudioContext;
    try {
      target.AudioContext = class FaultyOscAudioContext {
        constructor() {
          this.state = 'running';
          this.currentTime = 0;
        }
        createOscillator() {
          throw new Error('Oscillator failure');
        }
      };
      expect(() => playModelReadySound(true)).not.toThrow();
    } finally {
      if (originalAudioContext !== undefined) {
        target.AudioContext = originalAudioContext;
      } else {
        delete target.AudioContext;
      }
    }
  });
});

describe('session pagination and load more utils', () => {
  it('defines correct page sizes for initial and load-more requests', () => {
    expect(INITIAL_PAGE_SIZE).toBe(20);
    expect(LOAD_MORE_PAGE_SIZE).toBe(10);
  });

  it('determines visibility of Load More button correctly', () => {
    expect(computeLoadMoreVisible(10, 25)).toBe(true);
    expect(computeLoadMoreVisible(20, 20)).toBe(false);
    expect(computeLoadMoreVisible(25, 20)).toBe(false);
    expect(computeLoadMoreVisible(0, 5)).toBe(true);
    expect(computeLoadMoreVisible(0, 0)).toBe(false);
    expect(computeLoadMoreVisible(null, 20)).toBe(false);
    expect(computeLoadMoreVisible(10, undefined)).toBe(false);
  });

  it('formats Load More button text accurately', () => {
    expect(formatLoadMoreText(20, 45, false)).toBe('Load More (Showing 20 of 45)');
    expect(formatLoadMoreText(10, 15, false)).toBe('Load More (Showing 10 of 15)');
    expect(formatLoadMoreText(20, 45, true)).toBe('Loading...');
  });

  it('merges sessions without introducing duplicate IDs', () => {
    const existing = [
      { id: 'sess-1', title: 'Session 1' },
      { id: 'sess-2', title: 'Session 2' },
    ];
    const incoming = [
      { id: 'sess-2', title: 'Session 2 Updated' },
      { id: 'sess-3', title: 'Session 3' },
    ];
    const merged = mergeSessions(existing, incoming);
    expect(merged.length).toBe(3);
    expect(merged.map(s => s.id)).toEqual(['sess-1', 'sess-2', 'sess-3']);
    expect(merged[1].title).toBe('Session 2'); // preserves existing
  });

  it('handles null/undefined/empty arrays gracefully when merging', () => {
    expect(mergeSessions(null, [{ id: '1' }])).toEqual([{ id: '1' }]);
    expect(mergeSessions([{ id: '1' }], null)).toEqual([{ id: '1' }]);
    expect(mergeSessions([], [])).toEqual([]);
  });
});

describe('computeRequiredGrams', () => {
  it('returns 0 for null, temp, or empty-history sessions', () => {
    expect(computeRequiredGrams(null, 1)).toBe(0);
    expect(computeRequiredGrams({ isTemp: true, history: [] }, 1)).toBe(0);
    expect(computeRequiredGrams({ history: [] }, 1)).toBe(0);
  });

  it('correctly indexes code messages when history contains interleaved error messages', () => {
    const session = {
      id: 'sess-err',
      versions: 2,
      history: [
        { role: 'user', text: 'make a box' },
        { role: 'assistant', text: 'Error: AI failed to respond (timeout).' },
        { role: 'user', text: 'try again' },
        { role: 'assistant', code: 'cube(10);', grams: 6.4 },
        { role: 'user', text: 'make it huge' },
        { role: 'assistant', text: 'Error compiling 3D model: syntax error' },
        { role: 'user', text: 'make it 20mm' },
        { role: 'assistant', code: 'cube(20);', grams: 18.9 },
      ],
    };
    expect(computeRequiredGrams(session, 1)).toBe(6.4);
    expect(computeRequiredGrams(session, 2)).toBe(18.9);
    expect(computeRequiredGrams(session, 3)).toBe(0);
  });
});

describe('formatSupportStatus', () => {
  it('formats per-version support status with has_supports detail', () => {
    expect(formatSupportStatus({ enable_supports: true, has_supports: true }, false)).toBe(
      'Enabled (Tree supports generated)'
    );
    expect(formatSupportStatus({ enable_supports: true, has_supports: false }, false)).toBe(
      'Enabled (None needed)'
    );
    expect(formatSupportStatus({ enable_supports: true }, false)).toBe('Enabled');
    expect(formatSupportStatus({ enable_supports: false, has_supports: true }, true)).toBe(
      'Disabled'
    );
    expect(formatSupportStatus({}, true)).toBe('Enabled');
    expect(formatSupportStatus({}, false)).toBe('Disabled');
  });
});

describe('isPrinterStateBusy', () => {
  it('identifies RUNNING, PREPARE, PAUSE, and PAUSED as busy states', () => {
    expect(ACTIVE_PRINTER_STATES).toEqual(['RUNNING', 'PREPARE', 'PAUSE', 'PAUSED']);
    expect(isPrinterStateBusy('RUNNING')).toBe(true);
    expect(isPrinterStateBusy('PREPARE')).toBe(true);
    expect(isPrinterStateBusy('PAUSE')).toBe(true);
    expect(isPrinterStateBusy('PAUSED')).toBe(true);
    expect(isPrinterStateBusy('IDLE')).toBe(false);
    expect(isPrinterStateBusy('FINISH')).toBe(false);
    expect(isPrinterStateBusy('FAILED')).toBe(false);
    expect(isPrinterStateBusy(null)).toBe(false);
  });
});



