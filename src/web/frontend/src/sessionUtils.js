/**
 * Pure utility functions for session state logic.
 * Extracted from App.jsx for testability and reuse.
 */

/**
 * Determine whether the selected version is currently being generated.
 * Returns true if the version is actively in-progress or pending.
 *
 * @param {Object} params
 * @param {Object|null} params.activeSession - The currently active session
 * @param {number|null} params.selectedVersion - The version number the user has selected
 * @param {Object|null} params.currentJob - The generatingJobs entry for this session (from frontend state)
 * @param {Object|null} params.activeJob - The active_job from the server (activeSession.active_job)
 * @param {Object|null} params.selectedVersionObj - The version object from all_versions matching selectedVersion
 * @returns {boolean}
 */
export function isVersionGenerating({ activeSession, selectedVersion, currentJob, activeJob, selectedVersionObj }) {
  return Boolean(
    activeSession?.isTemp ||
    (activeSession?.isEditing && activeSession?.pendingVersion === selectedVersion) ||
    (activeJob && activeJob.target_version === selectedVersion) ||
    (currentJob && currentJob.targetVersion === selectedVersion) ||
    selectedVersionObj?.is_generating ||
    selectedVersionObj?.isPending
  );
}

/**
 * Compute the status message to display for the current generation.
 * Resolves from multiple sources in priority order.
 *
 * @param {Object} params
 * @param {Object|null} params.currentJob - Frontend generatingJobs entry
 * @param {Object|null} params.activeJob - Server-side active_job
 * @param {Object|null} params.selectedVersionObj - Version object from all_versions
 * @param {boolean} params.isTemp - Whether the session is temporary
 * @param {number|null} params.selectedVersion - The selected version number
 * @returns {string}
 */
export function getStatusMessage({ currentJob, activeJob, selectedVersionObj, isTemp, selectedVersion }) {
  return (
    currentJob?.statusMsg ||
    activeJob?.message ||
    selectedVersionObj?.status_message ||
    (isTemp ? "Designing & Compiling..." : `Designing & Compiling v${selectedVersion}...`)
  );
}

/**
 * Compute the progress percentage from multiple sources in priority order.
 *
 * @param {Object} params
 * @param {Object|null} params.currentJob - Frontend generatingJobs entry
 * @param {Object|null} params.activeJob - Server-side active_job
 * @param {Object|null} params.selectedVersionObj - Version object from all_versions
 * @returns {number}
 */
export function getProgress({ currentJob, activeJob, selectedVersionObj }) {
  return currentJob?.progress ?? activeJob?.progress ?? selectedVersionObj?.progress ?? 0;
}

/**
 * Determine whether a specific version in the versions list is currently generating.
 *
 * @param {Object} versionObj - A version entry from all_versions
 * @param {Object|null} activeJob - Server-side active_job
 * @param {Object|null} currentJob - Frontend generatingJobs entry
 * @returns {boolean}
 */
export function isVersionListItemGenerating(versionObj, activeJob, currentJob) {
  return Boolean(
    versionObj.is_generating ||
    versionObj.isPending ||
    (activeJob && activeJob.target_version === versionObj.version) ||
    (currentJob && currentJob.targetVersion === versionObj.version)
  );
}

/**
 * Determine whether input controls should be disabled.
 *
 * @param {Object|null} activeSession - The currently active session
 * @param {Object} generatingJobs - Map of session ID to job state
 * @returns {boolean}
 */
export function isInputDisabled(activeSession, generatingJobs) {
  return Boolean(
    activeSession?.isTemp ||
    activeSession?.isEditing ||
    activeSession?.active_job ||
    (activeSession?.id && generatingJobs[activeSession.id])
  );
}

/**
 * Produce updated generatingJobs state after setting/updating a job.
 *
 * @param {Object} prev - Previous generatingJobs state
 * @param {string} sessionId - Session to update
 * @param {string} [msg] - Status message
 * @param {number} [progress] - Progress percentage
 * @param {number} [targetVersion] - Target version number
 * @returns {Object} New generatingJobs state
 */
export function updateJobState(prev, sessionId, msg, progress, targetVersion) {
  const existing = prev[sessionId] || {};
  return {
    ...prev,
    [sessionId]: {
      statusMsg: msg !== undefined ? msg : (existing.statusMsg || ''),
      progress: progress !== undefined ? progress : (existing.progress || 0),
      targetVersion: targetVersion !== undefined ? targetVersion : existing.targetVersion,
    }
  };
}

