import { useState, useCallback } from 'react';
import axios from 'axios';
import { ACTIVE_PRINTER_STATES } from '../sessionUtils';

export function usePrinterStatus(apiBase = 'http://localhost:8000') {
  const [printerStatus, setPrinterStatus] = useState(null);
  const [printerRawState, setPrinterRawState] = useState(null);
  const [isPrinterConnected, setIsPrinterConnected] = useState(true);
  const [snapshotUrl, setSnapshotUrl] = useState(null);
  const [isFetchingStatus, setIsFetchingStatus] = useState(false);
  const [isFetchingSnapshot, setIsFetchingSnapshot] = useState(false);
  const [amsColors, setAmsColors] = useState(null);

  const fetchAmsColors = useCallback(async () => {
    try {
      const res = await axios.get(`${apiBase}/ams_colors`);
      setAmsColors(res.data);
    } catch (e) {
      console.error('Failed to fetch AMS colors', e);
    }
  }, [apiBase]);

  const fetchPrinterStatus = useCallback(async () => {
    setIsFetchingStatus(true);
    try {
      const res = await axios.get(`${apiBase}/status`);
      setPrinterStatus(res.data.formatted);
      setPrinterRawState(res.data.state || null);
      setIsPrinterConnected(Boolean(res.data.connected));
    } catch (e) {
      console.error(e);
      setPrinterStatus('Failed to load status.');
      setPrinterRawState(null);
      setIsPrinterConnected(false);
    }
    setIsFetchingStatus(false);
  }, [apiBase]);

  const fetchSnapshot = useCallback(async () => {
    setSnapshotUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    setIsFetchingSnapshot(true);
    try {
      const res = await axios.get(`${apiBase}/snapshot`, { responseType: 'blob' });
      const url = URL.createObjectURL(res.data);
      setSnapshotUrl(url);
    } catch (e) {
      console.error(e);
      alert('Failed to fetch snapshot.');
    } finally {
      setIsFetchingSnapshot(false);
    }
  }, [apiBase]);

  const handleStopPrint = useCallback(async () => {
    if (printerRawState && !ACTIVE_PRINTER_STATES.includes(printerRawState)) {
      alert(
        `Printer is currently ${printerRawState} (not printing). No active print job to stop.`
      );
      return;
    }
    if (!window.confirm('Are you sure you want to stop the active print job?')) return;
    try {
      const res = await axios.post(`${apiBase}/stop_print`);
      alert(res.data.message || 'Print stopped.');
      let attempts = 0;
      const poll = setInterval(async () => {
        await fetchPrinterStatus();
        attempts++;
        if (attempts >= 3) clearInterval(poll);
      }, 2000);
    } catch (err) {
      console.error(err);
      alert(`Failed to stop print: ${err.response?.data?.detail || err.message}`);
    }
  }, [apiBase, printerRawState, fetchPrinterStatus]);

  return {
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
  };
}
