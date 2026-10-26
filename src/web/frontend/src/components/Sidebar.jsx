import {
  computeLoadMoreVisible,
  formatLoadMoreText,
} from '../sessionUtils';

export default function Sidebar({
  sidebarWidth,
  sessions,
  totalSessionCount,
  isLoadingMore,
  searchQuery,
  onSearchChange,
  activeSession,
  generatingJobs,
  onNewIdea,
  onOpenStatusModal,
  onSelectSession,
  onLoadMore,
}) {
  return (
    <div className="sidebar" style={{ width: `${sidebarWidth}px`, flexShrink: 0 }}>
      <button
        className="new-btn"
        onClick={onNewIdea}
      >
        + New Idea
      </button>

      <button
        className="status-btn"
        onClick={onOpenStatusModal}
        style={{
          marginTop: '0.5rem',
          marginBottom: '1rem',
          background: '#3b82f6',
          color: 'white',
          border: 'none',
          padding: '0.75rem',
          borderRadius: '4px',
          cursor: 'pointer',
          fontWeight: 'bold',
        }}
      >
        🖨️ Printer Status
      </button>

      <div className="search-container">
        <input
          type="text"
          className="search-input"
          placeholder="Search ideas or chats..."
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
        />
      </div>
      <div className="session-list">
        {sessions.map((s) => (
          <div
            key={s.id}
            className={`session-item ${activeSession?.id === s.id ? 'active' : ''}`}
            onClick={() => onSelectSession(s)}
          >
            <div className="session-title">
              {s.isTemp && !s.isEditing && <span className="temp-icon">⏳</span>}
              {(s.isEditing || s.active_job || Boolean(generatingJobs[s.id])) && (
                <div className="mini-spinner"></div>
              )}
              {s.title}
            </div>
            {s.unread && !s.isTemp && <span className="unread-dot">🟢</span>}
          </div>
        ))}
        {computeLoadMoreVisible(sessions.length, totalSessionCount) && (
          <button
            className="load-more-btn"
            onClick={onLoadMore}
            disabled={isLoadingMore}
          >
            {formatLoadMoreText(sessions.length, totalSessionCount, isLoadingMore)}
          </button>
        )}
      </div>
    </div>
  );
}