/**
 * Produce updated generatingJobs state after clearing a job.
 *
 * @param {Object} prev - Previous generatingJobs state
 * @param {string} sessionId - Session to clear
 * @returns {Object} New generatingJobs state
 */
export function clearJobState(prev, sessionId) {
  if (!prev[sessionId]) return prev;
  const copy = { ...prev };
  delete copy[sessionId];
  return copy;
}

/**
 * Determine whether a version has OpenSCAD source code available.
 *
 * @param {Object|null} versionObj - The version object from all_versions
 * @returns {boolean}
 */
export function hasVersionCode(versionObj) {
  return Boolean(versionObj?.has_code);
}

/**
 * Determine whether re-rendering is possible for a given version.
 * Re-rendering requires that the version has source code and is not currently generating.
 *
 * @param {Object|null} versionObj - The version object from all_versions
 * @param {boolean} [isGenerating=false] - Whether the version is currently generating
 * @returns {boolean}
 */
export function canRerender(versionObj, isGenerating = false) {
  if (!versionObj || isGenerating) return false;
  return Boolean(versionObj.has_code);
}

export const INITIAL_PAGE_SIZE = 20;
export const LOAD_MORE_PAGE_SIZE = 10;

/**
 * Determine whether the "Load More" button should be visible.
 *
 * @param {number} currentCount - Number of sessions currently loaded
 * @param {number} totalCount - Total number of available sessions matching filter
 * @returns {boolean}
 */
export function computeLoadMoreVisible(currentCount, totalCount) {
  return typeof currentCount === 'number' && typeof totalCount === 'number' && currentCount < totalCount;
}

/**
 * Format the text for the "Load More" button.
 *
 * @param {number} currentCount - Number of sessions currently loaded
 * @param {number} totalCount - Total number of available sessions matching filter
 * @param {boolean} isLoading - Whether a load-more request is in flight
 * @returns {string}
 */
export function formatLoadMoreText(currentCount, totalCount, isLoading = false) {
  if (isLoading) return "Loading...";
  return `Load More (Showing ${currentCount} of ${totalCount})`;
}

/**
 * Merge newly fetched sessions into the existing list, avoiding duplicate IDs.
 *
 * @param {Array} existingSessions - Currently loaded sessions
 * @param {Array} newSessions - Newly fetched sessions batch
 * @returns {Array} Merged sessions list
 */
export function mergeSessions(existingSessions = [], newSessions = []) {
  if (!Array.isArray(existingSessions)) return newSessions || [];
  if (!Array.isArray(newSessions)) return existingSessions;
  const existingIds = new Set(existingSessions.map(s => s?.id));
  const uniqueNew = newSessions.filter(s => s && !existingIds.has(s.id));
  return [...existingSessions, ...uniqueNew];
}

/**
 * Compute required filament grams for the selected version by filtering
 * assistant messages that contain OpenSCAD code (ignoring error messages).
 *
 * @param {Object|null} activeSession - Active session object
 * @param {number|null} selectedVersion - 1-based version number
 * @returns {number}
 */
export function computeRequiredGrams(activeSession, selectedVersion) {
  if (!activeSession || activeSession.isTemp || !Array.isArray(activeSession.history)) return 0;
  const vIndex = selectedVersion || activeSession.versions || 1;
  const codeMsgs = activeSession.history.filter(m => m && m.role === 'assistant' && m.code);
  if (vIndex >= 1 && vIndex <= codeMsgs.length) {
    return codeMsgs[vIndex - 1].grams || 0;
  }
  return 0;
}

/**
 * Format the per-version support status label for an assistant message card.
 *
 * @param {Object|null} msg - Assistant message object from session.history
 * @param {boolean} [sessionEnableSupports=false] - Fallback session-level enable_supports
 * @returns {string}
 */
export function formatSupportStatus(msg, sessionEnableSupports = false) {
  const enabled = Boolean(msg?.enable_supports ?? sessionEnableSupports);
  if (!enabled) return 'Disabled';
  if (msg?.has_supports === true) return 'Enabled (Tree supports generated)';
  if (msg?.has_supports === false) return 'Enabled (None needed)';
  return 'Enabled';
}

export const ACTIVE_PRINTER_STATES = ['RUNNING', 'PREPARE', 'PAUSE', 'PAUSED'];

/**
 * Determine whether the printer raw state indicates an active/busy print job.
 *
 * @param {string|null|undefined} printerRawState
 * @returns {boolean}
 */
export function isPrinterStateBusy(printerRawState) {
  return Boolean(printerRawState && ACTIVE_PRINTER_STATES.includes(printerRawState));
}
