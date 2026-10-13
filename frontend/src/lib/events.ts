const DATA_CHANGED = "msme:data-changed";
const channel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("msme-receivables-data") : null;

/** Tell every mounted view, in this tab and other open tabs, that receivables data changed. */
export const notifyDataChanged = () => {
  window.dispatchEvent(new Event(DATA_CHANGED));
  channel?.postMessage(DATA_CHANGED);
};

export const onDataChanged = (listener: () => void): (() => void) => {
  const onMessage = () => listener();
  window.addEventListener(DATA_CHANGED, listener);
  channel?.addEventListener("message", onMessage);
  return () => {
    window.removeEventListener(DATA_CHANGED, listener);
    channel?.removeEventListener("message", onMessage);
  };
};
