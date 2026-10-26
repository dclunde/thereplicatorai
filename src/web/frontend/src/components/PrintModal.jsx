import { isPrinterStateBusy } from '../sessionUtils';

export default function PrintModal({
  isOpen,
  onClose,
  isPrinterConnected,
  printerRawState,
  requiredGrams,
  amsColors,
  selectedAmsSlot,
  onSelectAmsSlot,
  printSupports,
  onChangePrintSupports,
  onConfirmPrint,
  isPrinting,
}) {
  if (!isOpen) return null;

  const isPrinterBusy = isPrinterStateBusy(printerRawState);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Print Settings</h2>
          <button className="close-btn" onClick={onClose} aria-label="Close">
            ✖️
          </button>
        </div>
        <div className="modal-body">
          {!isPrinterConnected && (
            <div
              style={{
                marginBottom: '1rem',
                padding: '0.75rem',
                backgroundColor: 'rgba(239, 68, 68, 0.15)',
                borderRadius: '4px',
                border: '1px solid #ef4444',
                color: '#fca5a5',
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
              }}
            >
              <span>⚠️</span>
              <span>
                <strong>Printer is offline / disconnected.</strong> Please power on your
                printer and verify network connectivity before printing.
              </span>
            </div>
          )}

          {isPrinterBusy && (
            <div
              className="printer-Busy-banner"
              style={{
                background: '#3a2a1d',
                border: '1px solid #ff9800',
                color: '#ffcc80',
                padding: '10px 12px',
                borderRadius: '6px',
                marginBottom: '12px',
                fontSize: '0.85rem',
              }}
            >
              ⚠️ <strong>Printer Busy ({printerRawState}):</strong> Wait for the current
              print to finish before starting a new job.
            </div>
          )}

          <div
            style={{
              marginBottom: '1rem',
              padding: '0.75rem',
              backgroundColor: 'rgba(59, 130, 246, 0.1)',
              borderRadius: '4px',
              border: '1px solid rgba(59, 130, 246, 0.2)',
            }}
          >
            <h3 style={{ margin: '0 0 0.25rem 0', fontSize: '1rem' }}>
              Model requires: ~{requiredGrams}g of filament
            </h3>
            <p style={{ margin: 0, color: '#e2e8f0', fontSize: '0.9rem' }}>
              Select the AMS slot or External Spool you want to use for this print.
            </p>
          </div>

          <div
            className="ams-color-picker"
            style={{
              marginBottom: '2rem',
              display: 'flex',
              gap: '1rem',
              alignItems: 'flex-start',
              flexWrap: 'wrap',
            }}
          >
            {/* Auto selection */}
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                gap: '0.25rem',
              }}
            >
              <div
                className={`color-swatch auto-swatch ${
                  selectedAmsSlot === null ? 'selected' : ''
                }`}
                onClick={() => onSelectAmsSlot(null)}
                title="Auto (Let AI decide from AMS slots)"
                style={{ width: '50px', height: '50px', fontSize: '0.8rem' }}
              >
                Auto
              </div>
              <div style={{ fontSize: '0.75rem', opacity: 0 }}>Auto</div>
            </div>

            {/* Render AMS slots 0-3 if available */}
            {amsColors &&
            Object.keys(amsColors).some((k) => parseInt(k) >= 0 && parseInt(k) <= 3) ? (
              Object.entries(amsColors)
                .filter(([slotStr]) => parseInt(slotStr) !== 4)
                .map(([slotStr, data]) => {
                  const slot = parseInt(slotStr);
                  const hex = data.color.startsWith('#')
                    ? data.color
                    : data.color === 'Unknown'
                    ? '#94a3b8'
                    : `#${data.color}`;
                  const isAllowed = data.type === 'PLA' || data.type === 'Unknown';
                  const rawRemain = data.remain ?? -1;
                  const isUnknownWeight = rawRemain < 0;
                  const availableGrams = isUnknownWeight
                    ? -1
                    : Math.round(rawRemain * 10);
                  const hasEnough = isUnknownWeight
                    ? true
                    : availableGrams >= requiredGrams;

                  return (
                    <div
                      key={slot}
                      style={{
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'center',
                        gap: '0.25rem',
                      }}
                    >
                      <div
                        className={`color-swatch ${
                          selectedAmsSlot === slot ? 'selected' : ''
                        }`}
                        style={{
                          backgroundColor: hex,
                          width: '50px',
                          height: '50px',
                          fontSize: '1.2rem',
                          opacity: isAllowed ? 1 : 0.2,
                          cursor: isAllowed ? 'pointer' : 'not-allowed',
                          border: !hasEnough && isAllowed ? '2px solid #ef4444' : '',
                        }}
                        onClick={() => isAllowed && onSelectAmsSlot(slot)}
                        title={`Slot ${slot} - ${data.type} (~${
                          isUnknownWeight ? '?' : data.remain
                        }% / ${
                          isUnknownWeight ? '?' : availableGrams
                        }g remaining)${!isAllowed ? ' (UNSUPPORTED)' : ''}${
                          !hasEnough && isAllowed ? ' (INSUFFICIENT FILAMENT)' : ''
                        }`}
                      >
                        {slot}
                      </div>
                      {isAllowed && (
                        <div
                          style={{
                            fontSize: '0.75rem',
                            color: hasEnough ? '#94a3b8' : '#ef4444',
                            fontWeight: hasEnough ? 'normal' : 'bold',
                          }}
                        >
                          {isUnknownWeight ? '?g' : `${availableGrams}g`}
                        </div>
                      )}
                    </div>
                  );
                })
            ) : (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  alignSelf: 'center',
                  color: '#94a3b8',
                  fontSize: '0.85rem',
                }}
              >
                (AMS Unknown / Not Attached)
              </div>
            )}

            {/* External Spool Slot 4 - always available */}
            {(() => {
              const extData = amsColors && amsColors[4] ? amsColors[4] : null;
              const hex = extData?.color
                ? extData.color.startsWith('#')
                  ? extData.color
                  : extData.color === 'Unknown'
                  ? '#94a3b8'
                  : `#${extData.color}`
                : '#64748b';
              const isAllowed =
                !extData || extData.type === 'PLA' || extData.type === 'Unknown';
              const rawRemain = extData?.remain ?? -1;
              const isUnknownWeight = rawRemain < 0;
              const availableGrams = isUnknownWeight ? -1 : Math.round(rawRemain * 10);
              const hasEnough = isUnknownWeight
                ? true
                : availableGrams >= requiredGrams;

              return (
                <div
                  style={{
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    gap: '0.25rem',
                  }}
                >
                  <div
                    className={`color-swatch ${
                      selectedAmsSlot === 4 ? 'selected' : ''
                    }`}
                    style={{
                      backgroundColor: hex,
                      width: '50px',
                      height: '50px',
                      fontSize: '0.9rem',
                      opacity: isAllowed ? 1 : 0.2,
                      cursor: isAllowed ? 'pointer' : 'not-allowed',
                      border: !hasEnough && isAllowed ? '2px solid #ef4444' : '',
                    }}
                    onClick={() => isAllowed && onSelectAmsSlot(4)}
                    title={`External Spool (Slot 4) - ${extData?.type || 'PLA/Unknown'}${
                      extData
                        ? ` (~${isUnknownWeight ? '?' : extData.remain}% / ${
                            isUnknownWeight ? '?' : availableGrams
                          }g remaining)`
                        : ''
                    }${!isAllowed ? ' (UNSUPPORTED)' : ''}${
                      !hasEnough && isAllowed ? ' (INSUFFICIENT FILAMENT)' : ''
                    }`}
                  >
                    Ext
                  </div>
                  <div
                    style={{
                      fontSize: '0.75rem',
                      color: hasEnough ? '#94a3b8' : '#ef4444',
                      fontWeight: hasEnough ? 'normal' : 'bold',
                    }}
                  >
                    {isUnknownWeight ? 'External' : `${availableGrams}g`}
                  </div>
                </div>
              );
            })()}
          </div>

          <div
            style={{
              marginBottom: '1.5rem',
              padding: '0.75rem',
              backgroundColor: 'rgba(15, 23, 42, 0.6)',
              borderRadius: '4px',
              border: '1px solid #334155',
            }}
          >
            <h4 style={{ margin: '0 0 0.5rem 0', fontSize: '0.95rem', color: '#f8fafc' }}>
              Supports
            </h4>
            <div style={{ display: 'flex', gap: '0.75rem' }}>
              <button
                type="button"
                onClick={() => onChangePrintSupports(false)}
                style={{
                  padding: '0.4rem 1rem',
                  borderRadius: '4px',
                  border: '1px solid #64748b',
                  background: !printSupports ? '#3b82f6' : '#1e293b',
                  color: 'white',
                  fontWeight: !printSupports ? 'bold' : 'normal',
                  cursor: 'pointer',
                }}
              >
                Off
              </button>
              <button
                type="button"
                onClick={() => onChangePrintSupports(true)}
                style={{
                  padding: '0.4rem 1rem',
                  borderRadius: '4px',
                  border: '1px solid #64748b',
                  background: printSupports ? '#3b82f6' : '#1e293b',
                  color: 'white',
                  fontWeight: printSupports ? 'bold' : 'normal',
                  cursor: 'pointer',
                }}
              >
                🛠️ On
              </button>
            </div>
          </div>

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '1rem' }}>
            <button className="secondary-btn" onClick={onClose}>
              Cancel
            </button>
            <button
              className="primary-btn"
              onClick={onConfirmPrint}
              disabled={isPrinting || !isPrinterConnected || isPrinterBusy}
              title={
                !isPrinterConnected
                  ? 'Printer is offline'
                  : isPrinterBusy
                  ? `Printer is busy (${printerRawState})`
                  : ''
              }
            >
              {isPrinting
                ? 'Sending...'
                : !isPrinterConnected
                ? 'Printer Offline'
                : 'Confirm Print'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
