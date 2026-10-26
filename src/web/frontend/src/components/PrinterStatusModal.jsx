export default function PrinterStatusModal({
  isOpen,
  onClose,
  isFetchingStatus,
  printerStatus,
  onRefreshStatus,
  onStopPrint,
  onFetchSnapshot,
  isFetchingSnapshot,
  snapshotUrl,
}) {
  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Printer Status</h2>
          <button className="close-btn" onClick={onClose} aria-label="Close">
            ✖️
          </button>
        </div>
        <div className="modal-body">
          {isFetchingStatus ? (
            <p>Loading status...</p>
          ) : (
            <pre
              style={{
                whiteSpace: 'pre-wrap',
                background: 'rgba(0,0,0,0.2)',
                padding: '1rem',
                borderRadius: '4px',
              }}
            >
              {printerStatus}
            </pre>
          )}
          <div style={{ marginTop: '1rem', display: 'flex', gap: '1rem' }}>
            <button
              className="secondary-btn"
              onClick={onRefreshStatus}
              disabled={isFetchingStatus}
            >
              Refresh Status
            </button>
            <button
              className="secondary-btn"
              onClick={onStopPrint}
              disabled={isFetchingStatus}
              style={{
                backgroundColor: '#ef4444',
                borderColor: '#ef4444',
                color: 'white',
              }}
            >
              Stop Print
            </button>
            <button
              className="primary-btn"
              onClick={onFetchSnapshot}
              disabled={isFetchingSnapshot}
              style={
                isFetchingSnapshot
                  ? {
                      backgroundColor: '#94a3b8',
                      borderColor: '#94a3b8',
                      cursor: 'not-allowed',
                    }
                  : { backgroundColor: '#10b981', borderColor: '#10b981' }
              }
            >
              {isFetchingSnapshot ? 'Getting Snapshot...' : 'Get Snapshot'}
            </button>
          </div>
          {snapshotUrl && (
            <div style={{ marginTop: '1rem' }}>
              <img
                src={snapshotUrl}
                alt="Printer Snapshot"
                style={{
                  maxWidth: '100%',
                  borderRadius: '4px',
                  border: '1px solid #ccc',
                }}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
