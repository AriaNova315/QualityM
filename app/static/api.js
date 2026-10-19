export class ApiError extends Error {
  constructor(message, { status = 0, code = "NETWORK_ERROR", diagnostics = [], cause = null } = {}) {
    super(message, { cause });
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.diagnostics = diagnostics;
    this.isConflict = status === 409;
  }
}

function errorDetail(payload, fallback) {
  const detail = payload?.detail ?? payload;
  if (typeof detail === "string") return { message: detail, code: "REQUEST_FAILED", diagnostics: [] };
  return {
    message: detail?.message || fallback,
    code: detail?.code || "REQUEST_FAILED",
    diagnostics: detail?.diagnostics || payload?.diagnostics || [],
  };
}

export async function apiRequest(path, options = {}) {
  const { timeout = 30000, headers = {}, body, ...rest } = options;
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeout);
  const requestHeaders = { Accept: "application/json", ...headers };
  if (body && !(body instanceof FormData) && !requestHeaders["Content-Type"]) {
    requestHeaders["Content-Type"] = "application/json";
  }
  try {
    const response = await fetch(path, { ...rest, body, headers: requestHeaders, signal: controller.signal });
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json") ? await response.json() : await response.blob();
    if (!response.ok) {
      const detail = errorDetail(payload, `请求失败（HTTP ${response.status}）`);
      throw new ApiError(detail.message, { status: response.status, code: detail.code, diagnostics: detail.diagnostics });
    }
    return payload;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error?.name === "AbortError") {
      throw new ApiError("请求超时，请检查服务状态后重试", { code: "REQUEST_TIMEOUT", cause: error });
    }
    throw new ApiError("网络连接失败，请确认 QualityM 服务正在运行", { cause: error });
  } finally {
    window.clearTimeout(timer);
  }
}

export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
