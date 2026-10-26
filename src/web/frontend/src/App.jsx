import { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import Sidebar from './components/Sidebar';
import ChatPanel from './components/ChatPanel';
import PreviewPanel from './components/PreviewPanel';
import PrintModal from './components/PrintModal';
import PrinterStatusModal from './components/PrinterStatusModal';
import { useSessions } from './hooks/useSessions';
import { usePrinterStatus } from './hooks/usePrinterStatus';
import {
  INITIAL_PAGE_SIZE,
  computeRequiredGrams,
  isInputDisabled as computeIsInputDisabled,
} from './sessionUtils';
import { playModelReadySound } from './audioUtils';
import './index.css';

const API_BASE = 'http://localhost:8000';

function App() {
  const {
    sessions,
    setSessions,
    sessionsRef,
    totalSessionCount,
    setTotalSessionCount,
    isLoadingMore,
    searchQuery,
    setSearchQuery,
    searchQueryRef,
    fetchSessions,
    handleLoadMore,
  } = useSessions(API_BASE);

  const {
    printerStatus,
    printerRawState,
    isPrinterConnected,
    snapshotUrl,
    isFetchingStatus,
    isFetchingSnapshot,
    amsColors,
    fetchAmsColors,
    fetchPrinterStatus,
    fetchSnapshot,
    handleStopPrint,
  } = usePrinterStatus(API_BASE);

  const [showStatusModal, setShowStatusModal] = useState(false);
  const [showPrintModal, setShowPrintModal] = useState(false);

  const [activeSession, setActiveSession] = useState(null);
  const [inputText, setInputText] = useState('');
  const [isSessionLoading, setIsSessionLoading] = useState(false);
  const [selectedVersion, setSelectedVersion] = useState(null);
  const [generatingJobs, setGeneratingJobs] = useState({});

  const updateJob = (sessionId, msg, progress, targetVersion) => {
    setGeneratingJobs((prev) => {
      const existing = prev[sessionId] || {};
      return {
        ...prev,
        [sessionId]: {
          statusMsg: msg !== undefined ? msg : existing.statusMsg || '',
          progress: progress !== undefined ? progress : existing.progress || 0,
          targetVersion:
            targetVersion !== undefined ? targetVersion : existing.targetVersion,
        },
      };
    });
  };

  const clearJob = (sessionId) => {
    setGeneratingJobs((prev) => {
      if (!prev[sessionId]) return prev;
      const copy = { ...prev };
      delete copy[sessionId];
      return copy;
    });
  };

  const [sidebarWidth, setSidebarWidth] = useState(260);
  const [chatPercent, setChatPercent] = useState(50);
  const [selectedAmsSlot, setSelectedAmsSlot] = useState(null);
  const [isPrinting, setIsPrinting] = useState(false);
  const [enableSupports, setEnableSupports] = useState(false);
  const [printSupports, setPrintSupports] = useState(false);
  const chatBottomRef = useRef(null);
  const mainAreaRef = useRef(null);
  const activeSessionRef = useRef(activeSession);
  activeSessionRef.current = activeSession;

  useEffect(() => {
    if (activeSession && activeSession.enable_supports !== undefined) {
      setEnableSupports(Boolean(activeSession.enable_supports));
    } else if (!activeSession) {
      setEnableSupports(false);
    }
  }, [activeSession]);

  const readNDJSONStream = async (response, { onStep, onInit }) => {
    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let done = false;
    let buffer = '';
    let result = null;

    const handleLine = (parsed) => {
      if (parsed.status === 'init') {
        onInit?.(parsed.session_id, parsed.session);
      } else if (parsed.status === 'step') {
        onStep?.(parsed.message, parsed.progress, parsed.attempt, parsed.max_calls);
      } else if (parsed.status === 'error') {
        const err = new Error(parsed.message);
        err.session = parsed.session;
        err.sessionId = parsed.session_id;
        throw err;
      } else if (parsed.status === 'complete') {
        result = parsed.session;
      }
    };

    while (!done) {
      const { value, done: readerDone } = await reader.read();
      done = readerDone;
      if (value) {
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) {
          if (line.trim() !== '') {
            handleLine(JSON.parse(line));
          }
        }
      }
    }

    if (buffer.trim() !== '') {
      handleLine(JSON.parse(buffer));
    }

    return result;
  };

  const handleSidebarResize = (e) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = sidebarWidth;

    const onMouseMove = (moveEvent) => {
      let newWidth = startWidth + (moveEvent.clientX - startX);
      if (newWidth < 150) newWidth = 150;
      if (newWidth > 600) newWidth = 600;
      setSidebarWidth(newWidth);
    };

    const onMouseUp = () => {
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
      document.body.style.cursor = 'default';
    };

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
    document.body.style.cursor = 'col-resize';
  };

  const handleChatResize = (e) => {
    e.preventDefault();
    const startX = e.clientX;
    const startPercent = chatPercent;
    if (!mainAreaRef.current) return;
    const mainAreaWidth = mainAreaRef.current.offsetWidth;

    const onMouseMove = (moveEvent) => {
      const deltaX = moveEvent.clientX - startX;
      const deltaPercent = (deltaX / mainAreaWidth) * 100;
      let newPercent = startPercent + deltaPercent;
      if (newPercent < 20) newPercent = 20;
      if (newPercent > 80) newPercent = 80;
      setChatPercent(newPercent);
    };

    const onMouseUp = () => {
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
      document.body.style.cursor = 'default';
    };

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
    document.body.style.cursor = 'col-resize';
  };

  useEffect(() => {
    fetchSessions();
    fetchAmsColors();
  }, [fetchSessions, fetchAmsColors]);

  useEffect(() => {
    const delayDebounceFn = setTimeout(() => {
      fetchSessions(searchQuery, {
        append: false,
        limit: INITIAL_PAGE_SIZE,
        offset: 0,
      });
    }, 300);
    return () => clearTimeout(delayDebounceFn);
  }, [searchQuery, fetchSessions]);

  useEffect(() => {
    if (chatBottomRef.current) {
      chatBottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [activeSession?.history]);

  const getRequiredGrams = () => computeRequiredGrams(activeSession, selectedVersion);

  const openStatusModal = () => {
    setShowStatusModal(true);
    fetchPrinterStatus();
  };

  const loadSession = async (s) => {
    if (s.unread) {
      setSessions((prev) =>
        prev.map((sess) => (sess.id === s.id ? { ...sess, unread: false } : sess))
      );
    }

    if (s.isTemp) {
      setActiveSession({ ...s, unread: false });
      return;
    }

    try {
      setIsSessionLoading(true);
      const res = await axios.get(
        `${API_BASE}/sessions/${encodeURIComponent(s.id)}`
      );
      const loaded = res.data;
      if (loaded.active_job) {
        updateJob(
          loaded.id,
          loaded.active_job.message,
          loaded.active_job.progress,
          loaded.active_job.target_version
        );
      }
      setActiveSession({
        ...loaded,
        active_job: loaded.active_job || null,
        isEditing: Boolean(loaded.active_job),
        pendingVersion: loaded.active_job?.target_version,
      });
      if (loaded.active_job?.target_version) {
        setSelectedVersion(loaded.active_job.target_version);
      } else {
        setSelectedVersion(loaded.versions || 1);
      }
    } catch (e) {
      console.error('Failed to load session', e);
    } finally {
      setIsSessionLoading(false);
    }
  };

  const activeSessionId = activeSession?.id;
  const activeSessionHasJob = Boolean(activeSession?.active_job);
  const activeSessionIsTemp = Boolean(activeSession?.isTemp);

  useEffect(() => {
    if (!activeSessionId || !activeSessionHasJob || activeSessionIsTemp) return;

    let isSubscribed = true;
    const interval = setInterval(async () => {
      try {
        const res = await axios.get(
          `${API_BASE}/sessions/${encodeURIComponent(activeSessionId)}`
        );
        if (!isSubscribed) return;
        const updated = res.data;
        const hasActiveJob = Boolean(updated.active_job);

        if (activeSessionRef.current?.id === activeSessionId) {
          setActiveSession((prev) => ({
            ...prev,
            ...updated,
            active_job: updated.active_job || null,
            isEditing: hasActiveJob,
            pendingVersion: updated.active_job?.target_version,
          }));
          if (hasActiveJob) {
            updateJob(
              activeSessionId,
              updated.active_job.message,
              updated.active_job.progress,
              updated.active_job.target_version
            );
          } else {
            clearInterval(interval);
            clearJob(activeSessionId);
            const hasReadyModel = Boolean(
              updated.latest_stl || updated.all_versions?.some((v) => v.has_stl)
            );
            if (hasReadyModel) {
              playModelReadySound();
            }
            if (updated.versions) {
              setSelectedVersion((prev) => prev || updated.versions);
            }
          }
        }
        setSessions((prev) =>
          prev.map((item) =>
            item.id === activeSessionId
              ? {
                  ...item,
                  title: updated.title,
                  last_updated: updated.last_updated,
                  active_job: updated.active_job || null,
                }
              : item
          )
        );
      } catch (err) {
        console.error('Polling error for session', activeSessionId, err);
      }
    }, 2000);

    return () => {
      isSubscribed = false;
      clearInterval(interval);
    };
  }, [activeSessionId, activeSessionHasJob, activeSessionIsTemp, setSessions]);

  const hasAnyActiveJob = sessions.some((s) => Boolean(s.active_job));

  useEffect(() => {
    if (!hasAnyActiveJob) return;

    const interval = setInterval(() => {
      fetchSessions(searchQueryRef.current, {
        append: false,
        limit: Math.max(INITIAL_PAGE_SIZE, sessionsRef.current.length),
        offset: 0,
      });
    }, 3000);

    return () => clearInterval(interval);
  }, [hasAnyActiveJob, fetchSessions, searchQueryRef, sessionsRef]);

  const handleDelete = async (sessionId, e) => {
    e?.stopPropagation?.();
    if (
      window.confirm(
        'Are you sure you want to delete this idea? This cannot be undone.'
      )
    ) {
      try {
        await axios.delete(
          `${API_BASE}/sessions/${encodeURIComponent(sessionId)}`
        );
        clearJob(sessionId);
        setSessions((prev) => prev.filter((s) => s.id !== sessionId));
        setTotalSessionCount((prev) => Math.max(0, prev - 1));
        if (activeSessionRef.current?.id === sessionId) {
          setActiveSession(null);
        }
      } catch (err) {
        console.error(err);
        alert('Failed to delete session');
      }
    }
  };

  const handleSend = async () => {
    if (!inputText.trim()) return;
    const text = inputText;
    setInputText('');

    if (!activeSession || activeSession.isTemp) {
      const tempId = 'temp-' + Date.now();
      let currentSessionId = tempId;
      const timeString = new Date().toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      });
      const tempSession = {
        id: tempId,
        title: `In Progress: ${timeString}`,
        isTemp: true,
        history: [{ role: 'user', text }],
      };
      setSessions((prev) => [tempSession, ...prev]);
      setTotalSessionCount((prev) => prev + 1);
      setActiveSession(tempSession);
      setSelectedVersion(1);

      try {
        updateJob(tempId, 'Starting...', 0, 1);
        const response = await fetch(`${API_BASE}/sessions/new`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: text, enable_supports: enableSupports }),
        });

        const finalSession = await readNDJSONStream(response, {
          onInit: (serverSessionId, initialSession) => {
            currentSessionId = serverSessionId;
            setGeneratingJobs((prev) => {
              const updated = { ...prev };
              if (updated[tempId]) {
                updated[serverSessionId] = updated[tempId];
                delete updated[tempId];
              }
              return updated;
            });
            setSessions((prev) =>
              prev.map((s) =>
                s.id === tempId
                  ? {
                      ...(initialSession || s),
                      id: serverSessionId,
                      isTemp: false,
                    }
                  : s
              )
            );
            setActiveSession((curr) =>
              curr?.id === tempId
                ? {
                    ...(initialSession || curr),
                    id: serverSessionId,
                    isTemp: false,
                  }
                : curr
            );
          },
          onStep: (msg, progress) => {
            updateJob(currentSessionId, msg, progress, 1);
          },
        });

        if (finalSession) {
          clearJob(currentSessionId);
          clearJob(tempId);
          const sanitizedSession = {
            ...finalSession,
            active_job: null,
            isEditing: false,
            pendingVersion: null,
            all_versions:
              finalSession.all_versions?.map((v) => ({
                ...v,
                is_generating: false,
                isPending: false,
              })) || finalSession.all_versions,
          };
          const hasReadyModel = Boolean(
            sanitizedSession.latest_stl ||
              sanitizedSession.all_versions?.some((v) => v.has_stl)
          );
          if (hasReadyModel) {
            playModelReadySound();
          }
          const isCurrentlyViewed =
            activeSessionRef.current?.id === currentSessionId ||
            activeSessionRef.current?.id === tempId;
          setSessions((prev) =>
            prev.map((s) =>
              s.id === currentSessionId || s.id === tempId
                ? { ...sanitizedSession, unread: !isCurrentlyViewed }
                : s
            )
          );
          setActiveSession((current) =>
            current?.id === currentSessionId || current?.id === tempId
              ? sanitizedSession
              : current
          );
          if (isCurrentlyViewed)
            setSelectedVersion(sanitizedSession.versions || 1);
        } else {
          throw new Error('Did not receive complete session data');
        }
      } catch (e) {
        console.error(e);
        const targetId = currentSessionId !== tempId ? currentSessionId : null;
        if (e.session) {
          const errSession = e.session;
          const isCurrentlyViewed =
            activeSessionRef.current?.id === currentSessionId ||
            activeSessionRef.current?.id === tempId;
          setSessions((prev) =>
            prev.map((s) =>
              s.id === currentSessionId || s.id === tempId
                ? { ...errSession, unread: !isCurrentlyViewed }
                : s
            )
          );
          setActiveSession((current) =>
            current?.id === currentSessionId || current?.id === tempId
              ? errSession
              : current
          );
        } else if (targetId) {
          try {
            const res = await axios.get(
              `${API_BASE}/sessions/${encodeURIComponent(targetId)}`
            );
            const errSession = res.data;
            const isCurrentlyViewed =
              activeSessionRef.current?.id === targetId ||
              activeSessionRef.current?.id === tempId;
            setSessions((prev) =>
              prev.map((s) =>
                s.id === targetId || s.id === tempId
                  ? { ...errSession, unread: !isCurrentlyViewed }
                  : s
              )
            );
            setActiveSession((current) =>
              current?.id === targetId || current?.id === tempId
                ? errSession
                : current
            );
          } catch (fetchErr) {
            console.error('Failed to load error state session:', fetchErr);
          }
        }
      } finally {
        clearJob(currentSessionId);
        clearJob(tempId);
      }
    } else {
      const sessionId = activeSession.id;
      const maxExistingVersion = Math.max(
        activeSession.versions || 0,
        ...(activeSession.all_versions?.map((v) => v.version) || [0]),
        1
      );
      const nextVersionNum = maxExistingVersion + 1;

      const optimisticSession = {
        ...activeSession,
        history: [...activeSession.history, { role: 'user', text }],
        isEditing: true,
        pendingVersion: nextVersionNum,
        all_versions: activeSession.all_versions
          ? [
              ...activeSession.all_versions,
              { version: nextVersionNum, isPending: true, is_generating: true },
            ]
          : [{ version: nextVersionNum, isPending: true, is_generating: true }],
      };

      setActiveSession(optimisticSession);
      setSelectedVersion(nextVersionNum);
      setSessions((prev) =>
        prev.map((s) => (s.id === sessionId ? optimisticSession : s))
      );

      try {
        updateJob(sessionId, 'Starting...', 0, nextVersionNum);
        const response = await fetch(
          `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/edit`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt: text, enable_supports: enableSupports }),
          }
        );

        const finalSession = await readNDJSONStream(response, {
          onStep: (msg, progress) => {
            updateJob(sessionId, msg, progress, nextVersionNum);
          },
        });

        if (finalSession) {
          clearJob(sessionId);
          const sanitizedSession = {
            ...finalSession,
            active_job: null,
            isEditing: false,
            pendingVersion: null,
            all_versions:
              finalSession.all_versions?.map((v) => ({
                ...v,
                is_generating: false,
                isPending: false,
              })) || finalSession.all_versions,
          };
          const hasReadyModel = Boolean(
            sanitizedSession.latest_stl ||
              sanitizedSession.all_versions?.some((v) => v.has_stl)
          );
          if (hasReadyModel) {
            playModelReadySound();
          }
          const isCurrentlyViewed = activeSessionRef.current?.id === sessionId;
          setSessions((prev) =>
            prev.map((s) =>
              s.id === sessionId
                ? { ...sanitizedSession, unread: !isCurrentlyViewed }
                : s
            )
          );
          setActiveSession((current) =>
            current?.id === sessionId ? sanitizedSession : current
          );
          if (isCurrentlyViewed)
            setSelectedVersion(sanitizedSession.versions || 1);
        } else {
          throw new Error('Did not receive complete session data');
        }
      } catch (e) {
        console.error(e);
        if (e.session) {
          const errSession = e.session;
          const isCurrentlyViewed = activeSessionRef.current?.id === sessionId;
          setSessions((prev) =>
            prev.map((s) =>
              s.id === sessionId
                ? { ...errSession, unread: !isCurrentlyViewed }
                : s
            )
          );
          setActiveSession((current) =>
            current?.id === sessionId ? errSession : current
          );
          if (isCurrentlyViewed) setSelectedVersion(errSession.versions || 1);
        } else {
          try {
            const res = await axios.get(
              `${API_BASE}/sessions/${encodeURIComponent(sessionId)}`
            );
            const errSession = res.data;
            const isCurrentlyViewed = activeSessionRef.current?.id === sessionId;
            setSessions((prev) =>
              prev.map((s) =>
                s.id === sessionId
                  ? { ...errSession, unread: !isCurrentlyViewed }
                  : s
              )
            );
            setActiveSession((current) =>
              current?.id === sessionId ? errSession : current
            );
            if (isCurrentlyViewed) setSelectedVersion(errSession.versions || 1);
          } catch (fetchErr) {
            console.error('Failed to load error state session:', fetchErr);
          }
        }
      } finally {
        clearJob(sessionId);
      }
    }
  };

  const handlePrint = () => {
    if (!activeSession || activeSession.isTemp) return;
    const vMatch = activeSession.all_versions?.find(
      (v) => v.version === selectedVersion
    );
    if (vMatch && (vMatch.is_generating || vMatch.isPending || vMatch.has_stl === false))
      return;
    setPrintSupports(Boolean(activeSession.enable_supports));
    fetchPrinterStatus();
    fetchAmsColors();
    setShowPrintModal(true);
  };

  const handleConfirmPrint = async () => {
    setIsPrinting(true);
    try {
      await axios.post(
        `${API_BASE}/sessions/${encodeURIComponent(activeSession.id)}/print`,
        {
          version: selectedVersion || activeSession.versions,
          ams_slot: selectedAmsSlot,
          enable_supports: printSupports,
        }
      );
      setShowPrintModal(false);
    } catch (e) {
      console.error(e);
      const detail = e.response?.data?.detail;
      alert(detail ? `Failed to start print job: ${detail}` : 'Failed to start print job');
    } finally {
      setIsPrinting(false);
    }
  };

  const handleOpenFolder = async () => {
    if (!activeSession || activeSession.isTemp) return;
    try {
      await axios.post(
        `${API_BASE}/sessions/${encodeURIComponent(activeSession.id)}/open_folder`,
        { version: selectedVersion || activeSession.versions }
      );
    } catch (e) {
      console.error(e);
      alert('Failed to open folder');
    }
  };

  const handleRerender = async (targetVer) => {
    if (!activeSession || activeSession.isTemp) return;
    const ver = targetVer || selectedVersion || activeSession.versions || 1;
    const sessionId = activeSession.id;

    try {
      updateJob(sessionId, `Re-rendering v${ver}...`, 0, ver);
      setIsSessionLoading(true);

      const response = await fetch(
        `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/versions/${ver}/rerender`,
        {
          method: 'POST',
        }
      );

      const finalSession = await readNDJSONStream(response, {
        onStep: (msg, progress) => {
          updateJob(sessionId, msg, progress, ver);
        },
      });

      if (finalSession) {
        clearJob(sessionId);
        const sanitizedSession = {
          ...finalSession,
          active_job: null,
          isEditing: false,
          pendingVersion: null,
          all_versions:
            finalSession.all_versions?.map((v) => ({
              ...v,
              is_generating: false,
              isPending: false,
            })) || finalSession.all_versions,
        };
        const hasReadyModel = Boolean(
          sanitizedSession.latest_stl ||
            sanitizedSession.all_versions?.some((v) => v.has_stl)
        );
        if (hasReadyModel) {
          playModelReadySound();
        }
        const isCurrentlyViewed = activeSessionRef.current?.id === sessionId;
        setSessions((prev) =>
          prev.map((s) =>
            s.id === sessionId
              ? { ...sanitizedSession, unread: !isCurrentlyViewed }
              : s
          )
        );
        setActiveSession((current) =>
          current?.id === sessionId ? sanitizedSession : current
        );
        if (isCurrentlyViewed) setSelectedVersion(ver);
      } else {
        throw new Error('Re-render failed to return session data');
      }
    } catch (e) {
      console.error(e);
      alert(`Failed to re-render: ${e.message}`);
    } finally {
      clearJob(sessionId);
      setIsSessionLoading(false);
    }
  };

  const isInputDisabled = computeIsInputDisabled(activeSession, generatingJobs);

  return (
    <div className="app-container">
      <Sidebar
        sidebarWidth={sidebarWidth}
        sessions={sessions}
        totalSessionCount={totalSessionCount}
        isLoadingMore={isLoadingMore}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        activeSession={activeSession}
        generatingJobs={generatingJobs}
        onNewIdea={() => setActiveSession(null)}
        onOpenStatusModal={openStatusModal}
        onSelectSession={loadSession}
        onLoadMore={handleLoadMore}
      />

      <div className="pane-divider" onMouseDown={handleSidebarResize}></div>

      <div className="main-area" ref={mainAreaRef}>
        <ChatPanel
          chatPercent={chatPercent}
          isSessionLoading={isSessionLoading}
          activeSession={activeSession}
          chatBottomRef={chatBottomRef}
          enableSupports={enableSupports}
          onToggleSupports={setEnableSupports}
          inputText={inputText}
          onInputChange={setInputText}
          onSend={handleSend}
          isInputDisabled={isInputDisabled}
        />

        <div className="pane-divider" onMouseDown={handleChatResize}></div>

        <PreviewPanel
          apiBase={API_BASE}
          activeSession={activeSession}
          selectedVersion={selectedVersion}
          onSelectVersion={setSelectedVersion}
          generatingJobs={generatingJobs}
          onPrint={handlePrint}
          onRerender={handleRerender}
          onOpenFolder={handleOpenFolder}
          onDelete={handleDelete}
        />
      </div>

      <PrinterStatusModal
        isOpen={showStatusModal}
        onClose={() => setShowStatusModal(false)}
        isFetchingStatus={isFetchingStatus}
        printerStatus={printerStatus}
        onRefreshStatus={fetchPrinterStatus}
        onStopPrint={handleStopPrint}
        onFetchSnapshot={fetchSnapshot}
        isFetchingSnapshot={isFetchingSnapshot}
        snapshotUrl={snapshotUrl}
      />

      <PrintModal
        isOpen={showPrintModal}
        onClose={() => setShowPrintModal(false)}
        isPrinterConnected={isPrinterConnected}
        printerRawState={printerRawState}
        requiredGrams={getRequiredGrams()}
        amsColors={amsColors}
        selectedAmsSlot={selectedAmsSlot}
        onSelectAmsSlot={setSelectedAmsSlot}
        printSupports={printSupports}
        onChangePrintSupports={setPrintSupports}
        onConfirmPrint={handleConfirmPrint}
        isPrinting={isPrinting}
      />
    </div>
  );
}

export default App;
