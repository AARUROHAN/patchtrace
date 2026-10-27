/** Parse API responses without exposing upstream HTML, CSS or base64 data in UI errors. */
export async function readApiJson<T>(response: Response): Promise<T> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    const description = response.status === 502 || response.status === 503 || response.status === 504
      ? "The hosting gateway could not complete the request"
      : "The server returned an unexpected response";
    throw new Error(`${description} (HTTP ${response.status}). Please retry. If this persists, check the deployment logs.`);
  }

  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload && typeof payload.detail === "string"
      ? payload.detail
      : null;
    const safeDetail = detail && detail.length <= 500 && !/[<>\u0000-\u001f]/.test(detail) ? detail : null;
    throw new Error(safeDetail || `PatchTrace request failed (HTTP ${response.status}). Please retry.`);
  }

  return payload as T;
}
