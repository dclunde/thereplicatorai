import { formatSupportStatus } from '../sessionUtils';

export default function ChatPanel({
  chatPercent,
  isSessionLoading,
  activeSession,
  chatBottomRef,
  enableSupports,
  onToggleSupports,
  inputText,
  onInputChange,
  onSend,
  isInputDisabled,
}) {
  return (
    <div className="pane" style={{ flex: `0 0 ${chatPercent}%` }}>
      {isSessionLoading && (
        <div className="loader-overlay">
          <div className="progress-bar-container">
            <div className="progress-fill indeterminate"></div>
          </div>
          <div style={{ marginTop: '1rem' }}>Retrieving Session...</div>
        </div>
      )}

      <div className="chat-container">
        {!activeSession ? (
          <div className="empty-state">Start a new 3D design idea below.</div>
        ) : (
          activeSession.history.map((msg, i) => (
            <div key={i} className={`message ${msg.role === 'user' ? 'msg-user' : 'msg-bot'}`}>
              {msg.role === 'user' ? (
                msg.text
              ) : (
                <div>
                  {msg.code ? (
                    <>
                      <strong>AI updated the design</strong>
                      <p style={{ marginTop: '0.5rem' }}>
                        Here is your new 3D model! You can view it in the pane to the right.
                      </p>
                      <div
                        style={{
                          marginTop: '1rem',
                          padding: '0.75rem',
                          background: 'rgba(255,255,255,0.05)',
                          borderRadius: '4px',
                          fontSize: '0.9rem',
                        }}
                      >
                        <div style={{ marginBottom: '0.25rem' }}>
                          📏 <strong>Size:</strong> {msg.size || 'Unknown'}
                        </div>
                        <div style={{ marginBottom: '0.25rem' }}>
                          ⚖️ <strong>Filament:</strong> {msg.grams ? `${msg.grams}g` : 'Unknown'}
                        </div>
                        <div style={{ marginBottom: '0.25rem' }}>
                          ⏱️ <strong>Expected Time:</strong> {msg.time || 'Unknown'}
                        </div>
                        <div>
                          🛠️ <strong>Supports:</strong>{' '}
                          {formatSupportStatus(msg, activeSession.enable_supports)}
                        </div>
                      </div>
                      {msg.warnings && msg.warnings.length > 0 && (
                        <div
                          className="validation-warnings-box"
                          style={{
                            marginTop: '0.5rem',
                            padding: '0.5rem 0.75rem',
                            background: 'rgba(245, 158, 11, 0.12)',
                            border: '1px solid rgba(245, 158, 11, 0.4)',
                            borderRadius: '4px',
                            color: '#fcd34d',
                            fontSize: '0.85rem',
                          }}
                        >
                          <div>
                            ⚠️ <strong>Warnings:</strong>
                          </div>
                          <ul style={{ margin: '0.25rem 0 0 1.2rem', padding: 0 }}>
                            {msg.warnings.map((w, wi) => (
                              <li key={wi}>{w}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      <p style={{ marginTop: '0.75rem', fontStyle: 'italic', opacity: 0.8 }}>
                        Tip: Reply to describe further edits, or click Print below.
                      </p>
                    </>
                  ) : (
                    <div style={{ whiteSpace: 'pre-wrap' }}>{msg.text}</div>
                  )}
                </div>
              )}
            </div>
          ))
        )}
        <div ref={chatBottomRef} />
      </div>

      <div className="input-area" style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0 0.25rem',
          }}
        >
          <label
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.4rem',
              fontSize: '0.85rem',
              color: '#cbd5e1',
              cursor: 'pointer',
              userSelect: 'none',
            }}
          >
            <input
              type="checkbox"
              checked={enableSupports}
              onChange={(e) => onToggleSupports(e.target.checked)}
              disabled={isInputDisabled}
              style={{ cursor: 'pointer' }}
            />
            <span>🛠️ Supports {enableSupports ? '(ON)' : '(OFF)'}</span>
          </label>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem', width: '100%' }}>
          <textarea
            className="chat-input"
            placeholder={
              activeSession &&
              (activeSession.latest_stl ||
                activeSession.all_versions?.some((v) => v.has_code || v.has_stl))
                ? 'Adjust the created STL... (optional: --support or --no-support)'
                : 'Describe a 3D model to generate... (optional: --support)'
            }
            value={inputText}
            onChange={(e) => onInputChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                onSend();
              }
            }}
            disabled={isInputDisabled}
            rows={3}
          />
          <button
            className="send-btn"
            onClick={onSend}
            disabled={isInputDisabled || !inputText.trim()}
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
