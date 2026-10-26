import { useState, useRef, useCallback } from 'react';
import axios from 'axios';
import {
  INITIAL_PAGE_SIZE,
  LOAD_MORE_PAGE_SIZE,
  mergeSessions,
} from '../sessionUtils';

export function useSessions(apiBase = 'http://localhost:8000') {
  const [sessions, setSessions] = useState([]);
  const [totalSessionCount, setTotalSessionCount] = useState(0);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  const sessionsRef = useRef(sessions);
  sessionsRef.current = sessions;
  const searchQueryRef = useRef(searchQuery);
  searchQueryRef.current = searchQuery;

  const fetchSessions = useCallback(
    async (query = '', options = {}) => {
      const {
        append = false,
        limit = append ? LOAD_MORE_PAGE_SIZE : INITIAL_PAGE_SIZE,
        offset = append ? sessionsRef.current.length : 0,
      } = options;

      if (append) {
        setIsLoadingMore(true);
      }

      try {
        const params = new URLSearchParams();
        if (query) params.append('q', query);
        params.append('limit', String(limit));
        params.append('offset', String(offset));

        const res = await axios.get(`${apiBase}/sessions?${params.toString()}`);
        const countHeader = res.headers ? res.headers['x-total-count'] : null;
        if (countHeader !== undefined && countHeader !== null) {
          setTotalSessionCount(parseInt(countHeader, 10) || 0);
        } else {
          const fetchedCount = Array.isArray(res.data) ? res.data.length : 0;
          setTotalSessionCount(
            append ? sessionsRef.current.length + fetchedCount : fetchedCount
          );
        }

        if (append) {
          setSessions((prev) => mergeSessions(prev, res.data));
        } else {
          setSessions(Array.isArray(res.data) ? res.data : []);
        }
      } catch (e) {
        console.error('Failed to fetch sessions', e);
      } finally {
        if (append) {
          setIsLoadingMore(false);
        }
      }
    },
    [apiBase]
  );

  const handleLoadMore = useCallback(() => {
    if (isLoadingMore) return;
    fetchSessions(searchQueryRef.current, {
      append: true,
      limit: LOAD_MORE_PAGE_SIZE,
      offset: sessionsRef.current.length,
    });
  }, [isLoadingMore, fetchSessions]);

  return {
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
  };
}
