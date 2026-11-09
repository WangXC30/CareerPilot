import type { Data } from "./api";

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("careerpilot-unsaved", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("edits");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}
export async function cachedDraft(key: string): Promise<Data | undefined> {
  const db = await open();
  try {
    return await new Promise((resolve, reject) => {
      const request = db.transaction("edits").objectStore("edits").get(key);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  } finally {
    db.close();
  }
}
export async function cacheDraft(key: string, values?: Data) {
  const db = await open();
  try {
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction("edits", "readwrite");
      const store = transaction.objectStore("edits");
      if (values) store.put(values, key);
      else store.delete(key);
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
  } finally {
    db.close();
  }
}
export function clearCachedDrafts(): Promise<void> {
  for (let i = sessionStorage.length - 1; i >= 0; i--) {
    const key = sessionStorage.key(i);
    if (key?.startsWith("careerpilot.chat-draft:") || key?.startsWith("careerpilot.learning-draft:"))
      sessionStorage.removeItem(key);
  }
  return new Promise((resolve, reject) => {
    const request = indexedDB.deleteDatabase("careerpilot-unsaved");
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
    request.onblocked = () =>
      reject(
        new Error("Close other CareerPilot tabs to clear local editing cache"),
      );
  });
}
