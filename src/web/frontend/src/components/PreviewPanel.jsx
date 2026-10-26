import STLViewer from './STLViewer';
import {
  isVersionGenerating,
  getStatusMessage,
  getProgress,
  isVersionListItemGenerating,
} from '../sessionUtils';

export default function PreviewPanel({
  apiBase,
  activeSession,
  selectedVersion,
  onSelectVersion,
  generatingJobs,
  onPrint,
  onRerender,
  onOpenFolder,
  onDelete,
}) {
  const currentJob = activeSession?.id ? generatingJobs[activeSession.id] : null;
  const activeJob = activeSession?.active_job;
  const selectedVersionObj = activeSession?.all_versions?.find(
    (v) => v.version === selectedVersion
  );

  const isSelectedVersionGenerating = isVersionGenerating({
    activeSession,
    selectedVersion,
    currentJob,
    activeJob,
    selectedVersionObj,
  });

  const currentStatusMsg = getStatusMessage({
    currentJob,
    activeJob,
    selectedVersionObj,
    isTemp: activeSession?.isTemp,
    selectedVersion,
  });

  const currentProgress = getProgress({ currentJob, activeJob, selectedVersionObj });

  return (
    <div className="pane viewer-pane" style={{ flex: 1 }}>
      {isSelectedVersionGenerating && (
        <div className="loader-overlay">
          <div className="progress-bar-container">
            <div className="progress-fill" style={{ width: `${currentProgress}%` }}></div>
          </div>
          <div style={{ marginTop: '1rem' }}>
            {activeSession?.isTemp
              ? currentStatusMsg
              : currentStatusMsg.includes(`v${selectedVersion}`)
              ? currentStatusMsg
              : `${currentStatusMsg} (v${selectedVersion})`}
          </div>
        </div>
      )}

      {activeSession && !activeSession.isTemp && !isSelectedVersionGenerating && (
        <div className="viewer-toolbar">
          <div className="viewer-title-group">
            <span className="viewer-title">{activeSession.title}</span>
            {selectedVersion && (
              <span className="viewer-version-tag">v{selectedVersion}</span>
            )}
          </div>
          <div className="action-buttons">
            {(() => {
              const vMatch = activeSession.all_versions?.find(
                (v) => v.version === selectedVersion
              );
              const hasStl = Boolean(vMatch?.has_stl);
              const hasCode = Boolean(vMatch?.has_code);
              return (
                <>
                  {hasStl && (
                    <button className="print-btn" onClick={onPrint}>
                      🖨️ Print to Bambu P2S {selectedVersion ? `v${selectedVersion}` : ''}
                    </button>
                  )}
                  {hasStl && (
                    <button
                      className="secondary-btn"
                      onClick={() => onRerender(selectedVersion)}
                      title="Re-render STL from OpenSCAD"
                    >
                      🔄 Re-render
                    </button>
                  )}
                  {!hasStl && hasCode && (
                    <button
                      className="recompile-nav-btn"
                      onClick={() => onRerender(selectedVersion)}
                    >
                      🔄 Re-render STL
                    </button>
                  )}
                  <button
                    className="secondary-btn"
                    onClick={onOpenFolder}
                    title="Open Folder in File Explorer"
                  >
                    📁 Open Folder
                  </button>
                  <button
                    className="danger-btn"
                    onClick={(e) => onDelete(activeSession.id, e)}
                    title="Delete Idea"
                  >
                    🗑️ Delete Idea
                  </button>
                </>
              );
            })()}
          </div>
        </div>
      )}

      <div className="viewer-body">
        {activeSession &&
          activeSession.all_versions &&
          activeSession.all_versions.length > 0 &&
          !activeSession.isTemp && (
            <div className="version-selector">
              {activeSession.all_versions.map((v) => {
                const isVerGenerating = isVersionListItemGenerating(
                  v,
                  activeJob,
                  currentJob
                );
                return (
                  <button
                    key={v.version}
                    className={`version-btn ${
                      selectedVersion === v.version ? 'active' : ''
                    }`}
                    onClick={() => onSelectVersion(v.version)}
                  >
                    v{v.version}
                    {isVerGenerating ? ' ⏳' : v.has_stl === false ? ' ⚠️' : ''}
                  </button>
                );
              })}
            </div>
          )}

        {(() => {
          if (!activeSession || activeSession.isTemp || isSelectedVersionGenerating)
            return null;
          if (activeSession.all_versions && selectedVersion) {
            const vMatch = activeSession.all_versions.find(
              (v) => v.version === selectedVersion
            );
            if (
              vMatch &&
              vMatch.has_stl === false &&
              !vMatch.isPending &&
              !vMatch.is_generating
            ) {
              if (vMatch.has_code) {
                return (
                  <div className="missing-stl-container">
                    <div className="missing-stl-card">
                      <div className="missing-stl-icon">⚠️</div>
                      <h3>3D Model file (STL) for v{selectedVersion} is missing</h3>
                      <p>
                        The STL was removed or not generated yet. You can re-render it from
                        the source code.
                      </p>
                      <button
                        className="primary-btn missing-stl-action"
                        onClick={() => onRerender(selectedVersion)}
                      >
                        🔄 Re-render STL
                      </button>
                    </div>
                  </div>
                );
              } else {
                return (
                  <div className="missing-stl-container">
                    <div className="missing-stl-card">
                      <div className="missing-stl-icon">⚠️</div>
                      <h3>No 3D Model Generated</h3>
                      <p>
                        The AI encountered an error before generating OpenSCAD code. Check
                        the chat for details, or enter a new prompt below.
                      </p>
                    </div>
                  </div>
                );
              }
            }
          }
          return null;
        })()}

        <STLViewer
          url={(() => {
            if (!activeSession || activeSession.isTemp || isSelectedVersionGenerating)
              return null;
            if (activeSession.all_versions && selectedVersion) {
              const vMatch = activeSession.all_versions.find(
                (v) => v.version === selectedVersion
              );
              if (
                vMatch &&
                (vMatch.isPending || vMatch.is_generating || vMatch.has_stl === false)
              )
                return null;
              if (vMatch && vMatch.stl_url) return `${apiBase}${vMatch.stl_url}`;
            }
            if (activeSession.latest_stl) return `${apiBase}${activeSession.latest_stl}`;
            return null;
          })()}
        />
      </div>
    </div>
  );
}
